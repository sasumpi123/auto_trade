"""Opt-in Slack outbox. Enqueue is local; delivery is a separate explicit action."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import re
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import SLACK_BOT_TOKEN, SLACK_CHANNELS, SLACK_NOTIFICATIONS_ENABLED
from utils.message_queue import MessageQueue

_SECRET = re.compile(r'(?i)(xox[a-z]-\S+|xapp-\S+|gh[pousr]_\S+|github_pat_\S+|AKIA[A-Z0-9]{16}|[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|sk-\S+|bearer\s+\S+|(?:token|secret|password|api[_ -]?key|acc_key|sec_key)\s*[=:]|-----BEGIN|https?://|<[@!#])')
class FormattedMessage(str):
    """Locally formatted allowlisted record, never raw exception/log data."""


KINDS = {'START', 'STOP', 'HALTED', 'RECOVERY_REQUIRED', 'STATUS', 'FILL', 'ORDER_UNKNOWN',
         'API_ERROR', 'DATA_ERROR', 'SAFETY_LIMIT', 'CONSECUTIVE_LOSSES', 'REPORT',
         'PR_CREATED', 'CI_RESULT', 'WAIT_REVIEW', 'CHANGES_REQUESTED', 'TEST_FAILED',
         'DEVELOPMENT_HALTED', 'MERGED'}
DEV_KINDS = {'PR_CREATED','CI_RESULT','WAIT_REVIEW','CHANGES_REQUESTED','TEST_FAILED','DEVELOPMENT_HALTED','MERGED'}


def safe(value, limit=160):
    value = str(value)
    if len(value) > limit or _SECRET.search(value) or any(ord(c)<32 or ord(c)==127 or 0x202a<=ord(c)<=0x202e or 0x2066<=ord(c)<=0x2069 for c in value):
        return '[비공개 또는 미검증 데이터]'
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def metric(value, unit):
    if value is None:
        return 'N/A (미집계)'
    try:
        n = Decimal(str(value))
        if not n.is_finite():
            raise InvalidOperation
        return f'{n} {unit}'
    except (InvalidOperation, ValueError):
        return 'N/A (미집계)'


def format_event(kind, *, event_id, mode='PAPER', subject='BOT', status='미확인', values=None, at=None):
    if kind not in KINDS or mode not in ('PAPER', 'LIVE') or status not in ('성공', '실패', '미확인'):
        raise ValueError('Invalid notification event')
    stamp = at or datetime.now(timezone.utc)
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise ValueError('Timezone required')
    identity = hashlib.sha256(str(event_id).encode()).hexdigest()[:24]
    lines = [f'{stamp.isoformat()} [{mode}] {kind} | {safe(subject)} | {status}', f'이벤트 ID: {identity}']
    for key, value in (values or {}).items():
        lines.append(f'{safe(key)}: {safe(value)}')
    return FormattedMessage('\n'.join(lines))


def format_fill(order, *, mode='PAPER'):
    if order.get('status') != 'settled' or Decimal(str(order.get('executed_volume', 0))) <= 0:
        return format_event('ORDER_UNKNOWN', event_id=order.get('identifier', 'unknown'), mode=mode)
    values = {'방향': '매수' if order['side']=='bid' else '매도',
              '수량': metric(order.get('executed_volume'), '코인'),
              '평균 체결가': metric(order.get('average_fill_price'), 'KRW/코인'),
              '거래금액': metric(order.get('funds'), 'KRW'), '수수료': metric(order.get('paid_fee'), 'KRW'),
              '실현 손익': metric(order.get('profit_amount'), 'KRW'),
              '손익률': metric(order.get('profit_rate'), '%'),
              '전략/사유': next((tag for tag in ('물타기','손절','이익 실현','매수','매도') if tag in str(order.get('reason',''))), 'N/A (원문 비공개)'), '물타기': str(order.get('averaging', 'N/A'))}
    return format_event('FILL', event_id=order['identifier'], mode=mode,
                        subject=order['ticker'], status='성공', values=values)


def format_report(data, *, event_id, mode='PAPER'):
    units = {'시작 자산':'KRW', '종료 평가자산':'KRW', '현금':'KRW', '평가 손익':'KRW',
             '실현 손익':'KRW', '누적 수익률':'%', '거래 횟수':'회', '승률':'%', '수수료 합계':'KRW',
             '오류':'건', '중단':'건'}
    values = {k: metric(data.get(k), u) for k,u in units.items()}
    values['보유 포지션'] = data.get('보유 포지션', 'N/A (미집계)')
    values['전략별 결과'] = data.get('전략별 결과', 'N/A (미집계)')
    return format_event('REPORT', event_id=event_id, mode=mode, values=values)


class NotificationService:
    def __init__(self, *, enabled=SLACK_NOTIFICATIONS_ENABLED, approved=False, token=None,
                 client=None, queue=None, channels=None):
        self.enabled = enabled is True and approved is True
        self.token = SLACK_BOT_TOKEN if token is None else token
        self.channels = dict(SLACK_CHANNELS if channels is None else channels)
        self.queue = queue
        self.client = client
        self.last_delivery = 'DISABLED'
        # No client is created and no credential checked while disabled.

    def send_message(self, channel_type, message, *, event_id=None, priority=0):
        if not self.enabled:
            return False
        if self.queue is None or self.queue.path is None:
            self.last_delivery = 'DURABLE_OUTBOX_REQUIRED'
            return False
        if channel_type not in self.channels:
            return False
        if isinstance(message, FormattedMessage) and any('] '+kind+' |' in message for kind in DEV_KINDS):
            return False  # Development events stay formatter/Fake-only.
        # Only formatter-created multiline records; legacy free text is withheld.
        if not isinstance(message, FormattedMessage):
            message = format_event('STATUS', event_id='legacy', values={'내용':'레거시 원문 비공개'})
        lines = str(message).splitlines()
        if not lines or len(lines)>24 or len(str(message))>4000:
            return False
        clean = '\n'.join(safe(line, 400) for line in lines)
        match = re.search(r'^이벤트 ID: ([a-f0-9]{24})$', clean, re.MULTILINE)
        identity = event_id or (match.group(1) if match else hashlib.sha256(clean.encode()).hexdigest())
        try:
            return self.queue.enqueue(identity, self.channels[channel_type], clean, priority)
        except Exception:
            self.last_delivery = 'OUTBOX_BLOCKED'
            return False

    def flush_one(self):
        """Explicit delivery, never called from order processing or observers."""
        if not self.enabled:
            return 'DISABLED'
        if not isinstance(self.token, str) or not re.fullmatch(r'xoxb-[A-Za-z0-9-]{10,}', self.token):
            return 'BOT_TOKEN_REQUIRED'
        if self.queue is None or self.queue.path is None:
            return 'DURABLE_OUTBOX_REQUIRED'
        if any(not re.fullmatch(r'[CG][A-Z0-9]{1,32}', v) for v in self.channels.values()):
            return 'CHANNEL_ID_REQUIRED'
        try:
            row = self.queue.reserve()
            if row is None:
                return 'DEFERRED'
            key, channel, text = row
            repeats = self.queue.db.execute('SELECT repeats FROM duplicates WHERE id=?', (key,)).fetchone()
            if repeats:
                text += '\n동일 이벤트 추가 발생: '+str(repeats[0])+'회 (로컬 집계)'
            if self.client is None:
                self.client = WebClient(token=self.token, timeout=5, retry_handlers=[])
            result = self.client.chat_postMessage(channel=channel, text=text,
                unfurl_links=False, unfurl_media=False)
            state = 'SENT' if result.get('ok') is True and result.get('ts') and result.get('channel') == channel else 'UNKNOWN'
            self.queue.finish(key, state)
        except SlackApiError as exc:
            state = 'FAILED'
            if exc.response.status_code == 429:
                state = 'RATE_LIMITED'
                try:
                    delay = int(exc.response.headers.get('Retry-After', '60'))
                except (ValueError, TypeError):
                    delay = 60
                self.queue.finish(key, state, delay)
            else:
                self.queue.finish(key, state)
        except Exception:
            # Ambiguous result: no retry and no raw exception/response logging.
            state = 'UNKNOWN'
            if 'key' in locals():
                try:
                    self.queue.finish(key, state)
                except Exception:
                    pass  # SENDING stays durable and becomes UNKNOWN at next open.
        self.last_delivery = state
        return state

    def send_event(self, kind, *, event_id, mode='PAPER', subject='BOT', status='미확인', values=None):
        if kind in DEV_KINDS:
            raise ValueError('Development events are formatter/Fake only')
        channel = 'trades' if kind=='FILL' else 'reports' if kind=='REPORT' else 'errors' if kind in {'HALTED','RECOVERY_REQUIRED','ORDER_UNKNOWN','API_ERROR','DATA_ERROR','SAFETY_LIMIT','CONSECUTIVE_LOSSES'} else 'status'
        return self.send_message(channel, format_event(kind,event_id=event_id,mode=mode,subject=subject,status=status,values=values), event_id=event_id,priority=2 if channel in ('trades','errors') else 0)

    def send_trade_alert(self, message):
        return self.send_message('trades', message, priority=2)

    def send_status_update(self, message):
        scope = str(message).split(' | ')[0].split('] ')[-1]
        return self.send_message('status', message, event_id='status:'+scope+':'+str(int(self.queue.clock()//300)) if self.queue else 'status')

    def send_error_alert(self, message):
        # Raw exceptions must never leave the process.
        if isinstance(message, FormattedMessage):
            return self.send_message('errors', message, priority=2)
        bucket = str(int(self.queue.clock()//300)) if self.queue else 'disabled'
        return self.send_event('HALTED', event_id='halted:'+bucket, status='실패')

    def send_report(self, message):
        return self.send_message('reports', message if isinstance(message, FormattedMessage) else format_report({}, event_id=datetime.now(timezone.utc).date().isoformat()))

    def format_status_message(self, ticker, current_price, balance, coin_balance, total_profit, strategy_status):
        return format_event('STATUS', event_id='status:'+str(ticker), subject=ticker,
                            values={'현재가':metric(current_price,'KRW'), '현금':metric(balance,'KRW'),
                                    '보유량':metric(coin_balance,'코인'), '수익률':metric(total_profit,'%')})
