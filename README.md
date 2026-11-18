# auto_trade — 업비트 원화 현물 퀀트 연구·PAPER 시스템

**장기 목표:** 업비트 원화 현물에서 판단 시점에 알 수 있는 정보만으로 전략·투자 비중·현금 대기를 고릅니다. 정해 둔 위험 한도 안에서, 비용을 뺀 뒤에도 장기적으로 자산을 늘릴 수 있는지 **검증한 뒤에** 운용합니다. 자세한 내용은 [프로젝트 목표와 완료 기준](docs/PROJECT_GOAL.md)을 참고하세요.

**현재 운용 범위:** 아래 두 가지뿐입니다.
- 키 없이 하는 오프라인 연구(백테스트, 사전등록 평가 도구, 합성 검증)
- 사용자가 별도로 승인한 감독하 **PAPER**(모의매매)

`REAL_TRADING=False`입니다. 실거래, 자동 매수·매도 운용, 운영 배포, Slack 발송, 예약 실행, 자동 병합은 모두 꺼져 있습니다. 수익성이 입증된 전략도 아직 없습니다.

처음 읽는 사람과 AI는 이 순서로 확인하세요:
1. README
2. [PROJECT_GOAL](docs/PROJECT_GOAL.md)
3. [ROADMAP](docs/ROADMAP.md)
4. [AGENTS](AGENTS.md)
5. [PROJECT_INVARIANTS](docs/PROJECT_INVARIANTS.md)

## 지금 있는 것과 없는 것

