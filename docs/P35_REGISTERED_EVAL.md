# P35 공통 사전등록 러너: protocol 파일 + 고정 커밋

이슈 #75. ROADMAP Phase 0의 "공통 사전등록 러너"와 "커밋 고정 실행" 항목입니다.

**목표:** 새 평가마다 러너와 파일별 해시 목록(P33은 24개)을 새로 만들지 않고, **protocol 파일 하나와 `code_commit`** 만으로 등록·실행합니다.

**범위:**
- 새 엔진이나 새 전략은 만들지 않았습니다.
- P22/P33의 protocol·등록·anchor·고정 소스·러너는 수정하지 않았습니다.
- 실자료 수집·평가는 실행하지 않았습니다.
- P22/P33을 이 러너로 옮기지도 않았습니다.

## 명령

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.p35_registered_eval status --protocol REPO_RELATIVE_PROTOCOL_JSON
```

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.p35_registered_eval analyze --protocol REPO_RELATIVE_PROTOCOL_JSON --input SNAPSHOT_DIR --output NEW_PRIVATE_DIR
```

- **`status`**
  - 네트워크 호출과 파일 생성이 없습니다. 읽기 전용 git 조회만 합니다.
  - 출력은 3줄입니다: 상태, protocol SHA-256, `code_commit`과 기한.
  - exit 0: `NOT_REGISTERED`, `NOT_READY`, `READY`.
  - exit 2: 그 밖의 상태(`MISSED_REGISTRATION`, 차단 코드).
- **`analyze`**
  - 등록·기한·입력 manifest를 확인합니다.
  - 그다음 `code_commit`을 임시 `git worktree`로 체크아웃하고, 그 안에서 격리 인터프리터(`python -I`, `PYTHON*` 환경변수 제거, `PYTHON_DOTENV_DISABLED=1`)로 평가합니다.
  - 출력은 최대 8줄입니다.
  - 현재 저장소에는 등록된 P35 protocol이 없습니다. 첫 실제 평가는 별도 이슈에서 protocol 제안 → 리뷰 → 등록 PR 순서로 진행합니다.

## protocol 스키마 (`P35_EVAL_PROTOCOL_V1`)

키는 정확히 아래 집합이어야 합니다. 빠지거나 추가된 키가 있으면 `P35_PROTOCOL_INVALID`입니다.

| 키 | 내용 |
|---|---|
| `id`, `question` | 평가 id(디렉터리 안전 문자), 작업 질문 |
| `strategy` | `{"id":"consensus","label":"consensus_2of3","params":{"quorum":2}}`. 등록부에 있는 기존 전략만 허용합니다. 현재는 `ConsensusStrategy(2 or 3)`뿐이고, label은 클래스 이름과 같아야 합니다. |
| `folds` | `[{id,start,end_exclusive}]`. 정렬돼 있고 겹치지 않아야 하며, 최대 64개입니다. |
| `markets`, `unit_minutes` | 종목 목록, 봉 주기 1/15/60 |
| `costs_bps`, `initial_cash`, `position_fraction`, `warmup_candles` | 비용, 시작금, sizing, warmup |
| `baselines` | `["cash_100","buy_hold_40"]` |
| `comparison` | 주 비교는 종가 평가 수익률, 보조 비교는 P28 가상 청산입니다. 동률(TIE)과 NO_TRADES는 승리가 아니며, 합산하지 않습니다(고정 문구). |
| `decision_rule` | `both_exceed_fraction_v1`: `min_valid`, `min_trade_cases`, `min_both_exceed_markets`(모두 1 이상), `both_exceed_fraction`(0 초과 Decimal 문자열), `scope` |
| `registration_deadline_exclusive`, `analysis_not_before_utc` | 등록 기한과 분석 허용 시각(기한 ≤ 허용 시각). 전방 모드는 모든 fold 종료 ≤ 허용 시각 |
| `max_cases`, `analysis_seconds` | 값은 fold 수 × 종목 수와 같아야 합니다. 분석 시간은 600초 이하입니다. |
| `code_commit` | 평가 코드를 고정하는 40자리 git 커밋 SHA |
| `input` | 입력 결속 방식(아래 "입력 결속"). `{"mode":"fixed_snapshot","manifest_sha256":...}` 또는 `{"mode":"collected_after_registration"}` |
| `missing_candles`, `adopt_strategy` | 누락 시 격리(PARTIAL)하는 고정 문구, `false` |

