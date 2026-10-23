# P10 — 로컬 백업 및 복구 경계

새 서비스·DB·유료 저장소를 사용하지 않는다. 기존에 보유한 로컬 private 공간만 사용한다. MacBook에서 실제 복원/전원 손실 검증은 미실시다. 프로세스 잠금은 롤백 방지 witness가 아니다.

## 보존 단위

| 대상 | 경로/구성 | 보존·검증 조건 |
|---|---|---|
| PAPER 상태 | 기본 `runtime/paper-state.json` | cash/positions/orders/signals 전체 원본, private. `.lock`은 삭제/교체하지 않는 협력 잠금이며 거래 증거 아님 |
| 알림 outbox | 향후 권장 `runtime/notifications/outbox.sqlite3` (main에 미연결) | DB와 `-wal`/`-shm`/`-journal` 현황. 건별 상태/attempts/dedup/sends 유지. enqueue≠delivery |
| P7/P8 인계 영수증 | 사용자가 지정한 DB + `.checkpoint.json` + 별도 사용자 보관 recovery summary | 실제 파일 이름은 `HandoffJournal` 계약/기존 기록을 확인. 원본을 새로 생성하지 않음 |
| P1/P3 Controller | P5 권장 `~/Library/Application Support/auto_trade/ledger/tasks.sqlite`, 별도 `checkpoints/tasks.json` | DB/checkpoint/남은 next/sidecar와 controller.lock 현황을 한 세대로 보존. Fake-only |
| 복구 요약 | P9B 사용자 별도 보관 summary | epoch/revision/hash/receipt prefix 대조. 서명/단조 시계/독립 witness가 아님 |

원장·DB·토큰·로그를 공개 GitHub/Issue/댓글/Actions artifact에 올리지 않는다. 기존 저장소 `.gitignore`는 실수 방지일 뿐 접근 통제가 아니다. 현재 봇/소비자에 중앙 backup orchestrator는 없다. 각 구성의 일관성을 확인해도 구성 사이의 공통 트랜잭션이나 같은 시점의 확정을 증명하지 않는다.

## 운영 중 live backup

이 단계에서는 제공하지 않는다. 실행 중 파일 복사/cp/tar는 cold backup이 아니며 SQLite WAL과 JSON 교체 시점, 별도 checkpoint, 진행 중 Slack 전달을 함께 보장하지 못한다. sqlite3 backup API 한 개의 일관성도 JSON/다른 DB/checkpoint/외부 Slack 전달까지 포함하지 않는다. sidecar 삭제, WAL 강제 checkpoint, 실행 중 MessageQueue 재오픈으로 SENDING을 바꾸는 방법을 쓰지 않는다.

## 완전 종료 후 cold backup 순서

1. 자동 시작/다른 세션이 없음을 사용자가 확인한다. launchd 설정은 이 PR이 변경하거나 조회 명령을 실행하지 않는다. 등록되어 있으면 별도 승인된 관리 절차로 먼저 중지하고 재시작을 방지한다.
2. 정상 PAPER 루프는 실행 터미널에서 Ctrl-C 한 번으로 종료 요청한다. PID와 소유자를 이미 확인한 경우에만 사용자 판단으로 SIGTERM을 보낸다. 이름 기반 pkill, 처음부터 SIGKILL, lock 파일 삭제는 금지한다. 향후 소비자는 새로운 reserve를 중지하고 진행 중 요청이 반환된 후 같은 스레드의 queue.close까지 기다린다. 현재 main에는 소비자가 없다.
3. 실제 프로세스 종료와 상태/outbox/controller 잠금이 모두 해제되었는지 확인한다. `python -m ops.local_preflight ...`는 진단 시점의 기존 잠금만 확인한다. 결과 이후 복사까지의 재시작 방지는 사용자 책임이다. SENDING/UNKNOWN·pending·HALTED는 백업 보존은 가능하나 정상 운영/자동 재시도 허가는 아니다.
4. 사용자 private 백업 경로(0700), 여유 공간, 원본 파일 소유권(0600), 로컬 파일시스템을 확인한다. 기존 백업을 덮어쓰지 않고 새 세대 디렉터리를 사용한다. 백업은 실행 checkout 밖에 둔다. 파일 목록/크기/hash와 sidecar/next 존재 여부를 비공개로 기록한다.
5. 쓰기 프로세스가 없는 동안 **모든 관련 잠금을 유지한 채** JSON, SQLite, companion/checkpoint/recovery summary를 한 세대로 복사한다. outbox에 sidecar가 남으면 정상 cold 상태라고 단정하지 않는다. 원본을 SQLite로 열어 복구/checkpoint/삭제하지 말고 sidecar까지 격리 사본에 보존해 전문가 검토한다.
6. 복사본 hash 대조, JSON parse, SQLite integrity/schema 검사, 기존 checkpoint/epoch/revision 및 영수증 prefix 대조를 **격리 복사본에서** 수행한다. 원본으로 복사본을 덮어써서 시험하지 않는다. DB를 여는 것만으로 복구/UNKNOWN 전환이 발생할 수 있으므로 먼저 read-only 진단한다.

