# P4: 독립 리뷰·최종 병합 검증 (Fake-only)

## 범위와 활성화 상태

승인 Issue → P3 Fake Worker → Fake Publisher → 독립 표준 Review → CI → 최종 재조회 → 모의 병합을 구현합니다. 실제 개발 실행·게시·병합을 활성화하는 PR이 아닙니다. 기존 Plus 구독료 외 추가 지출 허용액은 **0원**입니다. policy.enabled=false, zero-cost.json의 모든 live 허용값=false, REAL_TRADING=False, builder/reviewer 실제 ID 빈 목록과 Disabled launchd 예제를 유지합니다.

이 개발 PR을 사람이 요청하여 제출하는 작업과, 프로젝트가 자동으로 Push/PR/merge하는 실행 경로는 별개입니다. 후자의 쓰기 API·Codex CLI·모델 호출은 구현/활성화하지 않았습니다. GitHub Secrets, App, 권한, 인증정보, launchd 등록을 변경하지 않았습니다.

## 계층과 데이터 흐름

| 파일 | 책임 | 허용되는 결과 |
|---|---|---|
| automation/review_gate.py | 불변 ID, 최신 Review 상태, 정확한 commit_id, 독립성 | APPROVED / CHANGES_REQUESTED / WAIT_REVIEW / BLOCKED 메타데이터 |
| automation/github_graphql.py | 고정 공식 GraphQL query 5종, 제한된 페이지 조회 | 읽기 전용 데이터. mutation/임의 query 없음 |
| automation/merge_reader.py | P2 REST 사실 + threads/requests/CODEOWNERS + 모든 최종 조건 | READ_ONLY_VERIFIED 또는 VerificationPending. 실행 후보/권한을 만들지 않음 |
| automation/fake_review_relay.py | 정확한 Fake 서비스의 검증된 피드백 → P3 Fake 리뷰 계약 | 중복 제거·재작업 허용 판단. 실제 관측값 연결 불가 |
| automation/fake_merge.py | 정확한 FakePipeline/FakeMergeWorld/ValidatedPatch만 수락 | MERGE_ELIGIBLE → MERGE_INTENT → FAKE_MERGED 또는 HALTED |
| automation/worker_store.py | 로컬 원장의 모의 intent/receipt/피드백 해시와 감사 기록 | 트랜잭션, CAS, 무결성, checkpoint 검증 |
| automation/observer_diagnostics.py | 제한된 고정 진단 코드 | PENDING 유지. 본문/예외 메시지/토큰 출력 없음 |

P1 Fake-only 제한은 유지합니다. P2 실측 Snapshot이나 인증된 MergeReader는 P3 FakeAuthority/Worker 또는 FakeMergeBroker의 입력으로 승격되지 않습니다. Broker는 exact type으로 Fake만 받으며 live merge 클래스는 항상 예외를 반환합니다. 모듈을 import하는 것만으로 외부 통신이 일어나지 않습니다.

## 표준 Review와 독립성

GitHub REST의 Review ID·user.id·state·commit_id만 권한 판단에 사용합니다. login/표시명/일반 PR 댓글은 승인 증거가 아닙니다. 사용자별 최신 비-COMMENTED 상태를 적용하고 최신 APPROVED는 정확한 head와 일치해야 합니다. COMMENTED는 기존 표준 Review를 지우지도, 승인을 새로 만들지도 않습니다. PENDING 초안은 승인이 아닙니다. 허용 독립 리뷰어와 PR 작성자의 불변 ID가 같으면 차단합니다.

허용 리뷰어 수는 보호 규칙의 요구 수 이상이어야 합니다. 다른 리뷰어의 미해결 CHANGES_REQUESTED도 보수적으로 차단하며, DISMISSED/오래된 허용 리뷰는 WAIT_REVIEW입니다. 서버의 reviewDecision=APPROVED 및 pending review request가 없다는 사실도 별도로 요구합니다. REST Review가 충분해도 이를 대체하지 못합니다.

피드백은 최신 head의 허용 리뷰어 CHANGES_REQUESTED만, 건당 UTF-8 8KiB 이하의 **신뢰하지 않는 데이터**로 수락합니다. 셸/명령/권한 확대 지시로 변환하지 않습니다. 원장에는 ID/hash/head만 남기며 본문을 저장하지 않습니다. task/head/version/review/content hash 중복 키와 P3의 중복 Review 처리를 함께 사용합니다. 같은 head의 오래된 ID는 STALE로 무시하며, 실행 중 새 리뷰/취소는 HALTED입니다. 새 publish는 승인/CI를 무효화합니다. 기존 최대 3회·연속 실패 2회·lease·예약 정책은 변경하지 않습니다.

