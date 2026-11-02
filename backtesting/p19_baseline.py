"""Fixed 18-case development baseline; no strategy sweep or live account access."""
import argparse
from contextlib import redirect_stdout,redirect_stderr
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import logging
import multiprocessing as mp
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

from . import minute_study as minute
from .legacy_diagnostic import prepare,simulate

from .engine import serializable
from trading.costs import CostModel,policy
from trading.execution import ExecutionEngine,number
from services.notification_metrics import close

PROTOCOL=Path(__file__).with_name('p19_protocol.json')
PROTOCOL_SHA256='641eabeb1d3fb46a2ab9eddcb61c5f3aa70033dfd6941ff8611a07a501682b36'
DAYS=('2026-10-30','2026-10-31','2026-11-01')
MARKETS=('KRW-BTC','KRW-XRP')
COSTS=((0,0,0),(5,5,2),(10,10,2))
MAX_SECONDS=600


def write_json(path,value):
    # Atomic snapshot: terminating a worker never destroys the last finished cases.
    path=Path(path);temp=path.with_suffix('.pending')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def day_start(day):return minute.utc(day+'T00:00:00+00:00')


def require(ok,reason):
    if not ok:raise ValueError(reason)


def protocol_hash():
    # Git may check out identical JSON with CRLF on Windows. Content stays pinned.
    return hashlib.sha256(PROTOCOL.read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def protocol():
    require(protocol_hash()==PROTOCOL_SHA256,'PROTOCOL_HASH_MISMATCH')
    require(minute.MAX_CALLS==40 and minute.DEADLINE_SECONDS==120,'COLLECTION_LIMIT_MISMATCH')
    value=json.loads(PROTOCOL.read_text())
    require(value['days']==list(DAYS) and value['markets']==list(MARKETS) and value['cost_cases_bps']==[list(x) for x in COSTS]
            and value['max_runs']==18 and value['analysis_seconds']==600,'PROTOCOL_MISMATCH')
    return value


def safe_path(value):
    p=Path(value).expanduser().absolute()
    require(not any(x.is_symlink() for x in (p,*p.parents)),'SYMLINK_FORBIDDEN')
    p=p.resolve()
    require(not p.is_relative_to(minute.ROOT),'PRIVATE_OUTPUT_OUTSIDE_CHECKOUT_REQUIRED')
    require(not any('holdout' in x.name.lower() or x.name.lower() in ('runtime','.git','.ssh','.codex','paper-experiments','notifications')
                    or any((x/n).exists() for n in ('experiment.json','paper-state.json','experiment-state.json','session.json'))
                    for x in (p,*p.parents)),'ACCOUNT_OR_HOLDOUT_PATH_FORBIDDEN')
    return p


def fresh(value):
    p=safe_path(value);require(not p.exists(),'OUTPUT_EXISTS_NO_OVERWRITE')
    return minute.new_output(p,collection=True)


class P17ResearchEngine(ExecutionEngine):
    """Only clock/trace instrumentation; receipt, cap, minimum and fill paths are P17."""
    def __init__(self,path,cash,costs,clock,*,capture_details=False):
        from ops import paper_experiment as exp
        account=(Path(path).parent/'account').resolve()
        require(number(cash)==1000000,'FIXED_CASE_REQUIRED')
        require(exp.create(account,cost_policy=policy(costs))['status']=='CREATED_NEW_INDEPENDENT_EXPERIMENT','DISPOSABLE_ACCOUNT_CREATION_FAILED')
        super().__init__(account/exp.STATE,cash,real_trading=False)
        self._experiment_session=exp.begin(account)
        self.capture_details=capture_details;self.detail_fills=[];self.signal_at=None
        self.clock=clock;self.costs=costs;self.slippage=Decimal(0);self.spread=Decimal(0);self.trace=[]
    def _apply_fill(self,order,receipt):
        with patch('trading.execution.time',SimpleNamespace(time=self.clock)):
            filled=super()._apply_fill(order,receipt)
        if filled:
            q=number(order['executed_volume']);reference=number(order['price_hint'])
            self.slippage+=q*reference*self.costs.slippage_bps/10000
            self.spread+=q*reference*self.costs.spread_bps/20000
            if self.capture_details:
                from .trade_attribution import fill_detail
                self.detail_fills.append(fill_detail(order,reference,self.costs,self.signal_at))
            self.trace.append({'side':order['side'],'executed_at':self.clock(),'price':order['average_fill_price'],
                               'quantity':str(q),'fee':order['paid_fee']})
        return filled


def buy_hold(frame,result,costs):
    if result['execution_first_open'] is None:return None
    stamp=minute.utc(result['execution_first_open']);reference=number(frame.loc[stamp,'open'])
    with tempfile.TemporaryDirectory(prefix='p19-bh-') as temp:
        engine=P17ResearchEngine(Path(temp)/'unused.json',1000000,costs,lambda:stamp.timestamp())
        engine.open()
        try:
            require(engine.buy(result['market'],reference,400000),'BUY_HOLD_REJECTED')
            equity=engine.cash+number(engine.position(result['market'])['quantity'])*number(frame.close.iloc[-1])
            return str((equity/1000000-1)*100)
        finally:engine.close()


def case(frame,windows,market,cost,*,details=False):
    c=CostModel(*cost)
    r=simulate(frame,windows,market=market,bar_seconds=60,costs=c,evaluation_start=frame.index[199],
               engine_factory=P17ResearchEngine,sample_opens=True,details=details)
    r['execution_last_open']=(frame.index[frame.index.get_loc(minute.utc(r['evaluation_last_open']))+1].isoformat() if r['evaluation_last_open'] else None)
    r['source']='NEW_DEVELOPMENT_SAMPLE_NOT_INDEPENDENT_VALIDATION'
    r['metrics']['buy_hold_40_net_pct']=buy_hold(frame,r,c)
    r['metrics'].pop('buy_hold_100_net_pct',None)
    if r['fills']['buys']+r['fills']['sells']:
        require(close(Decimal(r['metrics']['net_realized_pnl'])+Decimal(r['metrics']['unrealized_pnl']),
                      Decimal(r['final_equity'])-1000000),'PNL_RECONCILIATION_FAILED')
    r['mdd_basis']='MINUTE_OPEN_AFTER_DECISION_AND_NEXT_CLOSE_NOT_INTRABAR'
    return r


def collect(output,*,connect_public=False):
    protocol()
    if not connect_public:return {'status':'OFFLINE_PLAN_ONLY','days':DAYS,'markets':MARKETS,'max_requests':120,'network_calls':0}
    p=fresh(output);results=[]
    for day in DAYS:
        end=(day_start(day)+minute.pd.Timedelta(days=1)).isoformat()
        results.append(minute.collect(p/day,list(MARKETS),day_start(day).isoformat(),end,connect_public=True))
        write_json(p/'collection.json',results)
    complete=all(r['status']=='COLLECTED_DEVELOPMENT_SAMPLE' for r in results)
    if complete:(p/'INCOMPLETE').unlink()
    return {'status':'COLLECTED' if complete else 'INSUFFICIENT_REAL_DATA','days':results,
            'network_calls':sum(r.get('network_calls',0) for r in results)}


def _work(data,out,pipe):
    report={'status':'INCOMPLETE','protocol':protocol(),'cases':[],'excluded':[],'unfinished':[],
            'protocol_sha256':protocol_hash()}
    write_json(out/'results.json',report)
    with (out/'analysis.log').open('w') as log,redirect_stdout(log),redirect_stderr(log):
        try:
            for day in DAYS:
                try:
                    source=safe_path(data/day);plan=minute.read_json(source/'plan.json')
                    require(plan['markets']==list(MARKETS) and minute.utc(plan['start'])==day_start(day)
                            and minute.utc(plan['end_exclusive'])==day_start(day)+minute.pd.Timedelta(days=1),'FIXED_INPUT_REQUIRED')
                    meta,frames,excluded=minute.load_partitioned_sample(source,quarantine_gaps=True)
                except Exception:
                    report['excluded'].append({'day':day,'markets':list(MARKETS),'reason':'INVALID_OR_MISSING_DAILY_INPUT'})
                    write_json(out/'results.json',report);continue
                for market in MARKETS:
                    if market not in frames:
                        report['excluded'].append({'day':day,'market':market,'reason':'MISSING_CANDLES','quality':excluded.get(market)})
                        write_json(out/'results.json',report);continue
                    frame,windows=prepare(frames[market],60)  # Once per market/day, never per cost.
                    for costs in COSTS:
                        r=case(frame,windows,market,costs);r.update(day=day,input_quality=meta['datasets'][market]);r.pop('trace',None)
                        report['cases'].append(r);write_json(out/'results.json',report)
                        pipe.send({'finished_cases':len(report['cases']),'maximum_cases':18})
            report['status']='COMPLETE' if len(report['cases'])==18 else 'PARTIAL_REAL_DATA' if report['cases'] else 'INSUFFICIENT_REAL_DATA'
            write_json(out/'results.json',report)
            if report['status']=='COMPLETE':(out/'INCOMPLETE').unlink()
        except Exception as exc:
            import traceback
            report['status']='INCOMPLETE';report['failure_type']=type(exc).__name__
            traceback.print_exc()  # Full diagnostics stay in the private analysis.log.
            write_json(out/'results.json',report)
        finally:pipe.close()


def work(data,out,pipe):
    previous=logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        return _work(data,out,pipe)
    finally:logging.disable(previous)


def summarize(report,elapsed,output):
    paired=[]
    cases=report.get('cases',[])
    for r in cases:
        if r['costs']!={'fee_bps':'5','slippage_bps':'5','spread_bps':'2'}:continue
        zero=next((x for x in cases if x['day']==r['day'] and x['market']==r['market'] and x['costs']=={'fee_bps':'0','slippage_bps':'0','spread_bps':'0'}),None)
        if zero and r['metrics']['net_return_pct'] is not None and zero['metrics']['net_return_pct'] is not None:
            paired.append(Decimal(r['metrics']['net_return_pct'])<Decimal(zero['metrics']['net_return_pct']))
    rows=[{'day':r['day'],'market':r['market'],'fills':r['fills'],'net_pct':r['metrics']['net_return_pct'],
           'fees':r['fees'],'mdd_pct':r['metrics']['mdd_pct'],'buy_hold_40_pct':r['metrics']['buy_hold_40_net_pct']}
          for r in report.get('cases',[]) if r['costs']=={'fee_bps':'5','slippage_bps':'5','spread_bps':'2'}]
    present={(r['day'],r['market']) for r in rows}
    rows += [{'day':d,'market':m,'status':'N/A_INVALID_OR_UNFINISHED'} for d in DAYS for m in MARKETS if (d,m) not in present]
    return {'status':report['status'],'elapsed_seconds':round(elapsed,3),'finished_cases':len(report.get('cases',[])),
            'assessment':{'paired_cases':len(paired),'primary_worse_than_zero':sum(paired),
                'limit':'Development only; costs may change execution paths; no causal or profitability claim',
                'next_hypotheses':['Check whether losses precede fees on unchanged signals','Check exposure and exit timing on another predeclared sample']},
            'excluded_inputs':len(report.get('excluded',[])),'primary_rows':rows,'output':str(output)}


def analyze(data,output,*,seconds=MAX_SECONDS,worker=work):
    protocol()
    require(type(seconds) is int and 1<=seconds<=MAX_SECONDS,'TIME_LIMIT')
    data=safe_path(data);out=fresh(output);started=time.monotonic()
    ctx=mp.get_context('spawn');receive,send=ctx.Pipe(duplex=False)
    process=ctx.Process(target=worker,args=(data,out,send));process.start();send.close()
    interrupted=False;progress_open=True
    try:
        while process.is_alive() and time.monotonic()-started<seconds:
            message=None
            if progress_open:
                try:
                    if receive.poll(.1):message=receive.recv()
                except (EOFError,OSError):
                    # Windows poll can raise BrokenPipeError after child closure.
                    # Progress is advisory; process exit and atomic results decide success.
                    progress_open=False
            if message is not None:print(json.dumps(message),flush=True)
            process.join(.1)
    except KeyboardInterrupt:interrupted=True
    finally:
        timed_out=process.is_alive()
        if timed_out:process.terminate();process.join(2)
        if process.is_alive():process.kill();process.join(2)
        receive.close()
    path=out/'results.json';report=minute.read_json(path) if path.exists() else {'cases':[],'excluded':[],'status':'INCOMPLETE'}
    if timed_out or interrupted or process.exitcode!=0:report['status']='INCOMPLETE';(out/'INCOMPLETE').touch()
    done={(r['day'],r['market'],tuple(r['costs'][k] for k in ('fee_bps','slippage_bps','spread_bps'))) for r in report['cases']}
    report['unfinished']=[{'day':d,'market':m,'cost_bps':list(c)} for d in DAYS for m in MARKETS for c in COSTS
                          if (d,m,tuple(map(str,c))) not in done]
    report['elapsed_seconds']=time.monotonic()-started;write_json(path,report)
    brief=summarize(report,report['elapsed_seconds'],out);write_json(out/'summary.json',brief)
    return brief


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('collect','analyze'))
    parser.add_argument('--output',required=True);parser.add_argument('--data');parser.add_argument('--connect-public',action='store_true')
    a=parser.parse_args(argv)
    if a.command=='analyze' and (not a.data or a.connect_public):parser.error('explicit offline data required')
    if a.command=='collect' and a.data:parser.error('collection has no data input')
    try:r=collect(a.output,connect_public=a.connect_public) if a.command=='collect' else analyze(a.data,a.output)
    except Exception:r={'status':'INCOMPLETE','reason':'INPUT_OR_EXECUTION_UNVERIFIED'}
    print(json.dumps(serializable(r),ensure_ascii=False));return 0 if r['status'] in ('COMPLETE','COLLECTED','OFFLINE_PLAN_ONLY') else 2


if __name__=='__main__':raise SystemExit(main())