종료 후 **sidecar 없는 PAPER/outbox 두 파일만** 복사하는 POSIX 예시다. 사용자 checkout이 `~/auto_trade`인 경우, 두 파일이 실제 존재할 때만 사용한다. Controller/Handoff 파일을 사용했다면 아래 예시만으로 전체 백업이 완료되지 않는다. 독립 checkpoint 포함 별도 세대 보존과 기존 P9B 대조를 수행한다. Windows 진단은 BLOCKED이므로 이 예시를 사용하지 않는다.

```sh
cd "$HOME/auto_trade"
. .venv/bin/activate
python - <<'PY'
from contextlib import ExitStack
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
from ops.local_preflight import _private, cold_lock
sources = [Path('runtime/paper-state.json'), Path('runtime/notifications/outbox.sqlite3')]
with ExitStack() as stack:
    files = [_private(p, {'.json', '.sqlite3'}) for p in sources]
    for p in files:
        if stack.enter_context(cold_lock(p)) != 'READY_IDLE_AT_SNAPSHOT':
            raise SystemExit('BLOCKED: owner/lock unverified; no backup')
        if any(Path(str(p)+s).exists() for s in ('-wal','-shm','-journal')):
            raise SystemExit('BLOCKED: sidecars require isolated manual reconciliation')
    parent = Path.home() / 'auto-trade-private-backups'
    parent.mkdir(mode=0o700, exist_ok=True)
    if parent.is_symlink() or parent.stat().st_mode & 0o077:
        raise SystemExit('BLOCKED: backup directory must be private')
    dest = parent / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    dest.mkdir(mode=0o700)  # fail rather than overwrite a generation
    for p in files:
        shutil.copy2(p, dest / p.name)
    print('Private two-file cold copy created; verify isolated copies; not a full Controller backup.')
PY
```

## 복원 리허설

P10 테스트는 **TemporaryDirectory의 Fake 데이터**만 사용해 cold copy → 격리 복원 → SQLite integrity → 확정 주문 동일성/실현손익/전송 상태·중복 억제 → 재시작을 검증한다. 원본과 현금이 맞지 않는 부분 복원은 P9-A replay가 차단한다. 기존 P9B는 DB/checkpoint 각각 손상/롤백, 파일 소실, 공동 롤백 탐지 한계를 검증한다.

```sh
python -m ops.paper_rehearsal
python -m unittest tests.test_p9b_cloud_recovery -v
```

실제 복원은 P10-B의 별도 검토 대상이다. 원본을 삭제하거나 강제 덮어쓰지 않는다. 종료/잠금 확인 → 새 격리 경로로 **동일 세대 전체** 복사 → 무결성/원장/revision/receipt 확인 → 최신 GitHub PR/commit/CI와 수동 대조 → 독립 보관 증거와 비교 → 별도 검토 순서를 따른다. 파일 경로를 바꾸면 기존 lock은 새로운 경로의 소유권을 증명하지 않으므로 원래 프로세스가 없는지 먼저 확인한다.

DB+checkpoint+summary가 함께 과거로 돌아가면 내부 정합성이 통과할 수 있다. outbox도 과거 QUEUED로 돌아가면 과거 전달 여부를 알 수 없어 중복 위험이 있다. lock/hash/복사 성공/Mock 성공/GitHub 커밋·댓글은 독립 롤백 방지 증거가 아니다. **복원 신뢰성 부족은 UNKNOWN_NOT_RESET/HALTED 유지, SENDING/UNKNOWN 자동 재발송 금지**다. 실제 Slack 상태를 조회하지 않은 오프라인 도구는 전달 여부를 확정하지 않는다.
