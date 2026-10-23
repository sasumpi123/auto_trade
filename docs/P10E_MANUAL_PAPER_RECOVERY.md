# P10-E 감사형 수동 PAPER 복구

## 현재 판정

**실제 사용자 원장 변경은 미승인이고 BLOCKED다.** 이번 Cloud에서는 Mac/운영 원장/백업/토큰에 접근하지 않았다. P10-D 774개 통과·현금/보유/해시는 사용자 보고이며 Cloud 실기기 검증으로 바꾸지 않는다. 실제 시세/계좌/주문/Slack/launchd/배포/유료 서비스/모델 호출은 하지 않는다. REAL_TRADING=False와 모든 실제 자동화 허용 false를 유지한다.

이번 구현은 기존 잠금·순수 회계 replay·원자적 StateStore.save를 재사용하는 감사형 전환 프로토콜과 읽기 전용 운영 도구다. 전환은 격리된 Fake 원장에서만 테스트한다. **독립 증거/승인/롤백 방지 authority의 실제 구현·연결은 없다. CLI의 --apply도 항상 BLOCKED이며 파일을 읽거나 쓰지 않는다.** 임의 JSON, approved=true, 비어 있지 않은 해시, 동일 계정 댓글을 authority로 취급하지 않는다. 완료되지 않은 전제를 boolean으로 통과시킬 방법이나 provider 선택 CLI는 없다.

기존 P10-D의 "이번 PR에는 mutator 없음"은 P10-D의 범위다. P10-E에는 미연결 내부 전환 primitive가 있지만, 실제 실행 어댑터는 계속 비활성이다. [P10-D](P10D_FRESHNESS_RECOVERY.md), [기존 cold backup](P10_BACKUP_RECOVERY.md), [Mac preflight](P10_MAC_PREFLIGHT.md)의 제한을 완화하지 않는다.

## 로컬에서 확인할 수 있는 증거

- 원본 원장 및 **기존** `.lock`의 단일 소유권. 잠금 파일/원장은 생성·삭제·재초기화하지 않는다. 기존 ExistingPaperStore의 비차단 kernel lock을 재사용한다.
- PAPER/version/account, 주문 구조, 확정 체결 순서/수량/거래대금/수수료, 현금/포지션/엔진 평균단가, 수수료 포함 원가를 P9-A 순수 replay로 재검증한다. engine.open을 호출하거나 HALT를 임시 삭제하지 않는다. 미확정/UNKNOWN/수치 모순은 차단한다.
- 지원 HALT는 `PAPER_SESSION_FAILURE` 한 종류다. 다른 사유/원인 불명 원장/이미 복구 표식이 있는 원장은 전환 대상으로 승인하지 않는다.
- 현재 원장의 **정확한 원시 SHA-256**을 요구한다. 원장과 다른 경로에 보관한 private **장애 후 cold backup**이 원문 bytes/해시/회계 내용까지 같아야 한다. 정상 과거 백업이나 회계만 같고 HALT가 다른 백업은 복구 입력으로 쓰지 않는다.
- 계획 해시는 canonical 원본/백업 경로의 해시(경로 원문은 출력하지 않음), 원장/백업/회계 해시, 확정 거래 건수, 도구 모듈 해시를 묶는다. 도구 모듈 해시는 전체 설치·의존성·검토 커밋을 서명한 증명이 아니다. 실제 독립 리뷰는 정확한 Git head와 동일 head CI까지 별도 확인해야 한다.

장애 후 동일 복사본은 **두 파일의 현재 일치**만 증명한다. 사고 전후 신규 체결 0건, PUBLIC_STALE_TICKER 원인, 파일이 최신이라는 사실, 공동 롤백이 없다는 사실은 증명하지 않는다. 일반 HALT 사유에서 독립 원인을 추론하지 않는다. 사고 전 백업/체결 순서/당시 세션 요약/사용자 관찰의 연결을 별도 검토하고 자료가 없으면 미확인으로 남긴다. 공개 GitHub 댓글/커밋을 신뢰 가능한 영수증 원장으로 쓰지 않는다.

## 외부 검증·일회성 승인 계약 — 현재 미구현

향후 실제 authority는 다음을 독립적으로 검증할 수 있어야 한다. 비용0/권한 범위를 입증하지 못하면 연결하지 않는다. 유료 witness/서버/DB/API를 도입하는 제안이 아니다.

