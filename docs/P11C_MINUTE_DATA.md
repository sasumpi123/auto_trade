# P11-C: 실제1분봉 수집과 오프라인 전략 진단

기존 등록 연구 자료는 BTC/ETH/XRP/SOL15·60분 snapshot8개뿐이며 Cloud checkout에서 재사용 가능한1분봉 원본을 찾지 못했다. 이 조사는 Mac/개인파일 전체의 조사가 아니다. 원래Mac의 틱·당시 평가 입력도 없으므로 과거0건의 정확한 원인을 특정할 수 없다.

Cloud에서 BTC `[2026-10-26T00:00Z,2026-10-26T06:00Z)` 표본을 공개 REST로1회 시도했으나 `PUBLIC_REQUEST_FAILED`였다. 성공한HTTP응답이나1분봉은 확보하지 못했다. 프록시/연결 세부 원인은 미확인이며 우회·재시도·다른 인증/유료 서비스로 대체하지 않았다. 실제 신호·체결·성과는 **INSUFFICIENT_REAL_DATA/N/A**다. [수집 준비 상태](../research/p11c-minute-readiness-2026-10-27/README.md)에 실패 근거와 실제/합성 구분을 기록했다.

## 별도 수집과 하드 제한

`python -m backtesting.minute_study collect`는 기본 **OFFLINE_PLAN_ONLY**, 요청0/파일생성0이다. `--connect-public`를 붙여야만 네트워크를 사용한다. 분석 subcommand에는 이 옵션이 없다. 기존 `PublicMarketClient`의 공개GET·PublicOnlyAuth(Authorization/Cookie/netrc 차단)·TLS/프록시 유지·HTTP Date/로컬 시계 검사·2MB 응답 상한을 재사용한다. 인증키/계좌/주문/다른endpoint/POST/유료서비스를 받는 인자가 없다. 기존 오래된 collection client의 trust_env=False/자동retry 경로를 사용하지 않는다.

- 고정 대상 BTC/ETH/XRP/SOL 중 중복 없는1~4종목. 자동 순위 선정/교체 없음.
- unit=1만 가능. 명시 UTC 시작/끝, 완료 봉의 `[start,end)`이며201~1440분(최대24시간).
- 기존 연구 전체 종료 `2026-10-21T00:00Z` 이전 시작은 거부한다. 기존 공개개발기간/holdout을 신규 검증으로 재사용하지 않는다. 종료는 수집 시점의 마지막 완료 분 이전이고 최근30일 이내다. 미래/오래된/모호한 timezone/분 경계 불일치는 요청 전에 실패한다.
- 전체40요청, page최대200봉, 기존 client 요청간격0.2초, 요청/read timeout최대5초. 전체deadline120초를 새 요청·응답chunk·page완료 시 검사하며 네트워크 read/close 회수에는 제한된 추가 시간이 걸릴 수 있다. OS 강제종료 watchdog이라고 주장하지 않는다.429/timeout/HTTP오류/시계 점프에서 자동재시도하지 않는다.
- 수집 출력은 **checkout 밖 새 사용자 지정 디렉터리**만 허용한다. 기존 출력 덮어쓰기 없음. symlink·runtime·paper-experiments·notifications·인증 디렉터리는 거부한다. 운영원장/lock/백업을 입력받거나 삭제하는 기능이 없다.

수집 전에 plan/plan SHA와 INCOMPLETE를 기록한다. 역순API pagination을 검증하고 명시적으로 순서를 반전하여 UTC CSV로 저장한다. 중복/역순오류를 sort/dedup으로 고치지 않는다. 시장/unit/UTC 경계/OHLC/거래량/유한수/페이지 진행/요청예산을 검증한다. 각 응답은 canonical SHA와 고정endpoint·params·수신시각·행수만 audit에 남기고 raw응답·키·환경변수·인증정보를 저장하지 않는다.

무체결 분의 누락은 정상일 수 있다. 이를 거래 부재나API장애로 단정하지 않으며 가짜 봉을 채우지 않는다. gap CSV를 보존할 수 있으면 quality 실패/격리로 남기고 분석을 차단한다. API가 요청 구간보다 오래된 봉을 반환하는 등 경계가 불명확하면 수집 실패로 보류한다. 전체 선택 종목이 적합해야 분석하며 자동 제외/대체/구간 잘라내기는 없다.

## 오프라인 검증과 성과

분석은 plan/manifest/schema/UTC수집시간/완료상태, audit의 URL·시장·페이지·count·시각·hash형식, CSV파일SHA와 정규화OHLCV SHA, 연속성·중복·순서·값·기간을 다시 검증한다. 파일SHA는 로컬 무결성 근거이며 **거래소 출처의 독립적 전자서명/attestation이 아니다**. canonical 응답hash는 수집 audit이며 raw응답을 보관하지 않아CSV만으로 재계산하는 값이 아니다. 수집 source는 PUBLIC_REST_DECLARED_SOURCE_NOT_INDEPENDENTLY_ATTESTED로 명시한다. 메타데이터 자체의 공동 조작/롤백을 방어하는 외부 witness는 없다.

