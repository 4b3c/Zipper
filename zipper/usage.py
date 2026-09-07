"""zipper.usage

How much of the plan is left: the 5-hour session window and the 7-day window,
as percentages, for the meters under the dashboard's chat list.

**Nothing local can answer this.** The transcripts on this box know how many
tokens *this* box spent, but not the denominator, and not what was spent from
the phone or the laptop. The only honest source is Anthropic's own OAuth usage
endpoint, which is what Claude Code's own `/usage` reads.

That means a token, and the token is Claude Code's. It is read out of
`~/.claude/.credentials.json` at call time and **never stored, logged or
written anywhere** -- `Inbox/usage.json` holds the percentages and nothing
else. Same rule as `/opt/zipper/.env`: credentials stay where they are.

The response shape is not a contract we control, so `_pct` hunts for the number
rather than indexing a path. A shape change should show up as a blank meter,
never as a wrong one -- an authoritative-looking 12% when the truth is 90% is
the failure worth designing against.
"""
import os, json, time, datetime
import urllib.request, urllib.error

from .core import *          # noqa: F401,F403
from . import core


USAGE_JSON = os.path.join(INBOX, 'usage.json')
ENDPOINT = os.environ.get('ZIPPER_USAGE_URL',
                          'https://api.anthropic.com/api/oauth/usage')
CREDS = os.environ.get('ZIPPER_CLAUDE_CREDS',
                       os.path.expanduser('~/.claude/.credentials.json'))

TTL = int(os.environ.get('ZIPPER_USAGE_TTL', 300))   # seconds between calls

_CACHE = {'at': 0, 'data': None}


def token():
    """Claude Code's OAuth access token, or None.

    Two shapes have been seen in the wild: the token under `claudeAiOauth`, and
    flat at the top level. Anything else returns None and the meters go blank.
    """
    try:
        with open(CREDS) as fh:
            blob = json.load(fh)
    except Exception:
        return None
    for holder in (blob.get('claudeAiOauth'), blob.get('claude_ai_oauth'), blob):
        if isinstance(holder, dict):
            tok = holder.get('accessToken') or holder.get('access_token')
            if tok:
                return tok
    return None


def _pct(node):
    """The utilization of one window as 0-100, or None.

    Accepts either a percentage the API already computed or a used/limit pair,
    and normalises a 0-1 fraction. Returns None rather than guessing.
    """
    if isinstance(node, (int, float)):
        return max(0.0, min(100.0, float(node) * (100 if node <= 1 else 1)))
    if not isinstance(node, dict):
        return None
    for k in ('utilization', 'used_pct', 'percent_used', 'percentage', 'pct'):
        if isinstance(node.get(k), (int, float)):
            v = float(node[k])
            return max(0.0, min(100.0, v * 100 if v <= 1 else v))
    used, limit = node.get('used'), node.get('limit') or node.get('total')
    if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit:
        return max(0.0, min(100.0, 100.0 * used / limit))
    return None


def _resets(node):
    if not isinstance(node, dict):
        return None
    for k in ('resets_at', 'reset_at', 'resets', 'window_end', 'ends_at'):
        v = node.get(k)
        if v:
            return str(v)
    return None


def _find(blob, names):
    """The sub-object for a window, matched by name anywhere in the response."""
    if not isinstance(blob, dict):
        return None
    for k, v in blob.items():
        if any(n in k.lower() for n in names):
            return v
    for v in blob.values():                       # one level down
        if isinstance(v, dict):
            hit = _find(v, names)
            if hit is not None:
                return hit
    return None


WINDOWS = (
    ('session', 'session', ('five_hour', 'fivehour', '5h', 'session')),
    ('week',    'week',    ('seven_day', 'sevenday', '7d', 'week')),
)


def normalise(blob):
    out = {'fetched': datetime.datetime.now().isoformat(timespec='seconds'),
           'meters': []}
    for key, label, names in WINDOWS:
        node = _find(blob, names)
        pct = _pct(node)
        if pct is None:
            continue
        out['meters'].append({'key': key, 'label': label,
                              'pct': round(pct, 1), 'resets': _resets(node)})
    return out


def fetch():
    """Call the endpoint. Returns the normalised dict, or one with `error`."""
    tok = token()
    if not tok:
        return {'error': 'no Claude Code credentials on this box', 'meters': []}
    req = urllib.request.Request(ENDPOINT, headers={
        'Authorization': 'Bearer %s' % tok,
        'anthropic-beta': 'oauth-2025-04-20',
        'User-Agent': 'zipper',
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            blob = json.load(r)
    except urllib.error.HTTPError as e:
        # 401 is the interesting one: the token expired and Claude Code has not
        # refreshed it yet. Say so rather than showing a stale bar.
        return {'error': 'usage endpoint returned %s' % e.code, 'meters': []}
    except Exception as e:
        return {'error': str(e), 'meters': []}
    got = normalise(blob)
    if not got['meters']:
        got['error'] = 'usage response had no window this understands'
    return got


def read(force=False):
    """Cached usage. Called by the dashboard on a timer, so it must be cheap."""
    now = time.time()
    if not force and _CACHE['data'] and now - _CACHE['at'] < TTL:
        return _CACHE['data']
    got = fetch()
    if got.get('meters'):
        _CACHE.update(at=now, data=got)
        try:
            with open(USAGE_JSON, 'w') as fh:
                json.dump(got, fh, indent=1)
        except Exception:
            pass
        return got
    # A failed call falls back to the last good numbers, marked stale, rather
    # than blanking the meters on one flaky request.
    last = _CACHE['data']
    if last is None:
        try:
            with open(USAGE_JSON) as fh:
                last = json.load(fh)
        except Exception:
            last = None
    if last:
        return dict(last, stale=True, error=got.get('error'))
    return got