**검토된 경로가 실제로 구현하는 조건만 등록할 수 있습니다.** 다른 값은 조용히 무시하지 않고 차단합니다.
- 1분봉: P32 `strategy_case`가 `bar_seconds=60`으로 고정돼 있습니다.
- 비용 5/5/2bps, 100만 원, 40%, warmup 200
- 15·60분봉: 스키마에는 있지만 지금은 `P35_UNIT_NOT_SUPPORTED_BY_REVIEWED_PATH`로 차단합니다. 지원하려면 검토된 15·60분 경로를 별도 이슈로 추가해야 합니다(새 엔진 금지).
- 다른 비용·시작금: `P35_CONDITION_NOT_SUPPORTED_BY_REVIEWED_PATH`

## 입력 결속 (리뷰 5933603188 반영)

전방 평가는 기한 전에 등록하고, fold가 끝난 뒤에 자료를 수집합니다. 그래서 등록 시점에는 입력 스냅샷 SHA가 존재할 수 없습니다. 이전 Head는 이 SHA를 등록 시점에 요구했으므로, 정상 절차로는 전방 평가를 등록할 수 없었습니다. 두 방식으로 분리했습니다.

| 모드 | 용도 | 등록 시 고정 | 실행 시 확인 |
|---|---|---|---|
| `fixed_snapshot` | 등록 전에 이미 존재하는 과거 스냅샷(P36 등) | `manifest_sha256`(snapshot.json 바이트 SHA). 모든 fold 종료 ≤ 등록 기한이어야 함(`P35_FIXED_SNAPSHOT_AFTER_DEADLINE`) | SHA 일치(`P35_INPUT_MANIFEST_MISMATCH`), 추가 결속 필드 금지 |
| `collected_after_registration` | 전방 평가(P33형) | 사전 고정 규칙만 고정. 모든 fold 시작 ≥ 등록 기한(`P35_FORWARD_FOLD_BEFORE_DEADLINE`), fold 종료 ≤ 분석 허용 시각(`P35_ANALYSIS_BEFORE_FOLD_END`) | 사후 입력 영수증 `snapshot.json`이 이 protocol SHA와 `registration_id`를 담아야 함(`P35_INPUT_REGISTRATION_MISMATCH`). fold별 수집 시작은 fold 종료 이후여야 함. SHA는 실행 결과에 기록하고 실행 후 다시 확인 |

## 등록 절차 (P33 gate 방식 재사용)

1. **평가 코드 병합:** 평가에 쓸 코드가 master에 병합됩니다. 그 커밋 SHA가 `code_commit`입니다.
2. **protocol 제안 PR:** `<dir>/protocol.json`을 올리고, 검토자가 정확한 SHA-256을 GitHub 리뷰로 승인합니다. 이 커밋이 `protocol_commit`입니다.
3. **등록 PR:** 같은 디렉터리에 두 파일을 추가합니다. 이 PR은 **기한 전에** master에 병합돼야 합니다.
   - 오프라인 승인 영수증 `registration.json`(`P35_REGISTRATION_V1`)
     - `remote_repository`(=`sasumpi123/auto_trade`), `protocol_path`, `protocol_sha256`, `approved: true`
     - `approval_reference`: GitHub 리뷰 URL 형식(`https://github.com/sasumpi123/auto_trade/pull/N#pullrequestreview-ID`)
     - `protocol_commit`과 같은 값의 `observed_remote_head`
     - 기한 전이고 미래가 아닌 `observed_at_utc`
   - `anchor.json`(`P35_REGISTRATION_ANCHOR_V1`): `protocol_sha256`, `receipt_sha256`, 영수증과 같은 `approval_reference`
   - 검토자는 병합 전에 GitHub에서 기한 전 승인 이벤트, 제안 commit, protocol 바이트, 영수증을 확인합니다.
4. **gate:** 아래가 모두 확인될 때만 `REGISTERED`입니다. 증거가 하나라도 없거나 다르면 `P35_APPROVAL_UNVERIFIED` 등으로 차단합니다. `registration_id`는 영수증의 SHA-256입니다.
   - 영수증 필드가 정확한 집합이고 위 값과 일치. anchor가 영수증 SHA와 protocol SHA를 가리킴(`P35_RECEIPT_NOT_ANCHORED`, `P35_PROTOCOL_NOT_ANCHORED`)
   - 전체 이력, `refs/remotes/origin/master` 존재(아니면 `P35_HISTORY_UNVERIFIED`)
   - protocol·영수증·anchor가 추적되고 수정되지 않음
   - `protocol_commit`: 존재하고, 그 시점의 protocol 바이트가 현재 SHA와 같고, anchor가 아직 없고(제안과 등록이 다른 commit), 원격 master와 anchor commit의 조상이며, 시각이 기한 전이고, `code_commit`을 이력에 포함
   - anchor commit이 원격 master에 있고 시각이 기한 전
   - `code_commit`이 존재하고 원격 master에 있으며 anchor보다 앞섬