테스트에 주입한Fake transport는 자동으로 SYNTHETIC_TEST_TRANSPORT가 된다. 사용자 CLI는 이 자료를 실제자료로 분석하지 않는다. 내부테스트만 명시적으로 허용하며 보고서에도 SYNTHETIC_ONLY_REAL_DATA_INSUFFICIENT를 유지한다. boolean만 바꿔 실제계좌/자동화 허가로 전환하는 경로는 없다.

기존P11-A/B의 prepare/simulate,6개 고정전략, fee5/10bps×slippage0/5/10bps/full-spread2bps, 초기자금1백만원·40%배정·최소금액,5%익절/손절/1회물타기, 실제PAPER 원장/평균단가·P9 수수료포함 원가 대사를 재사용한다. 최근 완료200봉→다음시가, 처음199봉 warmup, 마지막봉은 다음시가가 없어 평가하지 않는다. 1분 간격의실제300초 cooldown을 적용하며 원시/유효 BUY·SELL·HOLD,거래량 차단,쿨다운 전체/실제 신호 차단,보유BUY·미보유SELL,주문거절·현금/최소금액,확정 가상매수/매도를 구분한다. 동일봉 반복체결·미래suffix 참조는 기존검사를 유지한다.

CSV/JSON/Markdown에 비용포함 순수익률·실현/평가손익·MDD·승률·매도당 기대값·수수료/slippage/spread·B&H와 tracehash를 제공한다. 수익률 등의 정의는 [P11-A](P11A_STRATEGY_DIAGNOSTICS.md), 기술통계의 한계는 [P11-B](P11B_STRATEGY_COMPARISON.md)를 따른다. 확정0거래는N/A이며0%수익을 만들어내지 않는다. 평가할 지표 자체가 없는 자료는 INSUFFICIENT_REAL_DATA다. 거래 없는 정상적 평가와 자료부족은 다르다. 단기표본·미청산재고·시가관측/종가MDD·고정비용·전량체결 가정의 한계를 유지한다.

새 수집시각·기간·기존 개발/holdout중첩(false)을 보고한다. 다른 새1분봉 수집과의 중첩은 전역 registry가 없어서 UNKNOWN이며 **independent_validation=false**를 항상 유지한다. 새 날짜라는 이유로 독립검증이나 일반화된 수익성 증거로 승격하지 않는다. 운영전략은 자동 변경하지 않는다.

## MacBook 사용자 명령

이 PR은 Mac에 접근하지 않았다. 병합/설치 검토 후 사용자가 실행 여부를 판단한다. 기존 세HALTED 계좌에는 아무 명령도 실행하지 않는다. 아래 경로는 기존 runtime/계좌와 별도의 연구 경로이며 기존 경로가 있다면 덮어쓰거나 삭제하지 말고 상태를 확인한다. macOS의 /tmp symlink 대신 HOME 아래의 명시 경로를 사용한다.

```sh
cd "$HOME/projects/auto_trade"
data_dir="$HOME/auto_trade-research/p11c-btc-20261026-6h"
report_dir="$HOME/auto_trade-research/p11c-btc-20261026-6h-analysis"

# 기본OFF: 계획만 확인, HTTP0/파일생성0
.venv/bin/python -m backtesting.minute_study collect \
  --output "$data_dir" --markets KRW-BTC \
  --start 2026-10-26T00:00:00Z --end 2026-10-26T06:00:00Z

# 사용자가 공개 데이터 수집을 명시적으로 결정한 경우에만
.venv/bin/python -m backtesting.minute_study collect \
  --output "$data_dir" --markets KRW-BTC \
  --start 2026-10-26T00:00:00Z --end 2026-10-26T06:00:00Z --connect-public

# 완전히 오프라인. 실제 수집/무결성/연속성 미충족이면성공/수익률을 만들지 않음
.venv/bin/python -m backtesting.minute_study analyze \
  --data "$data_dir" --output "$report_dir"
```

예시 날짜는2026-10-27 기준이며30일 수집 제한이 지나면 명시적인 새로운 완료 구간을 고른다. 수집 실패/429/시각오류/gap이면 자동 재시도/삭제/재초기화하지 않는다. 실패기록을 보관하고 원인과표본을 검토한 뒤 다른 새 연구경로/기간을 명시적으로 판단한다. 보고서 공유는README/JSON/CSV/Markdown의 비밀 없는 집계만 전달한다. SSH·운영 봇·Slack·launchd 자동실행을 함께 켜지 않는다.

실거래/실제 운영PAPER/Slack/launchd/자동시작·재시작/유료 API·모델·인프라/PR자동병합은OFF, 추가지출0원이다. 연구용임시 원장 외의 기존계좌·락·백업·holdout은 변경하지 않는다. Mac실제수집/실제1분봉성과는 아직 미검증이다.