1. 정확한 계획 해시와 구현/검토 커밋에 대해 식별 가능한 사용자가 **이번 한 번의 원장 전환만** 별도 명시 승인했는지 확인한다. 현재 개발 요청은 그런 승인이 아니다.
2. 사고 원인과 매매 시작 전 실패·신규 체결 부재에 대한 근거를 확인한다. 사용자 진술/일반 HALT/빈 로그의 한계를 그대로 보존한다. 근거가 부족하면 거절한다.
3. 정확한 계획 해시·증거 해시·독립 리뷰 자료 해시·만료 시각·일회성 attempt ID를 승인에 결합한다. 승인 유효 범위는 최대 15분이며 전환 직전에도 재검사한다.
4. 상태/감사 폴더와 **함께 롤백되지 않는** 예약/소비 기준을 검증하고 nonce를 원자적으로 한 번 소비한다. 재시작·저장 실패·로컬 복원 시 환불/재사용하지 않는다. 이 기준을 실제 검증할 공식 무료 수단은 현재 확보되지 않았다.
5. 전환 결과 해시의 commit 및 이후 읽기 전용 확인을 제공한다. 구조화된 receipt가 요청의 attempt/plan/approval/phase/after 해시와 일치해야 한다. false/None/수동 true는 허용하지 않는다.

`UnavailableAuthority`가 기본이다. 테스트의 FakeAuthority는 위 계약을 메모리로 **모의**하는 수단이고 실제 계정 인증·독립 원인·프로세스 재시작 후 영속성/공동 롤백 방지를 입증하지 않는다. Python 코드를 직접 수정하거나 자체 authority 객체를 넘길 수 있는 파일 소유자에 대한 접근 통제 장치라고 주장하지 않는다. 실제 verifier와 승인 전달 경로는 별도 설계/승인/코드 리뷰 대상이다.

## 감사형 전환 순서와 장애 처리

`_transition`은 라이브 CLI에 연결하지 않은 내부 프로토콜이다.

1. 기존 원장 잠금을 획득하고 재대사·원본 SHA CAS·장애 후 cold backup을 검사한다. 잠금 실패는 authority 예약 전 차단한다.
2. 외부 authority가 계획에 결합된 사용자의 일회성 승인을 검증/소비해야 한다. 기본 authority는 이 단계에서 차단하여 로컬 파일도 생성하지 않는다.
3. 같은 private 원장 디렉터리 아래 `.paper-recovery/<attempt>/`를 새로 독점 생성한다. 기존 attempt 폴더는 덮어쓰거나 초기화하지 않는다. 파일0600/디렉터리0700, 파일·디렉터리 fsync를 사용한다.
4. `before.json`에 **현재 HALTED 원문 그대로**, `intent.json`에 계획/제한된 승인 메타데이터, `after.json`에 예상 전환 결과를 저장한다. before에는 기존 일반 HALT 사유와 모든 체결 기록이 보존된다. 자유 형식 토큰/로그/실행 명령은 승인 입력이 아니다. 감사 파일 전체는 private이며 GitHub 업로드 금지다.
5. 잠금 inode·원본/백업 SHA·승인 만료를 전환 직전 재검사한다. 기존 atomic StateStore.save로 HALT 상태와 `manual_recovery` 감사 참조를 **같은 원장 쓰기**에 반영한다. 주문/현금/수량/평균단가/신호/기타 기존 필드를 바꾸지 않는다. 백업을 원본에 복원하지 않는다.
6. 실제 저장 bytes가 예상 결과와 같은지 확인하고 `commit.json`에 결과/intent 해시를 저장한다. 외부 authority commit 확인 전이나 로컬 파일만으로 성공을 선언하지 않는다.
7. 잠금을 해제하고 `PENDING_SEPARATE_READ_ONLY_VERIFICATION`만 반환한다. 별도 `verify_transition`이 잠금을 새로 확보하고 실제 원장·before/after/intent/commit·외부 receipt를 재조회한 경우에만 `RECOVERY_VERIFIED_NO_START_AUTHORITY`를 반환할 수 있다. 기본 verifier로는 성공하지 않는다.

감사 파일은 신규 거래/작업 원장을 대체하는 DB가 아니라 이 단발 전환의 필수 write-ahead 증거다. 여러 파일과 외부 소비 기록을 하나의 원자적 트랜잭션이라고 주장하지 않는다. 각 틈에서 중단되면 미확인 상태를 남긴다.

- 예약 후 저장 실패/공간 부족/프로세스 종료: 승인 재사용 금지, 감사 폴더 보존, 원본이나 백업으로 자동 rollback 금지.
- rename 이후 fsync/commit 실패: 원장 HALT 필드는 이미 None일 수 있다. 이를 정상 성공으로 읽지 않는다. 원장의 `manual_recovery` 표식으로 engine.open/기존 main.py 경로·paper_watch·local_preflight가 모두 재시작을 차단한다. 진단도 `NO_HALT_TO_RECOVER` 대신 수동 검증 대기를 표시한다.
- **성공적으로 별도 검증한 전환도 봇 시작 승인이 아니다.** 현 버전에는 이 표식을 해제하는 코드/명령이 없으며 계속 재시작을 차단한다. 별도 PAPER 시작 승인 및 안전한 시작 게이트가 추가 검토되기 전에는 실행하지 않는다.
- advisory lock은 협력 프로세스의 단일 소유권이다. 같은 UID의 임의 파일 조작/lock 삭제/잠금을 무시한 쓰기/디스크 공동 롤백에 대한 보안 경계가 아니다. CAS도 이 잠금 계약 안에서 유효하다. APFS 권한·fsync·전원 상실/절전 내구성은 실제 Mac에서 미검증이다.

