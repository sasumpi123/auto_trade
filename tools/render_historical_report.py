"""Render every fixed-protocol result; never rank, select, or tune candidates."""
import argparse
from decimal import Decimal
import json
from pathlib import Path


def number(value):
    return '—' if value is None else f'{Decimal(str(value)):.3f}'


def primary_table(report, split):
    lines = ['| 마켓·분봉 | 전략 | 순수익률 % | MDD % | 승률 % | 종료 왕복 | 체결 수 |',
             '|---|---|---:|---:|---:|---:|---:|']
    for key, dataset in report['datasets'].items():
        for row in dataset['results']:
            if row['split'] == split and row['primary']:
                metrics = row['metrics']
                lines.append(f"| {key} | {row['strategy']} | {number(metrics['net_return_pct'])} | "
                             f"{number(metrics['max_drawdown_pct'])} | {number(metrics['win_rate_pct'])} | "
                             f"{metrics['closed_trades']} | {metrics['fill_count']} |")
    return '\n'.join(lines)


def sensitivity_table(report, split):
    lines = ['| 마켓·분봉 | 전략 | 순수익률 최소~최대 % | MDD 최소~최대 % |',
             '|---|---|---:|---:|']
    for key, dataset in report['datasets'].items():
        for strategy in report['protocol']['strategies']:
            rows = [row for row in dataset['results'] if row['split'] == split and row['strategy'] == strategy]
            def span(metric):
                values = [Decimal(row['metrics'][metric]) for row in rows]
                return f'{number(min(values))} ~ {number(max(values))}'
            lines.append(f"| {key} | {strategy} | {span('net_return_pct')} | {span('max_drawdown_pct')} |")
    return '\n'.join(lines)


def render(report):
    if len(report['datasets']) != 8 or any(len(x['results']) != 96 for x in report['datasets'].values()):
        raise ValueError('Incomplete study: require all 8 datasets and 768 results')
    lines = ['# 실제 과거 OHLCV 백테스트 결과 — 2026-10-21', '',
             '실제 업비트 공개 데이터의 **학습·검증** 결과입니다. 미사용 테스트는 평가하지 않았습니다. '
             '고정된 최근 90일 중 학습 54일·검증 18일이며, 남은 18일은 봉인했습니다. '
             '이 짧은 구간 결과로 운영 성능이나 향후 수익성을 주장하지 않습니다.', '',
             '수집/품질/분할/체결/비용/노출 가정은 [설계 문서](../../docs/HISTORICAL_RESEARCH.md), '
             '원본 정규화 스냅샷과 요청 감사는 [data/](data/), 정확한 전체 수치는 '
             '[metrics.csv](metrics.csv)와 [results.json](results.json)에 있습니다.', '',
             '기본 표시는 편도 수수료 5bps·슬리피지 5bps·전체 스프레드 2bps입니다. '
             '각 전략은 독립 초기 자금 100만원, 고정 40% 노출입니다. buy_hold_100만 100% 노출입니다. '
             '손절·익절·물타기 없이 신호만 비교합니다. 0거래 수익률 0%를 전략 우수성으로 해석하지 않습니다.', '',
             '승률 —는 종료 왕복 거래가 없음을 뜻합니다. 체결 수는 매수+매도, 종료 왕복은 매도 완료 횟수입니다. '
             '최종 보유분은 종가 평가하며 청산 비용은 제외합니다. MDD는 종가 기준입니다.', '',
             '## 실제 관측 가능 범위와 품질', '',
             '| 데이터 | 가장 이른 관측 봉 UTC | 연구 종료 전 마지막 봉 UTC | 수집 봉 | 전체 창 누락 | 개발 구간 품질 |',
             '|---|---|---|---:|---:|---|']
    for key, dataset in report['datasets'].items():
        quality, available = dataset['quality'], dataset['availability']
        lines.append(f"| {key} | {available['earliest_observed']} | {available['latest_completed_observed']} | "
                     f"{quality['rows']} | {quality['missing']} | 통과 |")
    lines += ['', '확보 가능 범위는 API 존재 탐색이며 전체 역사 연속성을 검증한 범위가 아닙니다. '
              '전체 90일 창은 테스트 구간의 누락 때문에 부적합입니다. 학습·검증 접두 구간만 엄격 검사에 통과하여 평가했습니다. '
              '누락 원인은 미확정이며 채우거나 평가하지 않았습니다.', '', '## 학습 구간 기본 비용 결과', '',
              primary_table(report, 'train'), '', '## 검증 구간 기본 비용 결과', '', primary_table(report, 'validation'), '',
              '## 학습 구간 비용 민감도 (6조합 전체 범위)', '', sensitivity_table(report, 'train'), '',
              '## 검증 구간 비용 민감도 (6조합 전체 범위)', '', sensitivity_table(report, 'validation'), '',
              '수수료 5/10bps × 슬리피지 0/5/10bps, 전체 스프레드 2bps 고정입니다. '
              '비용은 잔액·최소 주문·수량에 영향을 줄 수 있어 순수익률 변화가 단순 사후 차감과 같지 않습니다. '
              '최소~최대는 6조합 범위이며 최적 비용 또는 선택한 전략이 아닙니다.', '',
              '## 미검증 사항', '',
              '미사용 테스트 결과, 장기/국면별 일반화, 실제 호가·시장 충격·부분 체결·지연, '
              '실제 계정 수수료, 운영 손절·익절·물타기 효과, 다종목 포트폴리오 손익은 검증하지 않았습니다. '
              '학습/검증 성과를 열어 본 상태에서 후속 변경을 반복하면 과최적화가 발생할 수 있습니다. '
              '사용할 수 있는 새 독립 표본과 사전 프로토콜을 먼저 정해야 합니다. '
              '**실제 주문·계정 API 호출·운영 배포는 수행하지 않았습니다.**', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(render(json.loads(args.report.read_text(encoding='utf-8'))), encoding='utf-8')


if __name__ == '__main__':
    main()