5. **실행:** `analysis_not_before_utc` 이후 사용자가 수동으로 1회 실행합니다.

기한을 놓치면 `MISSED_REGISTRATION`이며, 날짜를 옮기거나 소급 등록하지 않습니다.

## 성공 판정 (리뷰 5933603188 반영)

성공(`VERIFIED`/`VERIFIED_PARTIAL`)은 다음을 **모두** 만족할 때만입니다.
- 격리 자식 프로세스가 exit 0으로 종료
- 결과가 `P35_ANALYSIS_V1`이고 protocol SHA, `code_commit`, `registration_id`, 입력 SHA에 결속
- 실행 후 등록과 입력을 다시 확인해 변하지 않음

하나라도 어긋나면 `INCOMPLETE` 마커를 남깁니다(`P35_EVALUATION_PROCESS_FAILED`, `P35_TIME_LIMIT`, `P35_RESULT_BINDING_MISMATCH`, `P35_REGISTRATION_CHANGED`, `P35_INPUT_CHANGED`). 최종 상태로 `results.json`, `REPORT.md`, `cases.csv`를 다시 쓰므로 JSON, Markdown, CLI(exit 2)가 같은 실패를 표시합니다.

## 입력 스냅샷 (`P35_INPUT_SNAPSHOT_V1`)

- **구조:** `SNAPSHOT_DIR/snapshot.json`과 fold별 `data/<fold id>/`로 구성됩니다. fold 디렉터리는 P22/P33 일별 형식(`plan.json`, `manifest.json`, `<market>-1.csv`)입니다.
- **`snapshot.json`:** fold마다 id, 기간, plan SHA, manifest SHA를 적습니다.
- **평가 안에서 확인하는 것:**
  - 기존 `minute.load_partitioned_sample`의 실자료 출처, 시각, 품질, 파일 SHA 검사를 그대로 거칩니다.
  - 수집 시작이 fold 종료 이후여야 합니다(`P35_COLLECTED_BEFORE_FOLD_END`).
  - 마지막에 bounded 재해시(P32 `rehash`)를 합니다.
- `snapshot_record()`는 수집기·테스트가 manifest를 만들 때 쓰는 보조 함수입니다.
- 1분봉 CSV 2MB 상한(일 단위 fold)은 기존 loader의 한계입니다. 다년치 자료(P36)를 쓰려면 별도 loader 검토가 필요합니다.

## 평가 내부 (기존 검토 경로만 사용)

P33 `case`/`row`/`tally`와 같은 구성이며, 전략·판정 규칙 값을 protocol에서 읽는 점만 다릅니다.

- P32 `strategy_case`
- P21 `compact`(P9 대사)
- P29 `buy_hold_40_core`(같은 `execution_first_open`)
- P28 `exit_audit`

판정 규칙:
- 반올림 전 Decimal로 비교합니다. 같으면 TIE입니다.
- NO_TRADES와 BH40 NA는 승리가 아닙니다.
- 분모는 명시합니다: 예정 / 유효 / 격리 / 미완료 / 거래 / 무거래.
- `FURTHER_VALIDATION_WARRANTED`는 거래 사례가 1건 이상이고 동시 초과가 `max(1, ceil(Decimal(fraction) × 거래 사례))` 이상일 때만 나옵니다. 무거래나 동시 초과 0건은 긍정 판정이 될 수 없습니다(스키마도 0 임계값을 거부).

## 차단 코드

