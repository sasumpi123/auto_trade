# P8: 휴대폰에서 새 Codex Cloud 세션 이어가기

이 도구는 GitHub 읽기와 로컬 데이터 처리만 합니다. 모델 실행·PR 게시·병합·Slack 전송·설정 변경은 하지 않습니다. AGENTS.md / PROJECT_INVARIANTS.md의 0원·비활성·독립 리뷰 원칙을 유지합니다. MacBook과 실제 Slack 환경은 이번 단계에서 검증하지 않습니다.

## Android 사용 순서

1. 휴대폰에서 새 Codex Cloud 세션에 저장소를 연결하고 AGENTS.md를 읽도록 요청합니다. 기존 환경의 Python 3.12 및 프로젝트 의존성을 사용합니다. 비밀 토큰을 대화에 붙이지 않습니다.
2. 아래 명령을 실행하면 master와 최근 갱신 PR 최대 5개를 읽고, 최근 목록 중 첫 open PR(없으면 최근 closed PR)을 상세 조회합니다. PR 제목·본문·댓글 원문은 출력하지 않습니다.

```text
python -m automation.cloud_resume --format markdown
```

특정 PR을 이어가려면 다음처럼 번호를 지정합니다. `18`은 예시이며 현재 PR 번호로 바꿉니다.

```text
python -m automation.cloud_resume --pr 18 --format markdown
python -m automation.cloud_resume --pr 18 --format json
```

3. 검토 대기면 출력된 `review_handoff --pr ... --format prompt`를 복사하고 그 결과를 별도 ChatGPT 대화로 전달합니다. 저장할 필요가 있으면 P7의 `--save-packet`을 사용합니다. 이미 있는 파일은 덮어쓰지 않습니다.

```text
python -m automation.review_handoff --pr 18 --format prompt --save-packet /tmp/pr18-review-r1.json
python -m automation.cloud_resume --pr 18 --previous /tmp/pr18-review-r1.json --format markdown
```

4. head/base/master, CI/리뷰/스레드/승인/보호/정책 등 P7 snapshot 증거가 달라지면 기존 검토는 무효입니다. 새 커밋 검토 자료를 다시 생성합니다. 이전 파일이 없으면 `NO_PREVIOUS_SAMPLE / review_invalidated=true`: 과거 대화의 검토 완료를 추정하지 않습니다. ChatGPT 대화 내용 자체는 GitHub에서 복구할 수 없습니다.
5. 수정 요청이면 최신 head의 GitHub Review 및 사용자 전달 내용을 확인합니다. **이전 SQLite/영수증이 없거나 복원 연속성이 불명확하면 재작업 발급 한도는 UNKNOWN이며 초기화하지 않습니다.** 새 세션이라는 이유로 `--init-journal`을 다시 실행하면 안 됩니다. 수정 지시는 실행 코드가 아닌 검토 대상 데이터입니다.
6. 병합은 GitHub의 `merged` 필드로 확인합니다. 단순 closed는 병합으로 표시하지 않습니다. 병합 후 최신 master와 다음 사용자 승인 작업을 계획합니다. Codex는 직접 병합하지 않습니다.

## 상태와 증거