## MacBook 읽기 전용 실행 순서

현재 원장을 재실행하거나 수정하지 않는다. 모든 writer를 기존 절차로 정상 종료하고 PID/실제 경로/lock/launchd 등록 여부를 확인한다. 실행 중 git pull/backup을 하지 않는다. source/backup 및 부모는 사용자 소유 private이어야 한다. 백업 파일의 실제 경로를 모르거나 장애 후 cold backup이 없으면 중단한다.

검토·병합 후에만 다음 읽기 전용 명령을 Mac에서 실행한다. 아래 SHA는 **사용자 보고 사고 원장의 값**이며 일치시키려고 파일을 편집하는 기준이 아니다. 현재 파일이 다르면 중단하고 새 진단 결과를 독립 검토한다.

```sh
cd "$HOME/projects/auto_trade"
git status --short --branch
git fetch origin master
git pull --ff-only origin master
git rev-parse HEAD
.venv/bin/python --version
PYTHON_DOTENV_DISABLED=1 .venv/bin/python tools/check_safety.py
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m unittest discover
.venv/bin/python -m ops.paper_recovery_diagnostic --state runtime/paper-state.json
```

장애 후 cold backup을 [P10 절차](P10_BACKUP_RECOVERY.md)로 보존·검사한 다음 실제 파일 경로를 지정한다. 다음 변수의 경로는 예시이며 복사/덮어쓰기 명령이 아니다.

```sh
backup_json="$HOME/auto_trade-cold-backup/paper-state.json"
.venv/bin/python -m ops.paper_manual_recovery \
  --state runtime/paper-state.json --backup "$backup_json" \
  --expected-sha 9f4a790ae878ecf2c52fad512d0dff93a5dd66bd0fc2625c537244a858c0a991
```

출력의 `local_evidence=RECONCILED_READ_ONLY`는 원장/백업의 로컬 대사만 의미한다. 전체 status=BLOCKED와 네 가지 미충족 전제를 그대로 검토에 전달한다. exit2는 의도된 것이며 실행 승인이 아니다. 출력에는 계획 해시/거래 건수/차단 코드만 있고 원장 원문/주문 ID/토큰/사유 원문은 없다. 결과 전체도 필요 이상 공개하지 않는다.

현재 단계에서 사용자가 실행할 실제 상태 변경 명령은 없다. `--apply`를 지정해도 BLOCKED_REAL_WRITE_ADAPTER_NOT_ENABLED이며 파일을 건드리지 않는다. 동작하지 않는 옵션을 켜면 승인된 것으로 해석하지 않는다.

미래 전환이 별도 승인되고 실제 어댑터가 검토된 경우에만, 전환과 **다른 호출**로 다음 형식의 읽기 전용 검증을 한다. 실제 authority 미연결 상태에서는 이 명령도 HALTED를 반환한다.

```sh
# attempt ID는 해당 전환의 감사 기록에서 확인한 32자리 값이어야 한다.
.venv/bin/python -m ops.paper_manual_recovery \
  --state runtime/paper-state.json --verify-attempt '<attempt-id>'
```

검증/감사 파일을 지우거나 표시를 수정하여 재시작하지 않는다. 원장·백업·audit·DB/sidecar·checkpoint를 공개 GitHub나 채팅에 업로드하지 않는다. Slack/실거래/launchd/공개 시장 PAPER 재시작 승인은 각각 별도이며 이번 개발 PR에 포함되지 않는다.

## 검증 범위와 남은 과제

Fake 확정 매수 원장에 대해 전환·감사 원문/회계 보존, 근거/승인/만료/해시 거절, pending/UNKNOWN/현금/수량/평균단가/수수료 모순, cold backup 불일치, 잠금/다중 프로세스 충돌, 저장 전후 실패/실제 프로세스 중간 종료, 변조/일회성 예약, 별도 읽기 전용 검증과 모든 재시작 경로 차단을 확인한다. 전체/3 OS Python3.12 CI와 Safety 결과는 PR exact head/run/attempt에 기록한다. Windows는 기본 읽기 전용 차단 검증만 하며 전환·cold-lock 경로는 기존대로 미인증/BLOCKED다.

실제 사용자 원장 복구, Mac APFS, 독립 원인 증거, 승인 신원·전달, 무료 영속 외부 witness, 복구 후 시작 승인 게이트는 **미완료/미검증**이다. 이 전제의 해결 없이 실제 복구 가능하다고 보고하지 않는다. 현재 소스·백업의 전체 공동 롤백은 로컬 파일만으로 탐지 불가다. 다음 단계는 이 증빙을 무료·공식·최소 권한 경로에서 확보할 수 있는지 판단하는 것이며, 불가하면 BLOCKED를 유지한다.
