"""P26 manual PAPER continuation profile: fixed limits, reservations and session checkpoints.

Pure validation/build helpers reused by ops.paper_experiment (receipts, session marker) and
ops.paper_session (runtime). No I/O, network, ledger writes, automatic restart or new database.
"""
from services.public_market import MARKET
from services.tick_timing import ACCOUNT_QUARANTINE_POLICY

PROFILE_ID='P26_MANUAL_CONTINUATION_V1'
POLICY_ID=ACCOUNT_QUARANTINE_POLICY.identifier
COST_INPUT={'schema_version':1,'fee_bps':'5','slippage_bps':'5','full_spread_bps':'2'}
MARKETS=('KRW-BTC',)
MAX_RUNS=2                 # First run + one explicit manual resume.
MAX_SESSION_SECONDS=3600
MAX_TOTAL_SECONDS=7200
REST_PER_SESSION=64        # The existing ops/paper_watch.py per-session client limit (unchanged).
MAX_REST_TOTAL=128         # = MAX_RUNS x REST_PER_SESSION, reserved per run before network.
MODES=('first','resume')
MAX_COUNTER=2_147_483_647


class ContinuationError(ValueError):pass


def require(ok,code):
    if not ok:raise ContinuationError(code)


def profile():
    """Exact profile bound into the v3 creation receipt and account identity."""
    return {'id':PROFILE_ID,'markets':list(MARKETS),'max_runs':MAX_RUNS,'max_session_seconds':MAX_SESSION_SECONDS,
            'max_total_seconds':MAX_TOTAL_SECONDS,'rest_per_session':REST_PER_SESSION,'max_rest_total':MAX_REST_TOTAL,
            'regression_policy':POLICY_ID,'initial_cash':'1000000'}


def cost_policy():
    from trading.costs import from_policy,policy
    return policy(from_policy(COST_INPUT))


def empty_prior():return {'session_id':None,'state_sha256':None,'high_water_ms':{},'counts':{},'total':0}


def _prior_numbers(prior):
    require(type(prior) is dict and set(prior)=={'session_id','state_sha256','high_water_ms','counts','total'},'P26_PRIOR_INVALID')
    hw,counts,total=prior['high_water_ms'],prior['counts'],prior['total']
    require(type(hw) is dict and type(counts) is dict and set(hw)<=set(MARKETS) and set(counts)<=set(MARKETS),'P26_PRIOR_INVALID')
    require(all(type(v) is int and v>0 for v in hw.values()),'P26_PRIOR_INVALID')
    require(all(type(v) is int and 0<=v<=ACCOUNT_QUARANTINE_POLICY.max_per_market for v in counts.values()),'P26_PRIOR_INVALID')
    require(type(total) is int and total==sum(counts.values()) and 0<=total<=ACCOUNT_QUARANTINE_POLICY.max_per_session,'P26_PRIOR_INVALID')
    return {'high_water_ms':dict(hw),'counts':dict(counts),'total':total}


def guard_prior(block):
    """TickTiming account_prior derived ONLY from the validated marker block (never from CLI options)."""
    validate_block(block);return _prior_numbers(block['prior'])


def validate_block(block):
    require(type(block) is dict and set(block)=={'profile','policy','run_index','session_seconds','reserved_seconds_total',
            'rest_session_limit','rest_reserved_total','prior'},'P26_RESERVATION_INVALID')
    require(block['profile']==PROFILE_ID and block['policy']==POLICY_ID,'P26_RESERVATION_INVALID')
    run=block['run_index'];seconds=block['session_seconds'];total=block['reserved_seconds_total']
    require(type(run) is int and 1<=run<=MAX_RUNS,'P26_RESERVATION_INVALID')
    require(type(seconds) is int and 1<=seconds<=MAX_SESSION_SECONDS,'P26_RESERVATION_INVALID')
    require(type(total) is int and seconds<=total<=MAX_TOTAL_SECONDS,'P26_RESERVATION_INVALID')
    require(block['rest_session_limit']==REST_PER_SESSION and block['rest_reserved_total']==run*REST_PER_SESSION
            and block['rest_reserved_total']<=MAX_REST_TOTAL,'P26_RESERVATION_INVALID')
    _prior_numbers(block['prior'])
    if run==1:require(block['prior']==empty_prior() and total==seconds,'P26_RESERVATION_INVALID')
    else:require(type(block['prior']['session_id']) is str and type(block['prior']['state_sha256']) is str,'P26_RESERVATION_INVALID')
    return block