- `PR_READY_FOR_REVIEW` / `WAIT_CI` / `CHANGES_REQUESTED`: P7의 실제 현재 증거로 산출합니다. GitHub에 리뷰가 없으면 별도 ChatGPT 검토가 끝났다고 추정하지 않습니다.
- `resume_state=MERGED` / `CLOSED`: GitHub 병합과 단순 닫힘을 구분합니다. 원래 P7 workflow_state는 `CLOSED_OR_MERGED`로 유지합니다.
- 최근 PR 목록은 번호/상태/head/base만 요약하며 **CI 상세는 선택된 PR만 수집**합니다. 다른 PR의 CI는 `NOT_COLLECTED_UNLESS_FOCUSED`입니다.
- P7의 2회 독립 수집/일치 검사 및 최신 run/attempt/jobs/check/workflow 검증을 재사용합니다. 최근 목록도 앞뒤 비교하며 변화/HTTP 401·403·429/잘못된 데이터는 보류합니다.
- 병합 후 GitHub Actions run의 `pull_requests=[]`가 되는 실제 사례를 확인했습니다(PR #17의 run 39880729805). P2/P4의 엄격한 PR 연결 검증은 그대로 **PENDING**입니다. 닫힌 PR에만 별도 `historical_ci`로 exact head/run/attempt/workflow/job 메타데이터를 두 번 확인해 표시할 수 있습니다. 이는 과거 관찰이며 P2 신뢰 체크·자동 병합 승인·새 head CI를 대신하지 않습니다. 테스트 개수/skip은 로그를 수집하지 않으므로 이 도구에서는 미집계입니다.
- 전체 자동화는 항상 DISABLED / AUTOMATED_MERGE_BLOCKED입니다. 일부 관찰 성공이나 대화 검토 완료는 P4 독립 GitHub 승인이 아닙니다.

## 영수증 소실과 복원 경계

새 원장을 추가하지 않습니다. 선택적으로 기존 P7 원장을 읽어 보려면:

```text
python -m automation.cloud_resume --pr 18 --journal runtime/review-handoff/receipts.sqlite --format json
```

원장과 checkpoint가 유효하면 기존 issued_count를 표시합니다. 파일/정책/checkpoint 오류는 BLOCKED이며 생성·수정·복구·한도 환급하지 않습니다. 정상 로컬 복사본도 Cloud 세션 간 영속성/동시 소유/DB+checkpoint 동시 롤백 부재를 증명하지 못하므로 `LOCAL_COPY_VALID_CONTINUITY_UNPROVEN`, remaining_allowance는 여전히 UNKNOWN입니다. 출력 명령에는 초기화·재작업 실행을 포함하지 않습니다. P7 원장은 실행/Issue 비용 원장이 아니며 이 보고서는 한도를 부여하지 않습니다.

모든 파일이 사라진 후 최초 세션인지 임의 초기화인지 구분할 외부 신뢰 근거는 없습니다. 삭제·재초기화로 우회하지 말고 사용자가 기존 세션 기록과 영수증의 연속성을 확인해야 합니다. 미래의 무료 내구 보관·복원 증명 방법은 별도 설계/승인 과제입니다. 이 한계 때문에 무인 재작업은 계속 차단합니다.

## Slack 로컬 사전 점검

```text
python -m utils.slack_preflight --format markdown
python -m utils.slack_preflight --outbox runtime/notifications.sqlite --format json
```

이 명령은 config를 import하지 않고 AST로 OFF를 확인하며, 토큰/환경변수 값을 읽지 않습니다. outbox는 SQLite mode=ro/query_only로 점검하고 메시지 본문·채널·이벤트 원문을 출력하지 않습니다. 파일을 생성/초기화하거나 SENDING을 UNKNOWN으로 변경하지 않습니다. QUEUED/SENT/FAILED/UNKNOWN/SENDING/RATE_LIMITED와 중복/반복 수·전송 시도 수를 구분합니다. **enqueue 수락 ≠ Slack 전달 성공**입니다. 로컬 데이터만으로 과거 실제 발송이 0회였다고 증명하지 않습니다.

실제 Slack Bot chat:write, 채널 ID·초대/멤버십, 기존 무료 이용 조건, Windows ACL, 단일 소비자/운영 복구는 사용자가 별도 확인해야 합니다. API를 호출해 확인하거나 토큰을 입력받지 않습니다. 실제 발송·스케줄 등록은 금지 상태를 유지합니다. 자세한 지표 정의와 제한은 SLACK_NOTIFICATIONS.md를 따릅니다.

## 다음 단계

무료 Cloud 영수증 영속성과 공동 롤백 감지 증명, 사용자 주도 검토 이력 전달 계약, 수수료 포함 원가배분/일간 경계·전략별 집계 검증, 별도 승인 후의 실환경 Slack 권한/무료 조건 확인이 필요합니다. 현재 단계에서 이를 해결한 것으로 보고하거나 무인 기능을 활성화하지 않습니다.