| 단계 | 코드 |
|---|---|
| 스키마 | `P35_PROTOCOL_INVALID`, `P35_STRATEGY_NOT_REGISTERED`, `P35_STRATEGY_LABEL_MISMATCH`, `P35_FOLDS_INVALID`, `P35_MARKETS_INVALID`, `P35_UNIT_INVALID`, `P35_UNIT_NOT_SUPPORTED_BY_REVIEWED_PATH`, `P35_CONDITION_NOT_SUPPORTED_BY_REVIEWED_PATH`, `P35_CASE_LIMIT_INVALID`, `P35_TIME_LIMIT_INVALID`, `P35_CODE_COMMIT_INVALID`, `P35_INPUT_BINDING_INVALID`, `P35_FIXED_SNAPSHOT_AFTER_DEADLINE`, `P35_FORWARD_FOLD_BEFORE_DEADLINE`, `P35_ANALYSIS_BEFORE_FOLD_END`, `P35_DECISION_RULE_INVALID`, `P35_PROTOCOL_NOT_IN_CHECKOUT` |
| 등록 | `NOT_REGISTERED`, `MISSED_REGISTRATION`, `NOT_READY`, `P35_REGISTRATION_UNVERIFIED`, `P35_APPROVAL_UNVERIFIED`, `P35_APPROVAL_NOT_ON_MASTER`, `P35_RECEIPT_NOT_ANCHORED`, `P35_REGISTRATION_FROM_FUTURE`, `P35_PROTOCOL_NOT_ANCHORED`, `P35_HISTORY_UNVERIFIED`, `P35_REGISTRATION_UNTRACKED`, `P35_REGISTRATION_MODIFIED`, `P35_REGISTRATION_NOT_ON_MASTER`, `P35_MISSED_REGISTRATION`, `P35_CODE_COMMIT_UNKNOWN`, `P35_CODE_COMMIT_NOT_ON_MASTER`, `P35_CODE_COMMIT_AFTER_REGISTRATION` |
| 실행 | `P32_OUTPUT_INSIDE_INPUT`, `OUTPUT_EXISTS_NO_OVERWRITE`, `P35_INPUT_MANIFEST_MISMATCH`, `P35_INPUT_REGISTRATION_MISMATCH`, `P35_EVALUATION_PROCESS_FAILED`, `P35_EVALUATION_NOT_VERIFIED`, `P35_FOLD_MANIFEST_CHANGED`, `P35_PERIOD_MISMATCH`, `P35_COLLECTED_BEFORE_FOLD_END`, `P35_INPUT_CHANGED`, `P35_CASE_LIMIT`, `P35_WORKTREE_UNAVAILABLE`, `P35_WORKTREE_NOT_AT_CODE_COMMIT`, `P35_PROTOCOL_CHANGED`, `P35_CODE_COMMIT_MISMATCH`, `P35_TIME_LIMIT`, `P35_RESULT_MISSING`, `P35_RESULT_BINDING_MISMATCH`, `P35_REGISTRATION_CHANGED` + 기존 loader 코드(`REAL_SOURCE_UNVERIFIED`, `FILE_SHA_MISMATCH` 등) |

## 합성 검증 (`tests/test_p35_registered_eval.py`)

- **등록 전 실행 차단:** 네트워크 0, 출력 디렉터리·worktree 생성 0. 기한이 지나면 `MISSED_REGISTRATION`입니다.
- **등록 우회 차단:** 기한 뒤 anchor, 수정·미추적 anchor, 승인 뒤 바뀐 protocol은 모두 차단됩니다.
- **승인 증거 (리뷰 반영):** 임의 문자열 reference, anchor·영수증 reference 불일치, 미승인, 다른 저장소·protocol SHA, 관측 head 불일치, 존재하지 않는 제안 commit, 제안과 등록이 같은 commit, 필드 누락은 `P35_APPROVAL_UNVERIFIED`입니다. anchor에 없는 영수증은 `P35_RECEIPT_NOT_ANCHORED`입니다. 관측·제안·anchor가 기한 뒤이면 `P35_MISSED_REGISTRATION`, 관측 시각이 미래이면 `P35_REGISTRATION_FROM_FUTURE`입니다. 영수증만 있고 anchor가 없으면 `NOT_REGISTERED`입니다.
- **`code_commit` 검사:**
  - 존재하지 않는 커밋, master에 없는 side 브랜치 커밋, anchor보다 늦은 커밋을 차단합니다.
  - 원격 master ref가 없거나 shallow clone이면 `P35_HISTORY_UNVERIFIED`입니다. 이 경우를 성공이나 MISSED로 처리하지 않습니다.
