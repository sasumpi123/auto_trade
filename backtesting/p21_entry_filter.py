"""One predeclared entry-filter hypothesis; offline P19 development pairs only."""
import argparse
import csv
import hashlib
import logging
import re
from contextlib import redirect_stdout,redirect_stderr
from decimal import Decimal

from . import p19_baseline as base
from . import p20_attribution as p20
from . import minute_study as minute
from .legacy_diagnostic import ProbeBot
from services.notification_metrics import decimal
from trading.execution import number

THRESHOLD_BPS=Decimal('44')
POLICIES=('legacy','entry_bandwidth_44bps')


def hypothesis():
    return {'formula':'(bb_upper-bb_lower)/completed_close*10000',
        'block_if_bandwidth_bps_less_than':'44','equality':'ALLOW',
        'scope':'FLAT_FIRST_BUY_ONLY_AFTER_UNCHANGED_SIGNAL_AND_COOLDOWN',
        'basis':'Unverified hypothesis: twice approximate 22bps round-trip primary costs; not expected price range',
        'optimization':False,'independent_validation':False,'operational_adoption':False}


def bandwidth_bps(row):
    upper=decimal(row['bb_upper'],signed=True);lower=decimal(row['bb_lower'],signed=True);close=number(row['close'],positive=True)
    base.require(upper>=lower,'INVALID_COMPLETED_BB_BAND')
    return (upper-lower)/close*10000


class EntryFilterBot(ProbeBot):
    """Research-only hook after AutoTrade saves the original signal/cooldown.

    Never substitute a signal or intercept SELL/averaging/risk management.
    Only the already prepared completed analyzer row is visible to this hook.
    """
    entry_filter_enabled=True

    def initialize(self):
        super().initialize()
        if self.entry_filter_enabled:
            for key in ('entry_filter_checks','entry_filter_blocked','entry_filter_allowed'):
                self.diagnostic_counts.setdefault(key,0)

    def buy_coin(self,ticker,current_price,reason=None,target_price=None,amount=None):
        if self.entry_filter_enabled and amount is None and number(self.engine.position(ticker)['quantity'])==0:
            c=self.diagnostic_counts;c['entry_filter_checks']+=1
            width=bandwidth_bps(self.analyzers[ticker].df.iloc[-1])
            if width<THRESHOLD_BPS:
                c['entry_filter_blocked']+=1
                return False
            c['entry_filter_allowed']+=1
        return super().buy_coin(ticker,current_price,reason,target_price,amount)


def compact(result,detail,*,filtered):
    """P9-authoritative totals; open inventory is not a fabricated terminal sale."""
    base.require(result['accounting_verified'] is True and detail['verified'] is True,'P21_P9_UNVERIFIED')
    position=detail['open_position'];equity=number(result['final_equity']);initial=number(result['initial_cash'])
    net=decimal(detail['net_realized_pnl'],signed=True);unrealized=decimal(detail['unrealized_pnl'],signed=True)
    base.require(base.close(net+unrealized,equity-initial),'P21_TOTAL_PNL_MISMATCH')
    trades=sum(result['fills'].values());counts=result['counts']
    return {'status':'TRADES' if trades else 'NO_TRADES','net_return_pct':result['metrics']['net_return_pct'],
        'fills':result['fills'],'fees':result['fees'],'mdd_pct':result['metrics']['mdd_pct'],
        'holding_fraction':result['exposure_fraction'],'holding_basis':'HELD_MINUTE_OPEN_SAMPLES_OVER_EVALUATED_OPENS',
        'time_held_pct':detail['exposure']['position_held_time_pct'],
        'mean_capital_exposure_pct':detail['exposure']['mean_marked_notional_over_initial_pct'],
        'realized_pnl':str(net),'unrealized_pnl':str(unrealized),'total_pnl':str(net+unrealized),
        'final_equity':str(equity),'cash':str(equity-number(position['quantity'])*number(position['mark_price'])),
        'open_position':position,'p9_reconciled':True,
        'entry_filter':{'enabled':filtered,'threshold_bps':'44' if filtered else None,
            'checks':counts.get('entry_filter_checks',0),'blocked':counts.get('entry_filter_blocked',0),
            'allowed':counts.get('entry_filter_allowed',0)},
        'costs':result['costs'],'slippage_cost':result['slippage_cost'],'half_spread_cost':result['half_spread_cost']}


