# PR #1 인증 및 CI 리뷰 반영

## 범위와 역할

Codex는 구현·테스트·PR 갱신과 리뷰 수정 반영을 담당하며, 별도의 ChatGPT가 설계·전략·코드 리뷰·검증·병합을 담당합니다.
Codex는 PR을 병합하거나 자동 병합하지 않습니다. PR #1의 리뷰 통과와 병합 전에는 다음 매매 전략을 개발하지 않습니다.
실제 업비트 매수·매도와 맥북 운영 배포는 별도 명시적 승인 전까지 금지합니다.

이번 변경은 private REST 인증과 검증·CI에 한정됩니다. 주문 영속화/중복 제출 방지/체결 대조/재시작 복구와
RSI·MACD·볼린저, 물타기, 투자 비율, bfill 및 비용 없는 모의 체결 정책은 유지합니다.

## JWT와 query_hash

기준: [업비트 공식 인증 가이드](https://docs.upbit.com/kr/reference/auth).

- JWT는 HS512로 서명하고 Secret Key를 원문 그대로 사용합니다. Base64 디코딩하지 않습니다.
- 요청마다 새 UUID nonce를 생성합니다. 파라미터가 없으면 query_hash/query_hash_alg를 넣지 않습니다.
- 요청 파라미터의 순서를 유지한 사본을 한 번 만들고 `urlencode(..., doseq=True)`로 전송용 문자열을 생성합니다.
- 서명 입력은 공식 Python 예시와 같은 `unquote(encoded_query)`의 UTF-8 바이트입니다. 키 정렬이나 unquote_plus를 사용하지 않습니다.
- GET은 같은 인코딩 문자열을 requests에 전달하므로 배열이 `states[]=wait&states[]=watch` 순서로 반복됩니다.
- POST JSON은 같은 사본을 사용합니다. JSON의 바이트 자체를 해시하지 않고 그 필드들을 쿼리 문자열로 변환해 해시합니다.
- bool/float/None/중첩 객체/빈 배열 등 JSON과 쿼리 변환의 의미가 달라질 수 있는 입력은 전송 전에 거부합니다.
- 서명하지 않은 쿼리를 추가할 수 있는 Session 기본 params도 전송 전에 거부합니다.
- requests가 준비한 URL/본문/헤더를 가짜 HTTP 세션에서 캡처해 실제 전송 형태와 해시·서명을 독립적으로 검증합니다.
  정상/잘못된 키, 알고리즘 제한, 배열·순서·유니코드·인코딩, POST 매수·매도, 파라미터 없음,
  nonce 유일성 및 오류 시 재시도하지 않는 동작을 검사합니다.

HS256에서 HS512로 바꾼 이유는 공식 권장 방식과 리뷰 요청을 따르기 위해서입니다.
예상 영향은 인증 토큰의 서명/헤더 변경과 모호한 입력의 사전 거부이며 매매 신호나 주문 재시도 정책은 바뀌지 않습니다.
실제 API 인증/체결의 성공 여부는 이번 테스트로 확인하지 않았습니다.

## GitHub Actions

- `.github/workflows/tests.yml`: Python 3.12, ubuntu-latest/macOS-latest/windows-latest 매트릭스.
- PR 열기/동기화/재열기/리뷰 준비, master push 및 수동 실행 트리거를 사용합니다.
- PR에서는 GitHub의 임시 merge commit 대신 정확한 PR head SHA를 checkout합니다. 로그에 Python 버전과 SHA를 남깁니다.
- `contents: read`, checkout의 `persist-credentials: false`, SHA로 고정한 공식 actions를 사용합니다.
- `pull_request_target`, 배포·주문 실행 단계, 저장소 비밀키 주입, 자동 병합 단계는 없습니다.
- 테스트 시 거래소/알림 키 환경변수를 비우고 `.env` 자동 로드를 끕니다.
- 기본 보안/모의매매 설정 검사 → 의존성 설치 → 문법 검사 → 전체 unittest를 실행합니다.
- 모든 OS가 성공해야 `Python 3.12 safety gate`가 성공합니다. 이 이름을 필수 상태 검사로 사용할 수 있습니다.
  **워크플로 추가만으로 GitHub branch protection의 필수 검사 설정이 생기는 것은 아닙니다.** 저장소 관리 설정은 별도 검토 사항입니다.
- CI 결과가 성공해도 ChatGPT의 리뷰 통과·병합 판단과 실제 거래/운영 배포 승인을 대신하지 않습니다.

## 민감정보 보호 검사

`python tools/check_safety.py`는 Git 추적 파일만 읽습니다. config를 import하거나 API를 호출하지 않습니다.

- `REAL_TRADING`이 정확히 한 번 literal `False`로 설정됐는지 AST로 확인합니다.
- `.env`/변형, 키·인증서·자격증명 파일이 추적되면 실패합니다. `.env.example`은 빈 값/가짜 placeholder만 허용합니다.
- 일반적인 토큰/개인키 형식과 명백한 하드코딩 키를 검사하고, 오류에는 파일 경로와 사유만 출력합니다.
- `.gitignore`가 `.env`, `.env.*`, `.venv/`, 키 파일 및 runtime 상태 파일을 실제로 제외하는지 git으로 검증합니다.
- CI에 거래소/알림 키가 주입되면 값은 출력하지 않고 실패합니다.

이 검사는 알려진 형식과 설정을 확인하는 제한된 기본 검사입니다. 모든 비밀정보 탐지나 전체 Git 이력 검사를 보장하지 않습니다.
로컬 비추적 .env 내용을 업로드하거나 출력하지 않으며, 실제 키로 테스트하지 않습니다.

## 검증 한계

서명·HTTP 요청 검증은 가짜 키와 가짜 HTTP 세션만 사용합니다. 실제 거래소 주문 테스트·인증 계좌 조회·운영 배포는 수행하지 않았습니다.
CI의 macOS 호스트 결과는 집 맥북 M3의 장시간 시세 수신·전원·절전·재부팅 운영 검증을 대체하지 않습니다.
미확정 주문, 동기 처리 지연, 실행 중 외부 계좌 변화 등 나머지 운영 위험은 `P0_SAFETY.md` 및 PR 설명을 참고합니다.
