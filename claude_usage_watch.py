#!/usr/bin/env python3
"""Claude plan usage watch.

Reads the real 5-hour and weekly Claude plan usage meters, keeps a small history, warns when the week is
being used faster than the reset allows, and sends one short report every morning.

How it reads the meter: the same endpoint the Claude Code /usage screen uses, with the OAuth access token
Claude Code keeps in ~/.claude/.credentials.json. The token is only read: it is never printed, logged,
copied or refreshed (refreshing it here could log the running Claude Code sessions out). When the token has
expired, the script waits until a running Claude Code session renews it. The endpoint is not documented, so
it can break with an update; then the script sends one "cannot read" alert a day and nothing else.

Meant to run every 5 minutes from a systemd user timer (see examples/).

Quiet by design:
  weekly meter  one alert when it first passes 50, 75, 90 and 100 percent
  weekly pace   at most one alert a day, only when usage is 10 or more points ahead of an even pace AND
                would run out 12 or more hours before the reset
  5-hour meter  one alert when it first passes 70 and 90 percent of the current window
  daily         one report at DAILY_HOUR (local time in USAGE_TZ)

Delivery (any that are configured):
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   sent with the Telegram Bot API (sendMessage)
  OUTBOX_DIR                              one text file per alert, for another program to deliver
  neither                                 printed to stdout

Flags:
  --dry          print alerts, write nothing (no state, no history, no JSON)
  --daily        send the morning report now
  --fake FILE    read the meter answer from FILE (a saved JSON response) instead of the network;
                 a resets_at like "+30h" means 30 hours from now (see examples/fake-usage-response.json)
"""
import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

HOME = os.path.expanduser('~')
URL = 'https://api.anthropic.com/api/oauth/usage'
WEEK_MARKS = (50, 75, 90, 100)
FIVE_MARKS = (70, 90)
PACE_AHEAD_POINTS = 10
PACE_EARLY_HOURS = 12


def env(name, default=''):
    return os.environ.get(name, '').strip() or default


def config():
    tz_name = env('USAGE_TZ', 'UTC')
    tz = ZoneInfo(tz_name) if ZoneInfo else timezone.utc
    state_dir = os.path.expanduser(env('STATE_DIR', os.path.join(HOME, '.local', 'state', 'claude-usage-watch')))
    return {
        'cred': os.path.expanduser(env('CLAUDE_CREDENTIALS', os.path.join(HOME, '.claude', '.credentials.json'))),
        'state_dir': state_dir,
        'samples': os.path.join(state_dir, 'samples.jsonl'),
        'state': os.path.join(state_dir, 'state.json'),
        'outbox': os.path.expanduser(env('OUTBOX_DIR')),
        'json_out': [os.path.expanduser(p.strip()) for p in env('JSON_OUTPUT').split(',') if p.strip()],
        'tg_token': env('TELEGRAM_BOT_TOKEN'),
        'tg_chat': env('TELEGRAM_CHAT_ID'),
        'tz': tz,
        'daily_hour': int(env('DAILY_HOUR', '8')),
        'lang': 'fa' if env('ALERT_LANG', 'en').lower() == 'fa' else 'en',
    }


# ---------------------------------------------------------------- text (English by default, Persian optional)

