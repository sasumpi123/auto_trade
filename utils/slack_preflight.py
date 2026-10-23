"""Offline, read-only SQLite metadata diagnostics. Does not import config/Slack SDK."""
import ast
from datetime import datetime, timezone
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
STATES={'QUEUED','SENDING','SENT','FAILED','UNKNOWN','RATE_LIMITED'}


def preflight(path=None, *, root=ROOT):
    result={'kind':'slack-offline-preflight','network_calls':0,'message_sent_by_this_tool':False,
            'delivery_notice':'enqueue는 로컬 수락이며 Slack 전달 성공이 아닙니다.',
            'counts':None,'recorded_attempts':None,'queued_is_delivered':False,
            'overall':'BLOCKED','default_off':'PENDING','outbox':'NOT_PROVIDED',
            'history_proves_no_actual_messages':'UNVERIFIABLE_FROM_LOCAL_DATA',
            'user_checks':['Bot chat:write 권한 (토큰 값을 이 도구에 제공하지 마세요)',
                           '초대된 채널 ID와 Bot 멤버십','기존 무료 Slack 이용 조건',
                           '별도 사용자 승인과 단일 소비자; 운영 스케줄 등록 금지']}
    try:
        # AST only: never executes dotenv/config or reads environment/token values.
        tree=ast.parse((root/'config.py').read_text(encoding='utf-8'))
        values=[n.value for n in ast.walk(tree) if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='SLACK_NOTIFICATIONS_ENABLED' for t in n.targets)]
        off=len(values)==1 and isinstance(values[0],ast.Constant) and values[0].value is False
        result['default_off']='PASS' if off else 'FAIL'
        if path is None:return result
        p=Path(path).absolute()
        # Restrict to an explicit database input, never credential/config files.
        if p.suffix not in ('.db','.sqlite','.sqlite3') or p.is_symlink() or not p.is_file() or p.stat().st_size>64_000_000 or p.name.lower().startswith(('auth','credentials','secrets','.env')):
            raise ValueError('unsafe_input')
        if any(part.lower() in {'.codex','.git','.ssh','.aws','.azure','keychains'} for part in p.resolve().parts):
            raise ValueError('unsafe_input')
        if os.name=='posix' and (p.stat().st_uid!=os.getuid() or p.stat().st_mode&0o077):
            raise ValueError('unsafe_input')
        with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
            db.set_progress_handler(lambda:1,5_000_000)
            db.execute('BEGIN')
            if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or db.execute('PRAGMA user_version').fetchone()[0]!=1:
                raise ValueError('schema')
            expected={'messages':['id','channel','text','priority','state','attempts','due'],
                      'sends':['channel','at','day'],'duplicates':['id','repeats']}
            tables=db.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if set(tables)!={(name,'table') for name in expected}:raise ValueError('schema')
            for table,columns in expected.items():
                info=db.execute('PRAGMA table_info('+table+')').fetchall()
                if [r[1] for r in info]!=columns:raise ValueError('schema')
                if [r[5] for r in info]!=([1]+[0]*(len(info)-1) if table in ('messages','duplicates') else [0]*len(info)):raise ValueError('schema')
            rows=db.execute('SELECT id,state,attempts,due FROM messages LIMIT 50001').fetchall()
            if len(rows)>50000 or len({r[0] for r in rows})!=len(rows):raise ValueError('records')
            counts={k:0 for k in sorted(STATES)}
            for id,state,attempts,due in rows:
                if not isinstance(id,str) or not re.fullmatch('[a-f0-9]{64}',id) or state not in STATES or type(attempts) is not int or not 0<=attempts<=2 or not isinstance(due,(int,float)) or not math.isfinite(due):raise ValueError('records')
                if state in ('SENDING','SENT','FAILED','UNKNOWN','RATE_LIMITED') and attempts==0:raise ValueError('records')
                counts[state]+=1
            duplicates=db.execute('SELECT id,repeats FROM duplicates LIMIT 50001').fetchall()
            identities={r[0] for r in rows}
            if len({id for id,_ in duplicates})!=len(duplicates):raise ValueError('duplicates')
            if any(id not in identities or type(n) is not int or n<1 for id,n in duplicates):raise ValueError('duplicates')
            attempts_log=db.execute('SELECT at,day FROM sends LIMIT 100001').fetchall()
            if len(attempts_log)>100000:raise ValueError('attempts_limit')
            days={}
            for at,day in attempts_log:
                if not isinstance(at,(int,float)) or not math.isfinite(at) or at<0 or datetime.fromtimestamp(at,timezone.utc).date().isoformat()!=day:raise ValueError('attempts_log')
                days[day]=days.get(day,0)+1
            if any(n>900 for n in days.values()):raise ValueError('daily_limit')
            sends=len(attempts_log)
            # Legacy limiter records can exceed per-message attempts; equality not assumed.
            if sends<sum(r[2] for r in rows):raise ValueError('attempts_mismatch')
            result.update(outbox='VERIFIED_LOCAL_METADATA_ONLY',counts=counts,duplicate_events=len(duplicates),
                          repeated_occurrences=sum(n for _,n in duplicates),recorded_attempts=sends,
                          in_flight_after_restart='UNKNOWN_DO_NOT_RESEND' if counts['SENDING'] else 'NONE',
                          queued_is_delivered=False,overall='DISABLED_READY_FOR_USER_CONFIGURATION' if off else 'BLOCKED')
    except Exception:
        result['outbox']='BLOCKED_UNVERIFIED'  # No path, message text, token or raw error.
    return result


def main():
    import argparse
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2,'Invalid offline preflight arguments; no action performed.\n')
    parser=SafeParser(description=__doc__)
    parser.add_argument('--outbox')
    parser.add_argument('--format',choices=('json','markdown'),default='markdown')
    args=parser.parse_args()
    report=preflight(args.outbox)
    if args.format=='json':print(json.dumps(report,ensure_ascii=False,indent=2))
    else:
        print('Slack 사전 점검 — 실제 API 호출 0회 / 스케줄 등록 없음')
        print(report['delivery_notice'])
        print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['default_off']=='PASS' and report['outbox'] in ('NOT_PROVIDED','VERIFIED_LOCAL_METADATA_ONLY') else 2


if __name__=='__main__':
    raise SystemExit(main())