def baseline_case(frame,windows,old):
    detail=p20.matched_case(frame,windows,old)  # Exact whole P19 case match, not just rounded return.
    return compact(old,detail,filtered=False)


def candidate_case(frame,windows,old):
    result=base.case(frame,windows,old['market'],(5,5,2),details=True,bot_factory=EntryFilterBot)
    for key in ('market','bar_seconds','input_candles','warmup_window','terminal_candles_without_next_open',
                'data_sha256','evaluation_first_open','evaluation_last_open','execution_first_open','execution_last_open','conditions'):
        base.require(result[key]==old[key],'P21_COMPARISON_WINDOW_MISMATCH')
    return compact(result,result['attribution'],filtered=True)


def comparison(day,market,baseline,candidate):
    no_trades='NO_TRADES' in (baseline['status'],candidate['status'])
    delta=None if no_trades else str(decimal(candidate['net_return_pct'],signed=True)-decimal(baseline['net_return_pct'],signed=True))
    return {'day':day,'market':market,'baseline':baseline,'candidate':candidate,
        'status':'NO_TRADES' if no_trades else 'DEVELOPMENT_COMPARISON',
        'delta_net_percentage_points':delta,
        'interpretation':'No profitability proof, portfolio aggregation, optimization or automatic adoption'}