TEXT = {
    'en': {
        'cannot': '❗️ Cannot read the usage meter\n🛠 Check the token and the endpoint (see the log)',
        'week_mark': '📊 Weekly usage reached {pct}%',
        'week_fast': '📊 Weekly usage is running fast: {pct}%',
        'five_mark': '🕔 5-hour usage reached {pct}%',
        'reset': '⏳ Resets in {left}',
        'full': '⛔️ Weekly limit reached',
        'opens': '⏳ Opens again in {left}',
        'ahead': '🚀 Ahead of an even pace by {pts}%',
        'on_pace': '🎯 Roughly on pace',
        'runs_out': '⚠️ Runs out in {left}',
        'early': '⏱ That is {left} before the reset',
        'behind': '🐢 Behind an even pace by {pts}%',
        'week_end': '🎯 Week ends around {pct}%',
        'daily': '☀️ Morning report',
        'weekly': '📊 Weekly: {pct}%',
        'day_used': '📅 Last 24 hours: {pct}%',
        'since_reset': '📅 Since the reset: {pct}%',
        'day_later': '📅 Last 24 hours: available tomorrow',
        'five': '🕔 5-hour: {pct}%',
        'five_reset': '⏳ 5-hour resets in {left}',
        'd': '{n} d', 'h': '{n} h', 'm': '{n} min', 'dh': '{d} d {h} h',
    },
    'fa': {
        'cannot': '❗️ کنتور مصرف خونده نمیشه\n🛠 توکن و آدرس رو چک کن',
        'week_mark': '📊 هفتگی به {pct}٪ رسید',
        'week_fast': '📊 هفتگی تند میره: {pct}٪',
        'five_mark': '🕔 پنج‌ساعته به {pct}٪ رسید',
        'reset': '⏳ ریست: {left} دیگه',
        'full': '⛔️ سقف هفته پر شده',
        'opens': '⏳ باز میشه: {left} دیگه',
        'ahead': '🚀 جلوتر از برنامه: {pts}٪',
        'on_pace': '🎯 تقریبا روی برنامه',
        'runs_out': '⚠️ تموم میشه: {left} دیگه',
        'early': '⏱ زودتر از ریست: {left}',
        'behind': '🐢 عقب‌تر از برنامه: {pts}٪',
        'week_end': '🎯 آخر هفته حدود: {pct}٪',
        'daily': '☀️ گزارش صبح',
        'weekly': '📊 هفتگی: {pct}٪',
        'day_used': '📅 مصرف ۲۴ ساعت اخیر: {pct}٪',
        'since_reset': '📅 از ریست تا الان: {pct}٪',
        'day_later': '📅 مصرف ۲۴ ساعت اخیر: از فردا',
        'five': '🕔 پنج‌ساعته: {pct}٪',
        'five_reset': '⏳ ریست پنج‌ساعته: {left} دیگه',
        'd': '{n} روز', 'h': '{n} ساعت', 'm': '{n} دقیقه', 'dh': '{d} روز و {h} ساعت',
    },
}
FA_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


