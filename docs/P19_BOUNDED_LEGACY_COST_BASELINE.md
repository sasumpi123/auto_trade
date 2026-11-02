# P19 고정 개발 표본 기준선

Issue #45. protocol은 실자료 수집 전에 별도 커밋9e1a7ea로 고정했다.
BTC/XRP 각각 독립 계좌, UTC 2026-10-30/31/11-01 반열린 하루씩, 1분봉,
0/0/0·5/5/2·10/10/2bps 세 비용 가정만 사용한다. 최대18회이며 후보 전략/최적화는 없다.
비용은 실제 거래소 요율 검증값이 아니다. 기존 train/validation·봉인 holdout은 열지 않는다.

`python -m backtesting.p19_baseline collect --output NEW_PRIVATE_DIR` 기본은 네트워크/파일
생성 없는 계획이다. `--connect-public`에서만 minute_study.collect를 하루씩 호출한다.
기존40회/120초 일별 상한을 확인하며 전체120회, 자동 재시도/종목 대체/보간은 없다.
`analyze --data DATA --output NEW_PRIVATE_DIR`는 네트워크 없는 별도 단계다.
입력의 계획·완료 여부·파일/데이터 SHA·품질은 기존 loader로 재검증한다. 누락 시장은
명시적으로 제외하며 일부 자료를 전체 성공으로 표시하지 않는다. 기존 계좌/holdout/checkout
안의 경로 및 symlink는 거부한다. 기존 출력도 덮어쓰지 않는다.

분석은 stdlib spawn 자식 하나로 직렬 실행하고 부모가600초에 중단한다. 종료 정리에는
최대4초가 추가될 수 있다. 끝난 case마다 원자 JSON과 진행 건수를 남긴다. 중단/timeout은
INCOMPLETE이고 완성된 결과·미완료 목록을 보존한다. 자동 재개는 없다. 상세18개 결과는
results.json, 터미널은 진행18줄 이하와 완료/실측시간/품질·6개 primary 행/경로 요약이다.
private analysis.log와 summary.json도 생성한다. 정상 완료 때만 INCOMPLETE 표시를 없앤다.

기존 prepare의 causal rolling-200을 시장/일별1회(최대6회) 준비하고 비용별 재사용한다.
기존 simulate에 선택적 engine factory/open sampling을 추가했으며 기본 P11 경로는 그대로다.
P19ResearchEngine은 **실제 P17 ExecutionEngine**의 receipt/수수료/Decimal 보정/최소
주문대금/총예산 cap/_apply_fill을 그대로 사용한다. 임시 신규 계좌와 정책을 생성하고
계측용 시각·trace만 감싼다. 연구용 CostPaperEngine의 별도 수수료 공식을 사용하지 않는다.
B&H40%도 같은 P17 경로로 첫 유효 실행 시가에 매수하고 최종 종가로 평가하며 강제 매도하지 않는다. buy_rejected는 P17 순대금 제한 등 엔진 거절을 포함하며,
기존 reject_minimum_order는 총예산 사전 검사 건수이므로 순대금 거절의 정확한 원인별 건수로 해석하지 않는다.

첫200봉은 워밍업이다. 연속1440봉이면 신호 봉 시작03:19~23:58, 실행03:20~23:59 UTC의
1240회 평가다. 실제 유효 지표/평가 범위는 결과 timestamp를 따른다. 3회의24시간 실매매나
일간 수익률을 합성한 한 계좌 곡선으로 해석하지 않는다. 위험관리는 다음 분봉 시가에서
기존 AutoTrade 순서로 확인하므로 실제 틱/봉 내부 체결·P18 WebSocket을 재현하지 않는다.
MDD는 의사결정 후 시가/종가 표본, 노출은 포지션 보유 평가 비중이다.

P9가 매수 수수료 포함 원가와 실현/평가손익·최종 자산을 대사한다. 엔진 평균가는 매수
수수료 제외라는 기존 정의를 유지한다. 슬리피지/스프레드는 가격에 이미 포함돼 다시 빼지 않는다.
무거래 수익성·기대값은 N/A다. 열린 포지션은 남기고 실현/평가손익을 분리한다.
비용 때문에 주문 경로가 달라질 수 있어 비용별 산술 차이를 정확한 손실 기여도로 주장하지 않는다.
세 표본은 새 개발 자료이며 독립 수익성 검증/연수익률/전략 채택 근거가 아니다.

Cloud 실자료 수집·성과는 미검증이다. 적용된 네트워크 정책이 api.upbit.com을 허용하지
않으며 실제 시장 호출 대신 합성 테스트만 수행했다. 테스트 실측과 CI는 PR에 기록한다.
REAL_TRADING=False, 추가 지출0, 기존 계좌/backup 보존, Slack/launchd/무인 실행 OFF.

## 리뷰·병합 뒤에만 사용할 Mac 명령 한 블록

현재 실행 요청이 아니다. fresh private 경로만 생성하며 기존 계좌/연구 출력은 사용하지 않는다.
부분 수집 실패도 분석은 완료된 일별 자료만 보고하도록 한 번 시도한다. 실패 뒤 자동 반복은 없다.

```sh
(
  set -eu
  export PYTHON_DOTENV_DISABLED=1
  umask 077
  cd "$HOME/projects/auto_trade"
  .venv/bin/python tools/check_safety.py
  mkdir -p "$HOME/research"
  study_dir="$(mktemp -d "$HOME/research/p19-private.XXXXXX")"
  printf 'Private outputs: %s\n' "$study_dir"
  set +e
  .venv/bin/python -m backtesting.p19_baseline collect --connect-public \
    --output "$study_dir/data" >"$study_dir/collection.log" 2>&1
  collection_code=$?
  .venv/bin/python -m backtesting.p19_baseline analyze --data "$study_dir/data" \
    --output "$study_dir/analysis" >"$study_dir/analysis-command.log" 2>&1
  analysis_code=$?
  set -e
  .venv/bin/python - "$study_dir/analysis/summary.json" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1])
if not p.exists():
    print('INCOMPLETE: inspect private logs');raise SystemExit(2)
s=json.loads(p.read_text());print(s['status'],s['elapsed_seconds'],'seconds',s['finished_cases'],'/18')
for r in s['primary_rows']:print(r)
print(s['assessment']);print('Outputs:',s['output'])
PY
  test "$collection_code" -eq 0 && test "$analysis_code" -eq 0
)
```

로그·원장·전체 결과를 공개 GitHub로 올리지 않는다. 결과는 그 표본의 기술적 기준선이며
비용이 손실을 악화한 비교 건수와 제한된 다음 가설만 요약한다. 기존 운영 설정은 변경하지 않는다.