- **입력 결속 (리뷰 반영):** 전방 창에 `fixed_snapshot`을 쓰거나, 전방 fold가 기한 전에 시작하거나, 분석 허용 시각이 fold 종료 전이면 스키마에서 차단합니다. 다른 등록의 사후 영수증은 worktree 생성 전에 `P35_INPUT_REGISTRATION_MISMATCH`로 차단합니다.
- **입력 검사:** 과거 모드 SHA 불일치, fold manifest 변경, CSV 변경, 출력이 입력 안에 있는 경우를 차단합니다.
- **성공 판정 (리뷰 반영):** 결속이 맞는 `VERIFIED` JSON을 남기고 자식이 7로 끝나면 `INCOMPLETE`(`P35_EVALUATION_PROCESS_FAILED`)이고, 마커가 남으며, `REPORT.md`와 CLI(exit 2)도 같은 실패를 표시합니다. 시간 초과, 결과 결속·스키마·입력 SHA 불일치, 실행 중 입력 변경도 모두 `INCOMPLETE`입니다. exit 0과 모든 최종 검사가 맞을 때만 `VERIFIED`입니다.
- **무거래 판정 (리뷰 반영):** 0 임계값은 스키마에서 거부합니다. 모든 임계값을 0으로 강제해도 무거래·빈 표본·동시 초과 0건은 `FURTHER_VALIDATION_WARRANTED`가 되지 않습니다.
- **TIE·NO_TRADES·BH40 NA·PARTIAL 분모:** P33 규칙 값에서 거래 사례 0~14와 승리 수·종목 수 조합 전부에서 P33 `tally`와 판정·집계가 같습니다.
- **P33 재현 (전방 순서):**
  - P35 스키마 사본(fold=day, `collected_after_registration`)을 기한 전에 등록한 뒤, 창이 끝난 후 수집한 합성 자료에 등록 id를 결속한 영수증을 붙여 평가합니다. 같은 자료를 P33 러너(`p33.work`)로도 평가합니다.
  - 사례별 Decimal 문자열, window, BH40, exit audit, 주·보조 비교와 집계·판정이 **완전히 같습니다**(VERIFIED_PARTIAL, 2 사례 + 12 격리, `INSUFFICIENT_SAMPLE_HOLD`).
- **실제 outer 경로:**
  - 임시 전체 이력 저장소에서 코드 커밋 → 제안 커밋 → 등록 커밋(영수증+anchor) → `origin/master` 순서로 만듭니다. fold가 끝난 뒤 수집하고, 등록 id를 결속한 영수증으로 `analyze`를 실행합니다.
  - 평가가 `code_commit` worktree 안에서 실행되는 것을 확인합니다(`runner.source_root`).
  - 작업 트리에서 `p32`를 `SystemExit(99)`로 바꿔도 평가에 영향이 없습니다.
  - worktree는 정리됩니다.
  - 실자료 출처 검사는 꺼지지 않으므로, 합성 입력은 고정 코드 안에서 `REAL_SOURCE_UNVERIFIED`로 거절됩니다.
- **시간 상한:** 상한을 넘으면 프로세스 그룹 전체를 종료합니다.
- **기존 등록 보존:** P22 drift(`ops/paper_experiment.py`만)와 P33 protocol·고정 해시는 그대로입니다.

## 한계

- **합성 자료로만 검증했습니다.** 실자료 평가와 실제 등록은 하지 않았습니다.
- **현재 지원 범위:** 지금 등록할 수 있는 조건은 1분봉, 5/5/2bps, Consensus(2/3)입니다.
  - 15·60분봉, 다른 전략(RSI/Bollinger/Legacy 등), 다년치 loader는 검토된 경로를 별도로 추가해야 합니다.
- **원격 master 확인:** 로컬 `refs/remotes/origin/master`를 기준으로 합니다. 최신 상태로 fetch하는 것은 사용자의 책임입니다. 오래된 ref는 등록을 거부하는 쪽으로만 작동합니다.
- **승인 증거의 범위:** 코드는 네트워크 없이 확인할 수 있는 것만 검증합니다. 영수증 형식·값, anchor 결속, git의 protocol 바이트·순서·원격 master 조상 관계입니다. 리뷰 URL이 실제 승인 리뷰인지, 승인자가 독립 검토자인지는 코드가 확인하지 않습니다. 등록 PR 검토자가 병합 전에 GitHub에서 확인해야 합니다(P33과 같은 한계).
- **커밋 시각:** git 커밋 시각과 영수증 관측 시각은 서버 시각을 증명하지 않습니다. 기한 전 병합 여부는 GitHub의 PR·리뷰 기록으로 보완합니다.
- **사후 입력 영수증:** 전방 모드의 `snapshot.json`은 수집 뒤 사용자가 만듭니다. fold별 수집 시각·실자료 출처·파일 SHA는 기존 loader가 검사하지만, 영수증 생성 명령(수집기)은 아직 없습니다. 첫 전방 평가 이슈에서 수집기와 함께 추가해야 합니다.
- **worktree 메타데이터:** `analyze`는 저장소의 `.git/worktrees`에 임시 메타데이터를 만들고 종료 시 제거합니다. 강제 종료되면 `git worktree prune`이 필요할 수 있습니다. 자동 재시도·복구는 하지 않습니다.
- **해석:** 결과는 추가 사전등록 검증 여부만 판단하는 근거입니다. 운영 채택이나 실거래 근거가 아닙니다. `REAL_TRADING=False`이고 추가 지출은 0원입니다.