## 최종 검증과 보수적 지원 범위

MergeReader는 전체 증거를 두 번 조회하여 같은지 확인합니다. Issue는 현재 open, 정확한 라벨·관리자 ID·현재 body SHA-256과 수정되지 않은 승인 댓글이 필요합니다. PR에는 정확히 하나씩 다음 기계 계약이 있어야 합니다.

```text
/autodev issue <number> spec <sha256>
/autodev task <uuid> version <approved-spec-version>
```

TaskReference는 task/issue/PR/spec-version/head-version/base/head/변경 경로 hash를 고정합니다. PR 작성자·저장소 ID·autodev/issue-N branch·기본 master·head/base·현재 master commit·merge base를 검증합니다. 보호된/명세 외 경로와 rename 이전 보호 경로를 거부합니다. 실제 byte patch의 모드/내용/비밀정보/경로 이탈 검증은 P3 PatchGuard와 검증된 publisher receipt에 묶여 있으며 파일 목록 검증만으로 이를 대체하지 않습니다.

CI는 P2의 최신 run/attempt, App 15368 및 owner 9919, 고정 workflow, PR/repo/head, job/check URL과 ID, 필수 4개 검사 이름을 재사용합니다. 완료 success만 통과하며 누락/skip/위조/다른 head/실패/재실행 중인 상태는 차단합니다. CI 전체 테스트가 통과했다는 주장과 observer의 PENDING은 별개로 보고합니다.

GraphQL은 공식 https://api.github.com/graphql에 **고정 query만 POST**합니다. POST라는 전송 방식이 mutation 권한을 의미하지 않습니다. PR databaseId/head/base/state/reviewDecision/mergeable/mergeStateStatus, 전체 reviewThreads, reviewRequests, 정확한 base SHA의 CODEOWNERS blob, owner의 불변 user ID를 읽습니다. unresolved는 outdated여도 차단합니다. 페이지 수/총 개수/중복/cursor/head/base 일관성을 검증합니다. 부분 응답 errors/null/권한 부족/rate limit은 PENDING입니다. 2MB 응답·개별 timeout 10초·GraphQL 40요청·REST+GraphQL 총 120요청 이내이며 초과 시 중단합니다.

Classic protection은 strict checks, admins 포함 적용, stale 승인 폐기, last-push 승인, conversation resolution, 강제 Push/삭제 불가, 명시적인 빈 bypass 목록을 요구합니다. 활성 branch rules는 조직에서 상속된 규칙을 포함하는 공식 `/rules/branches/master`를 페이지 조회합니다. 활성 ruleset별 상세·enforcement·bypass_actors를 추가 확인합니다. 현재 지원 규칙은 pull_request, required_status_checks, non_fast_forward, deletion뿐입니다. 미지원 규칙/파라미터, merge queue, autoMergeRequest는 별도 검증 전 PENDING입니다. 이름이 같은 check라도 App이 다르면 거부합니다.

CODEOWNERS는 base의 `.github/CODEOWNERS`, `CODEOWNERS`, `docs/CODEOWNERS` 순서로 선택하고 Blob SHA/size/truncation을 확인합니다. 필수인 경우 마지막 일치 규칙과 각 파일당 owner 중 최소 한 명의 현재 head APPROVED를 요구합니다. owner 이름은 공식 API로 불변 ID와 write/maintain/admin 권한에 다시 연결합니다. 현재 지원 패턴은 `*`, 절대 리터럴 파일/디렉터리 및 개별 `@user`뿐입니다. 팀·복잡한 glob·미지원 문법·누락 owner/권한/승인은 PENDING입니다. 전체 GitHub CODEOWNERS 의미론을 지원한다고 주장하지 않습니다.

GitHub는 read-only 호출에서 ruleset bypass_actors를 생략할 수 있습니다. **생략은 우회 없음의 증명이 아닙니다.** 따라서 이 정보가 보이지 않으면 후보를 만들지 않습니다. 권한을 자동 확대하지 않습니다. 지원하지 않는 규칙도 제거하거나 성공으로 간주하지 않습니다.

이번 개발 중 branch GET으로 재확인한 master(`ff12a8ad715742cfd1b3e08c5c013b18904907e8`)는 `protected=false`, protection.enabled=false를 반환했습니다. 이는 현재 실제 자동 병합 후보가 될 수 없다는 별도 차단 사유입니다. 상세 보호 endpoint/GraphQL 권한을 실측한 결과로 확대 해석하지 않으며 설정을 직접 변경하지 않았습니다.

## 최종 경합·응답 유실·재시작