def reserve(mode,seconds,previous=None,current_sha=None):
    """Durable reservation for this run, written before network. Raises before any write on invalid input."""
    require(mode in MODES,'P26_MODE_INVALID')
    require(type(seconds) is int and 1<=seconds<=MAX_SESSION_SECONDS,'P26_SESSION_SECONDS_INVALID')
    if mode=='first':
        require(previous is None,'P26_FIRST_RUN_ALREADY_USED')
        return validate_block({'profile':PROFILE_ID,'policy':POLICY_ID,'run_index':1,'session_seconds':seconds,
            'reserved_seconds_total':seconds,'rest_session_limit':REST_PER_SESSION,'rest_reserved_total':REST_PER_SESSION,
            'prior':empty_prior()})
    require(type(previous) is dict and previous.get('phase')=='STOPPED_VERIFIED','P26_RESUME_REQUIRES_STOPPED_VERIFIED')
    old=validate_block(previous.get('p26'))
    require(old['run_index']+1<=MAX_RUNS,'P26_RUN_LIMIT_REACHED')
    checkpoint=validate_checkpoint(previous.get('p26_checkpoint'),previous,current_sha)
    # Cross-check the carried totals against the same session's persisted regression evidence and the
    # previous reservation's prior (catches a lowered single field; not a proof against coordinated edits).
    rc=previous.get('regression_checkpoint')
    combined=((rc or {}).get('evidence') or {}).get('combined') if isinstance(rc,dict) else None
    require(type(combined) is dict and isinstance(combined.get('markets'),dict),'P26_CHECKPOINT_EVIDENCE_MISSING')
    session_counts={t:r.get('count',0) for t,r in combined['markets'].items() if isinstance(r,dict)}
    for t in set(checkpoint['counts'])|set(session_counts)|set(old['prior']['counts']):
        require(checkpoint['counts'].get(t,0)==old['prior']['counts'].get(t,0)+session_counts.get(t,0),'P26_CHECKPOINT_EVIDENCE_MISMATCH')
    require(checkpoint['total']==old['prior']['total']+combined.get('discarded',-1),'P26_CHECKPOINT_EVIDENCE_MISMATCH')
    # High-water: a market that accepted ticks this session must keep its key; never below the prior carry.
    accepted=combined.get('accepted')
    require(type(accepted) is int and accepted>=0,'P26_CHECKPOINT_EVIDENCE_MISSING')
    if accepted>0:require(set(MARKETS)<=set(checkpoint['high_water_ms']),'P26_HIGH_WATER_MISSING')
    require(all(checkpoint['high_water_ms'].get(t,0)>=v for t,v in old['prior']['high_water_ms'].items()),'P26_HIGH_WATER_REGRESSED')
    total=old['reserved_seconds_total']+seconds
    require(total<=MAX_TOTAL_SECONDS,'P26_TOTAL_SECONDS_EXHAUSTED')
    # An exhausted discard budget blocks the resume BEFORE start (no refill, no silent zero-tolerance run).
    require(checkpoint['total']<ACCOUNT_QUARANTINE_POLICY.max_per_session
            and all(checkpoint['counts'].get(t,0)<ACCOUNT_QUARANTINE_POLICY.max_per_market for t in MARKETS),
            'P26_DISCARD_BUDGET_EXHAUSTED')
    prior={'session_id':previous['session_id'],'state_sha256':previous['end_sha256'],
           'high_water_ms':checkpoint['high_water_ms'],'counts':checkpoint['counts'],'total':checkpoint['total']}
    return validate_block({'profile':PROFILE_ID,'policy':POLICY_ID,'run_index':old['run_index']+1,'session_seconds':seconds,
        'reserved_seconds_total':total,'rest_session_limit':REST_PER_SESSION,
        'rest_reserved_total':(old['run_index']+1)*REST_PER_SESSION,'prior':prior})


def checkpoint_from_report(timing_report,session_id,state_sha,block):
    """End-of-session account checkpoint from the runtime/observation TickTiming report."""
    validate_block(block)
    require(type(timing_report) is dict,'P26_CHECKPOINT_UNVERIFIED')
    d=timing_report.get('regression_discard')
    require(type(d) is dict and d.get('policy_id')==POLICY_ID,'P26_CHECKPOINT_UNVERIFIED')
    require(d.get('quarantined_markets')==[] and d.get('awaiting_followup') is None,'P26_UNRESOLVED_QUARANTINE')
    counts={t:v for t,v in (d.get('account_counts') or {}).items() if v}
    checkpoint={'session_id':session_id,'state_sha256':state_sha,'policy':POLICY_ID,'run_index':block['run_index'],
                'high_water_ms':dict(d.get('high_water_ms') or {}),'counts':counts,'total':d.get('account_discarded_total'),
                'quarantined':[]}
    _prior_numbers({'session_id':None,'state_sha256':None,'high_water_ms':checkpoint['high_water_ms'],
                    'counts':checkpoint['counts'],'total':checkpoint['total']})
    prior=block['prior']
    require(checkpoint['total']>=prior['total'] and all(checkpoint['counts'].get(t,0)>=v for t,v in prior['counts'].items()),
            'P26_CHECKPOINT_REGRESSED')  # Budgets only ever grow; never refilled.
    require(all(checkpoint['high_water_ms'].get(t,0)>=v for t,v in prior['high_water_ms'].items()),'P26_CHECKPOINT_REGRESSED')
    return checkpoint


