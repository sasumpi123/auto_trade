# P31 P22 수동 연구 단일 명령

이슈 #65. 이미 검토된 세 도구를 정해진 순서로 한 번씩 연결하는 실행 편의 계층입니다. 대상은 P22 격리 수집, 격리 고정 평가, P30 사후 보조 감사입니다.
새 전략, 새 지표, 새 관측기는 없습니다. 이 명령이 Claude·모델·Slack·launchd·실거래를 자동으로 호출하지도 않습니다.

## 실행 (저장소 루트에서, 사용자가 직접 한 번)

<!-- P31_COMMAND_BEGIN -->
```bash
RUN_ROOT="$HOME/research/p22-run-$(date +%Y%m%d-%H%M%S)"
if test -z "$(git status --porcelain)"; then PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m ops.research_run p22 --directory "$RUN_ROOT" --connect-public; else echo "STOP: local changes in checkout"; false; fi
```
<!-- P31_COMMAND_END -->

- 변수 대입은 실행 줄과 분리되어 있습니다.
- 로컬 변경이 있으면 STOP을 출력하고 nonzero로 끝납니다.
- 병합 후 검토한 소스인지는 PR의 인계 명령이 blob SHA로 확인합니다. squash 병합에서는 PR head가 master의 조상이라고 가정하지 않습니다.
- `--connect-public`이 없으면 `OFFLINE_PLAN_ONLY`로 끝납니다(exit 0). 계획과 상한만 출력하며 네트워크, 디렉터리 생성, 하위 작업이 모두 없습니다.
- `2026-11-06T00:00:00Z`(09:00 KST) 이전에는 `NOT_READY`로 끝납니다(exit 2). 경로 접근, 출력, 네트워크, 하위 작업이 없고 대기하지도 않습니다.
- 시간 주입, 합성 허용, 재시도, 명령·모듈·기간·전략·비용을 지정하는 옵션은 없습니다. 대상도 `p22` 하나만 받습니다.

## 고정 순서

모든 하위 작업은 고정된 인자 배열로 실행합니다. shell 보간은 없습니다.

0. **사전 확인(읽기 전용)**
   - `tools.check_safety.check_repository`를 실행합니다.
   - 격리 실행 준비를 확인합니다:
     - drift가 등록된 `ops/paper_experiment.py`뿐인지
     - pin 복사본 SHA
     - runner와 registration SHA
   - 하나라도 실패하면 BLOCKED(exit 2)로 끝나고 출력을 만들지 않습니다.
1. **출력 root 준비**
   - `ROOT`는 기존 `base.fresh`로 만듭니다. 새 경로여야 하며, checkout 밖이어야 하고, symlink·계좌·holdout 경로는 거부합니다. INCOMPLETE 표지를 둡니다.
   - umask는 077이고 root 디렉터리 권한은 0700입니다.
   - 기존 root나 부분 결과를 재개하거나 덮어쓰지 않습니다.
2. **수집 1회:** `python tools/p22_isolated.py collect --connect-public --output ROOT/collection`
3. **분석 1회:** `python tools/p22_isolated.py analyze --input ROOT/collection --output ROOT/analysis`
   - 수집이 실패해도 `collection.json`이 있으면 분석을 1회 실행합니다. 완료된 자료의 한계를 기록하기 위해서입니다. 수집 실패 코드는 숨기지 않습니다.
   - `collection.json`이 없으면 `SKIPPED_WITH_REASON(P31_NO_COLLECTION_TO_ANALYZE)`로 건너뜁니다.
4. **보조 감사 1회:** `python -m backtesting.forward_result_audit --p22-root ROOT/collection --results ROOT/analysis/results.json --output ROOT/supplement`
   - 분석이 exit 0이고 `VERIFIED`/`VERIFIED_PARTIAL`일 때만 실행합니다.
   - 그 밖에는 `SKIPPED_WITH_REASON(P31_ANALYSIS_NOT_VERIFIED)`로 건너뜁니다.
   - 원래 collect/analyze를 작업 트리에서 직접 부르지 않습니다. 그래서 frozen 실패를 우회하지 않습니다.

### 하위 작업 실행 규칙

- 하위 작업은 하나씩 자체 process group으로 실행합니다.
- 바깥 제한 시간은 다음과 같습니다. 각 도구의 기존 상한(수집 일 40회·120초/전체 120회, 분석 600초와 정리, 보조 60초)은 그대로 유지됩니다.

  | 단계 | 바깥 제한 |
  |---|---|
  | 수집 | 420초 |
  | 분석 | 660초 |
  | 보조 감사 | 90초 |
  | 합계 상한 | 1170초 |