Fake Broker는 P3의 현재 승인·작업 버전·patch digest·publisher receipt·head와 최신 Fake Review/CI를 먼저 묶습니다. 0원/Plus quota 증빙은 테스트에서만 Fake로 제공합니다. 모든 증빙이 있어도 execution_enabled=false입니다. 검증된 후보의 유효기간은 15초입니다. simulate 직전 전체 API 사실을 다시 조회하고 승인 당시 증거 hash와 현재 Fake 서버 전체 fingerprint가 같아야 합니다. 로컬 store의 MERGE_INTENT CAS 후 Fake 서버도 lock 안에서 fingerprint/head/base를 원자적으로 비교합니다. 동시 broker 중 한 번만 효과를 냅니다.

실제 GitHub REST merge의 `sha`는 expected **head** 조건입니다. expected master **base**와 모든 보호 상태를 한 트랜잭션에 묶는 CAS로 간주할 수 없습니다. 두 번 읽기나 로컬 lock도 실제 GitHub의 시간차 경합을 제거하지 못합니다. P4가 증명한 원자성은 Fake 서버 범위뿐이며, 실제 broker는 무조건 비활성입니다. P5에서 branch protection/merge queue/서버 검증 및 base race 처리를 별도로 설계해야 합니다.

효과 후 응답이 유실되면 HALTED이며 자동 재전송하지 않습니다. reconcile은 Fake receipt의 task/head/base/PR을 대조해 기록만 하고 HALTED를 해제하지 않습니다. 중단된 MERGE_INTENT도 재실행하지 않습니다. SQLite를 새 client로 열어 상태를 복구하는 시험과 실제 자식 프로세스 `os._exit` 후 checkpoint mismatch 차단을 검증합니다. P3 FakeWitness는 메모리 내 시험용이지 내구 anti-rollback 구현이 아닙니다. 프로세스/호스트 재시작 시 trusted checkpoint가 없으면 자동 재개할 수 없습니다. Mac sleep/offline/재부팅 후 새 GitHub 증거와 원장/lease/receipt/내구 checkpoint를 확인하기 전 대기합니다. DB 삭제/예산 환급으로 한도를 초기화하지 않습니다.

## PR #12 observer 조사