class Words:
    def __init__(self, lang='en'):
        self.lang = lang
        self.t = TEXT[lang]

    def num(self, x):
        s = str(x)
        return s.translate(FA_DIGITS) if self.lang == 'fa' else s

    def __call__(self, key, **kw):
        return self.t[key].format(**{k: self.num(v) for k, v in kw.items()})

    def span(self, seconds):
        seconds = max(0, int(seconds))
        d, h = seconds // 86400, (seconds % 86400) // 3600
        if d and h: return self('dh', d=d, h=h)
        if d: return self('d', n=d)
        if h: return self('h', n=h)
        return self('m', n=max(1, seconds // 60))


# ---------------------------------------------------------------- reading the meter

def parse_meter(d, now):
    """Turn the endpoint's answer into {'five': {...}, 'week': {...}} or (None, error)."""
    out = {}
    for key, name in (('five_hour', 'five'), ('seven_day', 'week')):
        m = d.get(key) or {}
        if m.get('utilization') is None or not m.get('resets_at'):
            if name == 'five' and d.get('seven_day'):
                # an idle 5-hour window comes back empty: count it as 0, resetting in 5 hours
                m = {'utilization': m.get('utilization') or 0,
                     'resets_at': m.get('resets_at') or (now + timedelta(hours=5)).isoformat()}
            else:
                return None, 'unexpected answer'
        out[name] = {'pct': float(m['utilization']), 'resets': m['resets_at']}
    return out, None


def read_meter(cfg, now, fake=None):
    if fake:
        # a saved answer; "resets_at": "+30h" means 30 hours from now, so the sample never goes stale
        with open(fake) as f:
            d = json.load(f)
        for m in d.values():
            if isinstance(m, dict) and str(m.get('resets_at', '')).startswith('+'):
                m['resets_at'] = (now + timedelta(hours=float(m['resets_at'][1:].rstrip('h')))).isoformat()
        return parse_meter(d, now)
    try:
        with open(cfg['cred']) as f:
            o = json.load(f).get('claudeAiOauth') or {}
    except (OSError, ValueError):
        return None, 'no credentials file'
    # Read only. Never refresh the token here: a running Claude Code session renews it.
    if o.get('expiresAt', 0) / 1000 < time.time() + 30:
        return None, 'token expired (a running Claude Code session will renew it)'
    req = urllib.request.Request(URL, headers={'Authorization': 'Bearer ' + o['accessToken'],
                                               'anthropic-beta': 'oauth-2025-04-20',
                                               'User-Agent': 'claude-usage-watch'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code}'
    except Exception as e:
        return None, type(e).__name__
    return parse_meter(d, now)


# ---------------------------------------------------------------- pace and alerts (pure, no I/O)

def parse_ts(s):
    dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pace(week, now):
    """Where usage stands against an even burn over the 7-day window, and when it runs out at the average rate."""
    reset = parse_ts(week['resets'])
    start = reset - timedelta(days=7)
    elapsed = max(60.0, (now - start).total_seconds())
    expected = min(100.0, 100.0 * elapsed / (7 * 86400))
    used = week['pct']
    full_at = start + timedelta(seconds=elapsed * 100.0 / used) if used > 0 else None
    end_pct = used * (7 * 86400) / elapsed
    return {'reset': reset, 'expected': expected, 'ahead': used - expected, 'full_at': full_at, 'end_pct': end_pct}


def pace_lines(week, p, now, w):
    if week['pct'] >= 100:
        return [w('full'), w('opens', left=w.span((p['reset'] - now).total_seconds()))]
    pts = abs(round(p['ahead']))
    if p['full_at'] and p['full_at'] < p['reset']:
        head = w('ahead', pts=pts) if p['ahead'] >= 1 else w('on_pace')
        return [head, w('runs_out', left=w.span((p['full_at'] - now).total_seconds())),
                w('early', left=w.span((p['reset'] - p['full_at']).total_seconds()))]
    return [w('behind', pts=pts), w('week_end', pct=round(min(p['end_pct'], 100)))]


def decide(m, sent, now, today, w):
    """Alerts for one reading. Updates `sent` (the memory of what was already said) and returns the texts."""
    out = []
    week, five = m['week'], m['five']
    p = pace(week, now)
    reset_line = w('reset', left=w.span((p['reset'] - now).total_seconds()))

    # weekly marks: one alert the first time each mark is passed in this week; only the highest is announced
    for mark in WEEK_MARKS:
        key = f'week:{week["resets"][:13]}:{mark}'
        if week['pct'] >= mark and key not in sent:
            if not any(week['pct'] >= hi for hi in WEEK_MARKS if hi > mark):
                out.append('\n'.join([w('week_mark', pct=round(week['pct'])), *pace_lines(week, p, now, w), reset_line]))
            sent[key] = today

    # weekly pace: at most once a day, only when clearly ahead and running out well before the reset
    if (week['pct'] < 100 and p['ahead'] >= PACE_AHEAD_POINTS and p['full_at']
            and (p['reset'] - p['full_at']).total_seconds() >= PACE_EARLY_HOURS * 3600 and sent.get('pace') != today
            and not any(v == today and k.startswith('week:') for k, v in sent.items())):
        out.append('\n'.join([w('week_fast', pct=round(week['pct'])), *pace_lines(week, p, now, w), reset_line]))
        sent['pace'] = today

    # 5-hour marks: one alert per mark per window
    for mark in FIVE_MARKS:
        key = f'five:{five["resets"][:16]}:{mark}'
        if five['pct'] >= mark and key not in sent:
            if not any(five['pct'] >= hi for hi in FIVE_MARKS if hi > mark):
                out.append(w('five_mark', pct=round(five['pct'])) + '\n'
                           + w('reset', left=w.span((parse_ts(five['resets']) - now).total_seconds())))
            sent[key] = today
    return out, p


def daily_report(m, p, rows, now, w):
    week, five = m['week'], m['five']
    target = now.timestamp() - 86400
    old = min(rows, key=lambda r: abs(r['ts'] - target), default=None)
    if old and abs(old['ts'] - target) <= 5400:
        if old['week']['resets'][:13] == week['resets'][:13]:
            day_line = w('day_used', pct=round(week['pct'] - old['week']['pct']))
        else:
            day_line = w('since_reset', pct=round(week['pct']))
    else:
        day_line = w('day_later')
    return '\n'.join([w('daily'), w('weekly', pct=round(week['pct'])),
                      w('reset', left=w.span((p['reset'] - now).total_seconds())), day_line,
                      *pace_lines(week, p, now, w), w('five', pct=round(five['pct'])),
                      w('five_reset', left=w.span((parse_ts(five['resets']) - now).total_seconds()))])


# ---------------------------------------------------------------- delivery and files

def send(cfg, text, dry):
    if dry:
        print('--- alert ---\n' + text + '\n'); return
    delivered = False
    if cfg['tg_token'] and cfg['tg_chat']:
        try:
            body = json.dumps({'chat_id': cfg['tg_chat'], 'text': text}).encode()
            req = urllib.request.Request(f'https://api.telegram.org/bot{cfg["tg_token"]}/sendMessage', data=body,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as r:
                delivered = bool(json.load(r).get('ok'))
        except Exception as e:
            print('telegram send failed:', type(e).__name__)
    if cfg['outbox']:
        os.makedirs(cfg['outbox'], exist_ok=True)
        with open(os.path.join(cfg['outbox'], f'msg-{time.time_ns()}-claude-usage.txt'), 'w') as f:
            f.write(text + '\n')
        delivered = True
    if not delivered:
        print(text + '\n')


def load_samples(path):
    rows = []
    try:
        with open(path) as f:
            for line in f:
                try: rows.append(json.loads(line))
                except ValueError: pass
    except OSError:
        pass
    return rows


def gauge_json(m, p, now, tz):
    week, five = m['week'], m['five']
    return {'week_pct': round(week['pct']), 'week_expected': round(p['expected']), 'week_resets': week['resets'],
            'five_pct': round(five['pct']), 'five_resets': five['resets'],
            'week_resets_ts': int(parse_ts(week['resets']).timestamp()),
            'five_resets_ts': int(parse_ts(five['resets']).timestamp()),
            'updated': now.astimezone(tz).strftime('%Y-%m-%d %H:%M')}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dry = '--dry' in argv
    force_daily = '--daily' in argv
    fake = argv[argv.index('--fake') + 1] if '--fake' in argv and argv.index('--fake') + 1 < len(argv) else None
    cfg = config()
    w = Words(cfg['lang'])
    if not dry:
        os.makedirs(cfg['state_dir'], exist_ok=True)
    try:
        with open(cfg['state']) as f: state = json.load(f)
    except (OSError, ValueError):
        state = {}
    sent = state.setdefault('sent', {})
    now = datetime.now(timezone.utc)
    local = now.astimezone(cfg['tz'])
    today = local.strftime('%Y-%m-%d')

    def save_state():
        if not dry:
            with open(cfg['state'], 'w') as f: json.dump(state, f)

    m, err = read_meter(cfg, now, fake)
    if m is None:
        if time.time() - state.get('last_ok', 0) > 3 * 3600 and sent.get('cannot') != today:
            send(cfg, w('cannot'), dry)
            sent['cannot'] = today
        save_state()
        print('no reading:', err); return 1

    rows = load_samples(cfg['samples'])
    if not dry:
        with open(cfg['samples'], 'a') as f:
            f.write(json.dumps({'ts': int(now.timestamp()), **m}) + '\n')
        state['last_ok'] = time.time()

    alerts, p = decide(m, sent, now, today, w)
    for text in alerts:
        send(cfg, text, dry)

    if force_daily or (local.hour >= cfg['daily_hour'] and sent.get('daily') != today):
        send(cfg, daily_report(m, p, rows, now, w), dry)
        if not force_daily: sent['daily'] = today

    # forget old keys so the state file stays small
    cutoff = (local - timedelta(days=10)).strftime('%Y-%m-%d')
    state['sent'] = {k: v for k, v in sent.items() if v >= cutoff}
    save_state()

    # current numbers for gauges and dashboards (same meter read, costs no extra usage)
    cur = gauge_json(m, p, now, cfg['tz'])
    if dry:
        print('--- json ---\n' + json.dumps(cur))
    else:
        for dest in cfg['json_out']:
            try:
                tmp = dest + '.tmp'
                with open(tmp, 'w') as f: json.dump(cur, f)
                os.replace(tmp, dest)
            except OSError as e:
                print('json not written:', dest, type(e).__name__)
    print(f'week {m["week"]["pct"]:.0f}% (expected {p["expected"]:.0f}%), five-hour {m["five"]["pct"]:.0f}%')
    return 0


if __name__ == '__main__':
    sys.exit(main())