- 단계 사이에 전체 경과 시간을 확인합니다. 초과하면 나머지 단계를 `P31_TOTAL_BUDGET_EXCEEDED`로 건너뜁니다.
- timeout이나 Ctrl-C가 오면 **직접 시작한 그 process group만** 정리합니다. 무관한 프로세스는 건드리지 않고, 완료된 단계의 결과는 보존합니다.
  1. group에 SIGTERM을 보냅니다.
  2. 최대 10초 동안 leader가 끝나기를 기다립니다. 이때 leader를 reap하지 않으므로 PGID가 재사용되지 않습니다. leader 종료는 leader만 물려받은 종료 감지 pipe의 EOF로 확인합니다(`select`). Linux 전용 `os.waitid`를 쓰지 않으므로 macOS Python 3.12에서도 동작합니다(리뷰 5794390314 반영).
  3. leader가 끝났는지와 상관없이 **항상** 같은 group에 SIGKILL을 보냅니다. SIGTERM을 무시하거나 leader보다 오래 남는 하위 프로세스까지 정리하기 위해서입니다(리뷰 5794135817 반영).
  4. 그 뒤 leader를 reap합니다(최대 5초).
  - 정리 중 두 번째 Ctrl-C는 이 bounded 정리가 끝날 때까지 무시합니다.
  - run.json의 단계 `cleanup`에 `sigterm_sent`, `sigkill_sent`, `leader_exited_after_term`, `leader_exit`를 기록합니다.
- 단계 내부의 중단은 각 도구의 기존 정리 동작에 맡깁니다.
- 자동 재시도, 재수집, 새 날짜, 시간 연장, 새 root 자동 생성, 데몬, 감시, polling 루프는 없습니다.

## 실행 기록과 출력

**`ROOT/run.json`**
- 연구 실행 기록입니다(`RESEARCH_RUN_RECORD_NOT_PAPER_LEDGER_OR_APPROVAL`). PAPER 원장, 승인, 복구 증거가 아니며 DB나 queue도 없습니다.
- 단계별로 다음을 기록합니다:
  - argv, 상태(`EXITED`/`TIMEOUT`/`INTERRUPTED`/`SKIPPED_WITH_REASON`), 종료코드, 경과 시간
  - 알려진 schema의 결과 상태, 결과 파일 SHA, 요청 수, 짝·제외·미완료 수
- 사용한 6개 소스 파일의 SHA도 기록합니다.

**로그**
- 하위 작업의 stdout/stderr는 `ROOT/logs/<step>.log`에 저장합니다. 최대 4 MiB이고, 넘치면 마지막 부분만 남깁니다.
- 로그 내용은 공개 출력에 내보내지 않습니다.
- 원본 collection/analysis/supplement 파일은 수정하지 않습니다.

**결과 상태와 종료코드**

| 상태 | 의미 | exit |
|---|---|---|
| `COMPLETED` | 모든 단계 완전 성공 | 0 |
| `COMPLETED_PARTIAL_DATA` | 단계는 모두 성공했지만 격리 제외가 있음. 전체 성공으로 부르지 않음 | 0 |
| `FAILED` | 실패한 단계가 있음 | 2 |
| `TIMEOUT` | 제한 시간 초과 | 2 |
| `BUDGET_EXCEEDED` | 전체 예산 초과 | 2 |
| `INTERRUPTED` | 사용자 중단 | 2 |
| `NOT_READY` | 수집 가능 시각 이전 | 2 |
| `BLOCKED` | 사전 확인 실패 | 2 |

필수 단계가 하나라도 실패하면 결과는 exit 0이 되지 않습니다.

**표준출력(최대 8줄)**
- 1행: 총 상태와 수집·평가·보조 단계별 상태, 짝·제외·미완료 수, 요청 수
- 최대 6행: 짝별 비교. 보조 감사가 있으면 원래 종가 평가와 가상 청산 비교(L/44/BH)를 보여 줍니다. 보조 감사가 없으면 원래 net% 비교를 보여 줍니다.
- 마지막 행: 사후 보조 지표와 표본의 한계
- JSON은 bounded로 읽고, 알려진 상태값과 고정 reason만 출력합니다. 경로와 예외 원문은 출력하지 않습니다.

## 한계

- 실제 수집과 실자료 평가는 구현 중에 실행하지 않았습니다. 테스트는 다음 두 가지로만 이루어집니다:
  - 고정 P22가 만든 합성 fixture
  - 가짜 executor(`run()` 매개변수로만 주입하며 CLI 옵션이 아님)
- 합성 성공을 실자료 성공으로 해석하지 않습니다.
- 원래 사전 등록된 P22 판정이 우선입니다. P30 보조값으로 소급해서 성공 처리하지 않습니다.
- `REAL_TRADING=False`이며 추가 지출은 0원입니다.
