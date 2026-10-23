"""Local receipts for human prompt issuance. NOT a worker/budget/approval ledger.

Requires explicit initialization. Missing/corrupt/mismatched files never reset.
Checkpoint is written BEFORE SQLite commit under BEGIN IMMEDIATE; a crash gap
halts. Joint DB+checkpoint rollback is not detectable; never grants authority.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
from uuid import uuid4

from .handoff_common import fingerprint, require, SHA, DIGEST
from .store import validate_policy
from .github_read import VerificationPending

APP_ID=1096041555


def private(path,directory=False):
    info=path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)), 'local_journal_unverified')
    if os.name=='posix':
        require(info.st_uid==os.getuid() and not info.st_mode & 0o077,'local_journal_unverified')


class HandoffJournal:
    def __init__(self,path,policy):
        self.path=Path(path).absolute()
        self.seal=self.path.with_name(self.path.name+'.checkpoint.json')
        self.policy=json.loads(json.dumps(policy))
        validate_policy(self.policy)
        private(self.path.parent,directory=True)
        private(self.path);private(self.seal)
        with self._transaction():pass

    @classmethod
    def initialize(cls,path,policy):
        path=Path(path).absolute();seal=path.with_name(path.name+'.checkpoint.json')
        validate_policy(policy)
        private(path.parent,directory=True)
        require(not path.exists() and not seal.exists() and not path.is_symlink() and not seal.is_symlink(),'local_journal_unverified')
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        state={'epoch':str(uuid4()),'revision':0,'policy':fingerprint(policy),'requests':[]}
        journal=object.__new__(cls);journal.path=path;journal.seal=seal;journal.policy=policy
        db=sqlite3.connect(path)
        try:
            db.execute(f'PRAGMA application_id={APP_ID}')
            db.execute('PRAGMA user_version=1')
            db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL,sha TEXT NOT NULL)')
            db.execute('INSERT INTO state VALUES(1,?,?)',(json.dumps(state),fingerprint(state)))
            journal._seal(state)
            db.commit()
        finally:db.close()
        return cls(path,policy)

    def _seal(self,state):
        temporary=self.seal.with_name(self.seal.name+'.next')
        fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as stream:
            json.dump([state['epoch'],state['revision'],fingerprint(state)],stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,self.seal)
        if os.name=='posix':
            fd=os.open(self.seal.parent,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)

    def _validate(self,state):
        require(set(state)=={'epoch','revision','policy','requests'} and state['policy']==fingerprint(self.policy),'local_journal_unverified')
        from uuid import UUID
        require(str(UUID(state['epoch']))==state['epoch'] and type(state['requests']) is list and type(state['revision']) is int
                and state['revision']==len(state['requests']),'local_journal_unverified')
        counts={};seen=set();heads=set()
        for row in state['requests']:
            require(set(row)=={'pr','head','feedback','snapshot','receipt'} and type(row['pr']) is int and row['pr']>0
                    and SHA.fullmatch(row['head']) and all(DIGEST.fullmatch(row[k]) for k in ('feedback','snapshot','receipt')),'local_journal_unverified')
            key=(row['pr'],row['head'],row['feedback'])
            require(key not in seen and (row['pr'],row['head']) not in heads,'local_journal_unverified')
            seen.add(key);heads.add((row['pr'],row['head']))
            counts[row['pr']]=counts.get(row['pr'],0)+1
            require(counts[row['pr']]<=(self.policy['max_attempts_per_issue']-1),'local_journal_unverified')

    @contextmanager
    def _transaction(self):
        db=None
        try:
            private(self.path.parent,directory=True);private(self.path);private(self.seal)
            require(self.seal.stat().st_size<=4096,'local_journal_unverified')
            db=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,isolation_level=None,timeout=5)
            db.execute('PRAGMA synchronous=FULL');db.execute('PRAGMA trusted_schema=OFF');db.execute('BEGIN IMMEDIATE')
            require(db.execute('PRAGMA application_id').fetchone()[0]==APP_ID and db.execute('PRAGMA user_version').fetchone()[0]==1
                    and db.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'local_journal_unverified')
            rows=db.execute('SELECT data,sha FROM state').fetchall()
            require(len(rows)==1 and len(rows[0][0])<=1_000_000,'local_journal_unverified')
            state=json.loads(rows[0][0]);self._validate(state)
            require(rows[0][1]==fingerprint(state) and json.loads(self.seal.read_text())==[state['epoch'],state['revision'],fingerprint(state)],'local_journal_unverified')
            yield db,state
            db.commit()
        except VerificationPending:raise
        except Exception:raise VerificationPending('local_journal_unverified') from None
        finally:
            if db is not None:db.rollback();db.close()

    def recovery_summary(self):
        """Metadata only, no feedback/body/path/auth data and no execution grant."""
        with self._transaction() as (_,state):
            return {'version':1,'epoch':state['epoch'],'revision':state['revision'],
                    'policy':state['policy'],'state_digest':fingerprint(state),
                    'receipts':[{k:r[k] for k in ('pr','head','receipt')} for r in state['requests']]}

    def issue(self,pr,head,feedback,snapshot):
        require(type(pr) is int and pr>0 and SHA.fullmatch(head) and DIGEST.fullmatch(feedback) and DIGEST.fullmatch(snapshot),'local_journal_unverified')
        with self._transaction() as (db,state):
            previous=[r for r in state['requests'] if r['pr']==pr]
            duplicate=next((r for r in previous if r['head']==head and r['feedback']==feedback),None)
            if duplicate:return {'duplicate':True,'receipt':duplicate['receipt'],'issued_count':len(previous)}
            require(not any(r['head']==head for r in previous),'rework_already_issued_for_head')
            require(len(previous)<(self.policy['max_attempts_per_issue']-1),'rework_limit')
            receipt=fingerprint([state['epoch'],state['revision']+1,pr,head,feedback,snapshot])
            state['requests'].append({'pr':pr,'head':head,'feedback':feedback,'snapshot':snapshot,'receipt':receipt})
            state['revision']+=1
            self._validate(state)
            db.execute('UPDATE state SET data=?,sha=? WHERE id=1',(json.dumps(state),fingerprint(state)))
            # Other issuers block on SQLite until both checkpoint and DB commit finish.
            self._seal(state)
            return {'duplicate':False,'receipt':receipt,'issued_count':len(previous)+1}


def compare_recovery_summary(previous, current):
    """User-retained evidence can reveal rollback, but is NOT a trusted witness."""
    from uuid import UUID
    for item in (previous,current):
        require(type(item) is dict and set(item)=={'version','epoch','revision','policy','state_digest','receipts'}
                and type(item['version']) is int and item['version']==1
                and type(item['revision']) is int and item['revision']>=0
                and type(item['receipts']) is list and item['revision']==len(item['receipts'])<=10000,
                'recovery_evidence_unverified')
        require(str(UUID(item['epoch']))==item['epoch'] and all(type(item[k]) is str and DIGEST.fullmatch(item[k]) for k in ('policy','state_digest')),
                'recovery_evidence_unverified')
        seen=set()
        for row in item['receipts']:
            require(type(row) is dict and set(row)=={'pr','head','receipt'} and type(row['pr']) is int and row['pr']>0
                    and type(row['head']) is str and SHA.fullmatch(row['head'])
                    and type(row['receipt']) is str and DIGEST.fullmatch(row['receipt'])
                    and row['receipt'] not in seen,'recovery_evidence_unverified')
            seen.add(row['receipt'])
    require(previous['epoch']==current['epoch'] and previous['policy']==current['policy']
            and previous['revision']<=current['revision']
            and previous['receipts']==current['receipts'][:previous['revision']], 'recovery_rollback_or_epoch_mismatch')
    if previous['revision']==current['revision']:
        require(previous['state_digest']==current['state_digest'],'recovery_rollback_or_epoch_mismatch')
    return 'LOCAL_PREFIX_MATCH_NOT_TRUSTED_WITNESS'