공개 PR/API 및 [observer run 39569973177](https://github.com/sasumpi123/auto_trade/actions/runs/39569973177)의 job 119121412053 로그를 읽기 전용으로 확인했습니다. 당시 master checkout SHA는 `02a4f09194ffe9a59524436920626d04e8b896c1`, Actions/Checks/Contents/Issues/PullRequests는 모두 read로 표시됐고 PENDING/github_state_unverified, execution_enabled=false, model_calls=0, exit 2였습니다.

현재 PR #12에는 정확한 `/autodev issue ... spec ...` 계약이 0개입니다. P2는 정상 repo/PR 조회 뒤 이 계약이 없으면 issue_link_required로 중단합니다. 동일 조건을 Fake HTTP로 재현하면 새 진단 ISSUE_CONTRACT_MISSING이 나옵니다. 따라서 일반 수동 PR의 정상적 보류를 설명하는 조건은 확인했습니다.

그러나 당시 로그에는 고정 세부 코드가 없었으므로 **실제 최초 실패가 이 조건이었다고 확정할 수 없습니다**. 그보다 앞선 API 권한/응답 스키마/라우팅/일시 오류 가능성은 미확인입니다. connector의 현재 연결과 당시 Actions GITHUB_TOKEN은 다르며, 로그에 read가 있다고 보호 규칙 조회 권한까지 있다고 추론하지 않습니다. 권한 확대나 실패→성공 처리는 하지 않았습니다.

새 observer는 알려진 고정 코드만 출력하고 permission/rate limit, schema, contract missing, 상태 변경, observer 밖으로 전파된 프로그램 오류를 구분합니다. 기존 Verifier 내부의 schema/프로그램 오류 변환을 넘어 모든 과거 원인을 확정하는 도구는 아닙니다. 예외 원문·이벤트·body·token은 출력하지 않습니다. decision/reason/exit 2 정책은 유지합니다. 권한/스키마 미확인은 계속 PENDING입니다. observer는 trusted master 코드를 checkout하므로 이 PR이 병합되기 전에는 새 진단 코드가 해당 workflow에 적용되지 않습니다. 이 PR의 일반 수동 제출 역시 계약 없는 observer 보류를 예상하며 Python CI와 구분합니다.

## 공식 ChatGPT/Plus 기능 확인과 P5 사용자 설정

[공식 Automations 문서](https://learn.chatgpt.com/docs/automations)는 GitHub PR 이벤트 작업을 다루지만 계정/제품별 가용성과 GitHub의 표준 APPROVED/CHANGES_REQUESTED Review 자동 게시 여부는 실제 사용자 계정에서 확인하지 않았습니다. 예약 작업이나 ChatGPT의 대화 응답을 표준 Review로 추정하지 않습니다. **현재 독립 자동 리뷰는 미연결/WAIT_REVIEW**이며 유료 API, 비공식 HTTP, 브라우저 스크래핑, Codex 자기 승인으로 대체하지 않습니다.

[공식 CLI 인증](https://learn.chatgpt.com/docs/auth), [비대화형 exec](https://learn.chatgpt.com/docs/non-interactive-mode), [구독 포함량](https://learn.chatgpt.com/docs/pricing)에 따라 향후 사용자가 Mac에서 공식 ChatGPT 로그인을 진행해야 합니다. Plus 로그인만으로 추가 크레딧 미사용과 비용 0원을 증명하지 못합니다. 이번에는 CLI/login/status를 실행하거나 auth.json/Keychain/세션 토큰을 읽지 않았습니다. 자동 API 인증 fallback/크레딧 구매는 금지합니다.

P5는 아래를 구현·검증한 뒤 별도 사용자 승인을 받아야 합니다. 이번 PR에서 직접 설정하지 않습니다.

- [ ] 실제 Plus 계정에서 공식 PR 이벤트/예약 Work 가용성, 표준 Review 게시, 정확한 commit_id, 별도 불변 reviewer ID를 확인. 미지원이면 WAIT_REVIEW 유지
- [ ] Mac M3의 공식 CLI 버전/로그인/포함 quota의 신뢰 가능한 최신 조회, 유료 크레딧 미사용·자동 구매 차단을 입증. 불명/초과/만료면 중단
- [ ] API 키·대체 provider 환경 이름 감지 및 fail-closed 강제. 인증정보 추출/복사 없이 공식 흐름만 사용
- [ ] 기존 read 권한에서 GraphQL·보호 규칙·CODEOWNERS owner 권한·ruleset bypass 조회가 실제 가능한지 별도 실측. 부족 시 PENDING 유지; 사용자 승인 없이 권한 확대 금지
- [ ] Worker/읽기 controller/Publisher/Reviewer/Merge Broker의 OS 자격·최소 권한 경계를 검증. builder/reviewer ID는 사용자만 검토하고 분리
- [ ] 내구 checkpoint·rollback 감지·로컬 SQLite backup/restore와 실제 Mac sleep/재부팅 복구, 모호한 publish/merge 조정 및 수동 해제 절차 구현
- [ ] 실제 writer/merge의 expected head 및 base race·merge queue·보호 정책 변경·서버 측 독립 검증을 별도 구현. P4 Fake 원자성으로 대체 불가
- [ ] 사용자만 비활성 launchd 예제의 경로/계정/시작 정책 확인. 지금은 설치·활성화하지 않음
- [ ] 공개 저장소 무료 standard Actions 조건 유지. 유료 runner/외부 서비스/캐시·artifact 과금 경로 추가 금지
- [ ] 0원 증명과 독립 검증 이후에도 실제 Worker/게시/병합은 사용자 명시적 활성화 승인 전까지 비활성

이 인터페이스는 추가 유료 서비스 없이 로컬 Fake 테스트 가능합니다. 실제 Plus/권한/실시간 사용량/OS 격리/네트워크 E2E/서버 원자성은 미검증입니다. 회당 $5/Issue $15/UTC 일일 $50은 기존 **모의 예약값**일 뿐 실제 지출 hard cap이나 지출 허가가 아닙니다. 실거래·운영 배포·유료 모델 호출은 계속 금지합니다.

## 검증 방법과 공식 근거

```bash
python tools/check_safety.py
python -m unittest tests.test_review_merge_gate -v
python -m unittest discover -v
```

신규 92개 테스트와 기존 321개를 함께 실행합니다. HTTP fixture와 mock만 사용하며 실제 GitHub/모델/거래소를 호출하지 않습니다. Linux/macOS/Windows Python 3.12 결과와 정확한 head는 PR에 기록합니다. 실제 계정 검증과 모의 통과를 혼동하지 않습니다.

GitHub 공식 근거: [GraphQL Pulls](https://docs.github.com/en/graphql/reference/pulls), [Git/Blob](https://docs.github.com/en/graphql/reference/git), [rules/ruleset 및 bypass 노출](https://docs.github.com/en/rest/repos/rules), [CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners), [조건부 merge sha](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request). 이 문서의 지원 부분집합과 실제 배포 전 제한을 함께 적용합니다.