| 구분 | 상태 |
|---|---|
| PAPER 거래·회계 | Decimal 원장(P9), 비용 반영 체결(P17), 미확정 주문 재제출 금지와 HALT, 수동 재개(P26), 입력 격리(P25). 합성 테스트와 macOS CI로 검증했습니다. 장시간 실자료 PAPER는 아직 검증하지 않았습니다. |
| 연구 도구 | 오프라인 백테스트, 현금·BH40 기준선(P29), 가상 청산 감사(P28), 공통 사전등록 러너(P35) |
| 과거 데이터 | P36 다년치 분봉 수집기와 개발 구간 로더는 코드와 합성 검증까지 준비됐습니다. 실수집과 데이터 품질 확인(#76)은 아직입니다. |
| 전략 | 기존 봇의 지표 복합 전략(Legacy)과 연구 후보(RSI·MACD·볼린저 단독, 2/3·3/3 합의). 운영에 채택한 후보는 없습니다. |
| 실거래 | 없음. 인증 주문 어댑터는 `REAL_TRADING` 게이트 뒤에 있고 꺼져 있습니다. |

## 요구 사항과 설치

- **Python 3.12**: CI가 macOS Python 3.12로 전체 테스트를 실행합니다. 운영 기준 OS는 macOS이며, Linux/Windows는 필수 검증 범위가 아닙니다.
- 의존성은 [`requirement.txt`](requirement.txt)에 있습니다.

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/python -m pip install -r requirement.txt
```

**API 키나 Slack 토큰은 필요 없습니다.** 아래 빠른 시작과 전체 테스트는 키 없이 실행됩니다.
- `config.py`는 `.env`가 있으면 읽지만, 연구와 테스트에는 쓰이지 않습니다.
- `.env`는 저장소에 커밋하지 않습니다.
- 명령 앞에 `PYTHON_DOTENV_DISABLED=1`을 붙이면 `.env`를 읽지 않습니다.

## 빠른 시작 (오프라인, 키·네트워크 없음)

전체 오프라인 테스트:

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m unittest discover
```

안전 검사(PAPER 모드, 민감 파일):

```bash
.venv/bin/python tools/check_safety.py
```

합성 데이터로 전략을 비교합니다. 출력 경로는 새 디렉터리여야 합니다. `runtime/`은 git이 무시합니다.

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting --demo --fee-bps 5 --slippage-bps 5 --spread-bps 2 --output runtime/research-demo
```

- 비용 숫자는 연구 가정이며, 현재 업비트 요율이 아닙니다.
- 결과는 합성 데이터이므로 수익성의 근거가 아닙니다.
- 정의와 한계는 [연구 설계](docs/STRATEGY_RESEARCH.md)를 참고하세요.

P36 수집 계획을 확인합니다. 출력만 하고 네트워크 호출이나 파일 생성은 없습니다.

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.p36_history plan
```

## 상태를 바꾸는 명령 — 기본 실행 단계가 아닙니다

아래 명령은 다음 중 하나 이상을 합니다: 공개 시세 접속, PAPER 계좌·원장 생성, 수집 파일 쓰기. 각 안내 문서의 절차와 사용자 승인에 따라서만 실행합니다. 실거래는 어떤 경우에도 하지 않습니다.

| 목적 | 진입점 | 안내 |
|---|---|---|
| 감독하 PAPER(신규 실험 계좌) | `python -m ops.paper_bot {init,check,run,status,stop,report}` | [P12 PAPER 봇](docs/P12_PAPER_BOT.md), [P26 수동 재개](docs/P26_MANUAL_PAPER_CONTINUATION.md), [P0 안전](docs/P0_SAFETY.md) |
| P36 다년치 분봉 수집(수동, holdout 분리) | `python -m backtesting.p36_history {collect,resume,verify}` | [P36 스냅샷](docs/P36_HISTORY_SNAPSHOT.md), [P36 개발 구간 로더](docs/P36_DEV_LOADER.md) |
| 사전등록 평가 상태·실행 | `python -m backtesting.p35_registered_eval {status,analyze}` | [P35 러너](docs/P35_REGISTERED_EVAL.md) |
| P22 고정 연구 1회 실행 | `python -m ops.research_run p22` | [P31](docs/P31_RESEARCH_RUN.md), [P22](docs/P22_FROZEN_FORWARD_VALIDATION.md) |
| 15/60분 고정 스냅샷 연구(2026-10-21) | `python -m backtesting.historical {collect,evaluate}` | [과거 데이터 연구](docs/HISTORICAL_RESEARCH.md) |
| 기존 봇(`main.py`, PAPER) | `python main.py` | [P10C PAPER 운영](docs/P10C_PAPER_OPERATIONS.md), [P0 안전](docs/P0_SAFETY.md) |

`main.py`에 대하여:
- 공개 시세로 거래대금 상위 종목을 고른 뒤, PAPER 엔진으로 기존 봇을 **무기한 루프**로 실행합니다.
- 상태 파일과 로그를 씁니다. 시간 제한이나 오프라인 기본값이 없으므로 빠른 시작에 넣지 않았습니다.
- 감독하 PAPER는 `ops.paper_bot` 절차를 우선 사용합니다.

## 기존 봇의 신호와 위험 정책 (현재 코드 기준)

**신호** (`data_analyzer/analyzer.py`)
- RSI·MACD·볼린저 지표를 함께 보는 복합(Legacy) 신호입니다.
- 거래량 필터와 신호 쿨다운 300초가 있습니다.
- 분석기는 매수·매도·보유 결정만 내고, 목표가는 정하지 않습니다(`target_price=None`).

**위험** (`trading/auto_trade.py`, `config.py`)
- 평균가 대비 +5%에서 익절
- 평균가 대비 −5%에서 현재 평가액의 50%로 1회 물타기
  - 현금 부족 등으로 물타기가 안 되면 그 자리에서 손절합니다.
  - 물타기 후에는 새 평균가 대비 −5%에서 손절합니다.
- 동시 보유 최대 2종목, 종목당 시작금의 40%, 최소 주문 5,000원

**변경 규칙**
- 신호, 조건 순서, 거래량 필터, 쿨다운, 위험 정책을 바꾸면 전략 변경입니다. 사용자 승인이 필요합니다.
- 상세 동작과 복구 한계는 [P0 안전](docs/P0_SAFETY.md)을 참고하세요.

## 프로젝트 구조

```
auto_trade/
├── main.py                 # 기존 봇 진입점 (PAPER; REAL_TRADING=False)
├── config.py               # 거래 모드·자금·위험 설정
├── requirement.txt         # 의존성
├── trading/                # AutoTrade(위험 정책), 체결 엔진·비용 모델, 상태 저장
├── data_analyzer/          # 지표 계산과 기존 복합 신호
├── strategies/             # 연구용 전략(RSI·MACD·볼린저·합의·Legacy)
├── services/               # 공개 시세·스트림, PAPER 원장 지표(P9), 알림(기본 꺼짐), 인증 브로커 어댑터
├── utils/                  # 데코레이터, 메시지 큐, 프로세스 잠금, Slack 사전 점검
├── backtesting/            # 백테스트 엔진, 사전등록 평가(P22/P33/P35), 감사 도구, P36 수집기·로더
├── ops/                    # PAPER CLI(paper_bot), 수동 재개·복구, 연구 실행(research_run), 관측 도구
├── tools/                  # 안전 검사, P22 격리 실행, 요약 도구
├── automation/             # 개발 자동화 Fake-only 원장·관측(비활성)
├── research/               # 보존된 연구 스냅샷과 결과(봉인 구간 포함, 수정 금지)
├── tests/                  # 오프라인 테스트(가짜 HTTP·계좌만 사용)
└── docs/                   # 목표·로드맵·불변 원칙·단계별 설계 문서
```

## 단계별 문서

| 분야 | 문서 |
|---|---|
| 목표·계획·규칙 | [PROJECT_GOAL](docs/PROJECT_GOAL.md), [ROADMAP](docs/ROADMAP.md), [AGENTS](AGENTS.md), [PROJECT_INVARIANTS](docs/PROJECT_INVARIANTS.md) |
| PAPER 안전·회계 | [P0 안전](docs/P0_SAFETY.md), [P9 회계](docs/P9_PERFORMANCE_ACCOUNTING.md), [P17 비용 체결](docs/P17_PAPER_EXECUTION_COSTS.md), [P25 입력 격리](docs/P25_PAPER_INPUT_QUARANTINE.md), [P26 수동 재개](docs/P26_MANUAL_PAPER_CONTINUATION.md) |
| 전략 연구 | [연구 설계](docs/STRATEGY_RESEARCH.md), [P32](docs/P32_CONSENSUS_COST_COMPARISON.md), [P33](docs/P33_CONSENSUS_FORWARD_PROPOSAL.md), [P34](docs/P34_SHARED_CASH.md), [P35](docs/P35_REGISTERED_EVAL.md) |
| 과거 데이터 | [과거 데이터 연구](docs/HISTORICAL_RESEARCH.md), [P36 스냅샷](docs/P36_HISTORY_SNAPSHOT.md), [P36 개발 구간 로더](docs/P36_DEV_LOADER.md) |
| 개발 자동화(비활성) | [제안](docs/DEVELOPMENT_AUTOMATION.md), [P1 원장](docs/P1_LEDGER.md), [P2 읽기 전용 관측](docs/P2_GITHUB_READONLY.md), [P3 로컬 Worker](docs/P3_LOCAL_WORKER.md), [P4 리뷰·병합 게이트](docs/P4_REVIEW_MERGE_GATE.md) |

## 안전 경계와 역할

**안전 경계**
- `REAL_TRADING=False`를 유지합니다.
- 추가 지출은 0원입니다. 유료 API·모델·서버·runner를 쓰지 않습니다.
- 미확정 주문이 있거나 원장이 맞지 않으면 재제출하지 않고 멈춥니다. 상태 파일을 지워 복구 검사를 우회하지 않습니다.
- 기존·HALTED 계좌와 원장, 백업은 보존합니다.
- 키, 토큰, 세션 정보는 커밋하거나 출력하지 않습니다.
- Slack 알림(`SLACK_NOTIFICATIONS_ENABLED=False`), launchd, 예약 실행, 자동 병합은 꺼져 있습니다.

**역할** ([AGENTS](AGENTS.md))
- Claude Code: 구현, 테스트, PR 작성
- 별도 ChatGPT 대화: 검토와 인계. 위임된 일반 PR은 조건을 충족하면 한 건씩 직접 병합할 수 있습니다.
- 사용자: 그 밖의 PR을 수동으로 병합합니다.
- Claude Code는 직접 병합하지 않습니다.

## 라이선스

MIT License