def checkpoint_digest(checkpoint,previous):
    """Content digest binding every checkpoint field (incl. high-water) to the marker's session, end ledger
    SHA and the same session's regression evidence. Detects a single lowered/deleted value; a deliberate,
    coordinated rewrite of the same file (recomputing this digest) is outside what this can prove."""
    import hashlib,json
    body={k:v for k,v in checkpoint.items() if k!='digest'}
    rc=previous.get('regression_checkpoint')
    payload={'checkpoint':body,'session_id':previous.get('session_id'),'end_sha256':previous.get('end_sha256'),
             'regression_evidence':rc.get('evidence') if isinstance(rc,dict) else None}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate_checkpoint(checkpoint,previous,current_sha=None):
    require(type(checkpoint) is dict and set(checkpoint)=={'session_id','state_sha256','policy','run_index',
            'high_water_ms','counts','total','quarantined','digest'},'P26_CHECKPOINT_MISSING_OR_INVALID')
    require(checkpoint['digest']==checkpoint_digest(checkpoint,previous),'P26_CHECKPOINT_DIGEST_MISMATCH')
    require(checkpoint['policy']==POLICY_ID and checkpoint['quarantined']==[],'P26_UNRESOLVED_QUARANTINE')
    require(checkpoint['session_id']==previous.get('session_id') and checkpoint['state_sha256']==previous.get('end_sha256')
            and checkpoint['run_index']==(previous.get('p26') or {}).get('run_index'),'P26_CHECKPOINT_NOT_BOUND')
    if current_sha is not None:require(checkpoint['state_sha256']==current_sha,'P26_CHECKPOINT_STALE')
    _prior_numbers({'session_id':None,'state_sha256':None,'high_water_ms':checkpoint['high_water_ms'],
                    'counts':checkpoint['counts'],'total':checkpoint['total']})
    return checkpoint


def status(marker_value):
    """Account-level counters for check/status/report. Session values are reported separately."""
    if not isinstance(marker_value,dict) or 'p26' not in marker_value:
        return {'profile':PROFILE_ID,'runs_used':0,'max_runs':MAX_RUNS,'reserved_seconds_total':0,
                'remaining_seconds':MAX_TOTAL_SECONDS,'rest_reserved_total':0,'max_rest_total':MAX_REST_TOTAL,
                'account_discards_total':0,'discards_remaining':ACCOUNT_QUARANTINE_POLICY.max_per_session,
                'resume_allowed':False,'first_run_allowed':True}
    block=validate_block(marker_value['p26'])
    checkpoint=marker_value.get('p26_checkpoint')
    total=checkpoint['total'] if isinstance(checkpoint,dict) and type(checkpoint.get('total')) is int else block['prior']['total']
    resume=(marker_value.get('phase')=='STOPPED_VERIFIED' and block['run_index']<MAX_RUNS and isinstance(checkpoint,dict)
            and block['reserved_seconds_total']<MAX_TOTAL_SECONDS and total<ACCOUNT_QUARANTINE_POLICY.max_per_session
            and all(checkpoint.get('counts',{}).get(t,0)<ACCOUNT_QUARANTINE_POLICY.max_per_market for t in MARKETS))
    return {'profile':PROFILE_ID,'runs_used':block['run_index'],'max_runs':MAX_RUNS,'last_phase':marker_value.get('phase'),
            'reserved_seconds_total':block['reserved_seconds_total'],
            'remaining_seconds':MAX_TOTAL_SECONDS-block['reserved_seconds_total'],
            'rest_reserved_total':block['rest_reserved_total'],'max_rest_total':MAX_REST_TOTAL,
            'account_discards_total':total,'discards_remaining':ACCOUNT_QUARANTINE_POLICY.max_per_session-total,
            'resume_allowed':bool(resume),'first_run_allowed':False}


def validate_markets(tickers):
    require(list(tickers or [])==list(MARKETS) and all(MARKET.fullmatch(t) for t in tickers),'P26_FIXED_MARKETS_REQUIRED')