def work(source,out,pipe):
    prior=logging.root.manager.disable
    report={'schema':'P21_FIXED_44BPS_V1','status':'INCOMPLETE','hypothesis':hypothesis(),
        'maximum_cases':12,'network_calls':0,'cases':[],'completed_policies':[],'excluded':[]}
    base.write_json(out/'results.json',report)
    try:
        logging.disable(logging.CRITICAL)
        with (out/'analysis.log').open('w') as log,redirect_stdout(log),redirect_stderr(log):
            root,_,primary,digest=p20.inputs(source)
            report.update(p19_results_sha256=digest,protocol_sha256=base.protocol_hash())
            report['excluded']=[{'day':d,'market':m,'reason':'P19_BASELINE_EXCLUDED'}
                for d in base.DAYS for m in base.MARKETS if (d,m) not in primary]
            base.write_json(out/'results.json',report)
            for day in base.DAYS:
                selected=[m for m in base.MARKETS if (day,m) in primary]
                if not selected:continue
                daily=base.safe_path(root/'data'/day)
                plan,plan_hash=p20.metadata(daily/'plan.json');_,manifest_hash=p20.metadata(daily/'manifest.json')
                base.require(plan['markets']==list(base.MARKETS) and minute.utc(plan['start'])==base.day_start(day)
                    and minute.utc(plan['end_exclusive'])==base.day_start(day)+minute.pd.Timedelta(days=1),'P19_PERIOD_MISMATCH')
                meta,frames,_=minute.load_partitioned_sample(daily,quarantine_gaps=True)
                report.setdefault('input_hashes',{})[day]={'manifest':manifest_hash,'plan':plan_hash,
                    'files':{m+'-1.csv':meta['datasets'][m]['file_sha256'] for m in selected}}
                for market in selected:
                    old=primary[(day,market)]
                    base.require(market in frames and old['input_quality']==meta['datasets'][market],'P19_INPUT_MISMATCH')
                    frame,windows=base.prepare(frames[market],60)  # Shared prefix-only preparation, never per policy.
                    baseline=baseline_case(frame,windows,old)
                    report['completed_policies'].append({'day':day,'market':market,'policy':POLICIES[0],'result':baseline})
                    base.write_json(out/'results.json',report)
                    candidate=candidate_case(frame,windows,old)
                    report['completed_policies'].append({'day':day,'market':market,'policy':POLICIES[1],'result':candidate})
                    report['cases'].append(comparison(day,market,baseline,candidate))
                    base.write_json(out/'results.json',report)
            for day,hashes in report.get('input_hashes',{}).items():
                for name,digest0 in {'manifest.json':hashes['manifest'],'plan.json':hashes['plan'],**hashes['files']}.items():
                    base.require(hashlib.sha256((root/'data'/day/name).read_bytes()).hexdigest()==digest0,'P19_INPUT_CHANGED')
            base.require(hashlib.sha256((root/'analysis/results.json').read_bytes()).hexdigest()==digest,'P19_INPUT_CHANGED')
            base.require(len(report['completed_policies'])==2*len(primary)<=12,'P21_CASE_LIMIT')
            report['status']='VERIFIED_PARTIAL' if report['excluded'] else 'VERIFIED'
            base.write_json(out/'results.json',report);(out/'INCOMPLETE').unlink()
    except Exception as exc:
        code=str(exc);report.update(status='INCOMPLETE',failure_type=type(exc).__name__,
            failure_reason=code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',code) else 'INPUT_OR_REPLAY_UNVERIFIED')
        base.write_json(out/'results.json',report)
    finally:logging.disable(prior);pipe.close()


def write_report(out,report):
    done={(r['day'],r['market'],r['policy']) for r in report.get('completed_policies',[])}
    excluded={(r['day'],r['market']) for r in report['excluded']}
    report['unfinished_policies']=[{'day':d,'market':m,'policy':p} for d in base.DAYS for m in base.MARKETS
        if (d,m) not in excluded for p in POLICIES if (d,m,p) not in done]
    base.write_json(out/'results.json',report)
    fields=('net_return_pct','fees','mdd_pct','holding_fraction','time_held_pct','mean_capital_exposure_pct',
            'realized_pnl','unrealized_pnl','total_pnl','cash','p9_reconciled')
    records=[];lines=['# P21 fixed 44bps development comparison','',report['status'],
        'Not independent validation or profitability evidence. Returns are never summed across markets/days.']
    def display(value):return 'N/A' if value is None else str(value)
    for row in report['cases']:
        a,b=row['baseline'],row['candidate'];record={'day':row['day'],'market':row['market'],'status':row['status'],
            'delta_net_percentage_points':row['delta_net_percentage_points']}
        lines+=['',row['day']+' '+row['market']+' — '+row['status'],
            '|Metric|Baseline|44bps entry filter|','|---|---:|---:|']
        for field in fields:
            record['baseline_'+field]=a[field];record['candidate_'+field]=b[field]
            lines.append('|'+field+'|'+display(a[field])+'|'+display(b[field])+'|')
        for label,get in (('buys',lambda r:r['fills']['buys']),('sells',lambda r:r['fills']['sells']),
                          ('entry_filter_blocked',lambda r:r['entry_filter']['blocked']),
                          ('remaining_quantity',lambda r:r['open_position']['quantity']),
                          ('remaining_cost',lambda r:r['open_position']['remaining_cost'])):
            record['baseline_'+label]=get(a);record['candidate_'+label]=get(b)
            lines.append('|'+label+'|'+display(get(a))+'|'+display(get(b))+'|')
        lines.append('Delta net percentage points: '+display(row['delta_net_percentage_points']))
        records.append(record)
    lines+=['',f"Completed policies: {len(done)}/12 maximum; excluded inputs: {len(excluded)}; unfinished policies: {len(report['unfinished_policies'])}.",
        'NO_TRADES is not an improvement. Lower loss in this sample is not proof of profitability.',
        'Partial policy results, fixed hypothesis and evidence hashes: results.json.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    with (out/'comparison.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]) if records else ['day','market','status'])
        writer.writeheader();writer.writerows(records)


def analyze(source,output,*,seconds=600,worker=work):
    return p20.analyze(source,output,seconds=seconds,worker=worker,report_writer=write_report)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        _,report=analyze(args.input,args.output)
        print(report['status'],'pairs:',len(report['cases']),'policies:',len(report.get('completed_policies',[])),
              'excluded:',len(report['excluded']),'seconds:',report['elapsed_seconds'])
        def pct(value):return 'N/A' if value is None else f'{Decimal(value):.4f}'
        for r in report['cases']:
            print(r['day'],r['market'],r['status'],
                'net%:',pct(r['baseline']['net_return_pct']),'→',pct(r['candidate']['net_return_pct']),
                'delta pp:',pct(r['delta_net_percentage_points']),'blocked:',r['candidate']['entry_filter']['blocked'])
        print('Development comparison only; NO_TRADES is not improvement; no automatic adoption.')
        print('Source:',base.safe_path(args.input))
        return 0 if report['status'] in ('VERIFIED','VERIFIED_PARTIAL') else 2
    except Exception:print('INCOMPLETE: input or comparison unverified');return 2


if __name__=='__main__':raise SystemExit(main())
