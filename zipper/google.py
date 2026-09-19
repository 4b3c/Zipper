"""zipper.google

OAuth against Google, and the two Sheets calls the timesheet needs.

The token lives on this box rather than in the browser, which is the whole
point of doing it here: the extension could only ever act while a tab was open
on the sheet, and hours arrive over Discord at times when nothing is open at
all. With a refresh token in `.env`, `zipper hours add` can put a row in the
spreadsheet the moment he says it.

Stdlib only, like the rest of the server -- this is three HTTP calls, and a
dependency would be a worse trade than the forty lines below.

The client is Internal to his Workspace org, which is what makes this quiet:
no verification, no consent interstitial, and a refresh token that does not
expire. An External client in Testing would need re-authorising every 7 days.
"""
import json
import os
import time
import urllib.parse
import urllib.request

AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
SHEETS = 'https://sheets.googleapis.com/v4/spreadsheets'

# Read/write to the account's spreadsheets. Google has no per-document scope;
# `drive.file` would be narrower but only reaches files opened through its own
# picker, which a headless box cannot show. So the narrowing that is actually
# available is where the token lives, not what it can reach.
SCOPE = 'https://www.googleapis.com/auth/spreadsheets'

ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   '.env')


def _env_file():
    """.env as a dict, re-read each time.

    The environment is not enough on its own. A service gets .env through
    systemd, but a CLI run from a shell that started before a key was added
    sees the old value or none -- which reads as "the secret is unset" while
    the secret is sitting in the file. Worse, `exchange` writes the refresh
    token here, so a long-lived process that trusted os.environ would keep
    insisting it was unauthorized immediately after authorizing.
    """
    out = {}
    try:
        with open(ENV) as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith('#') or '=' not in ln:
                    continue
                k, v = ln.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def _cfg(key, default=''):
    # The file wins: it is where `exchange` writes, and where he pastes.
    return (_env_file().get(key) or os.environ.get(key) or default).strip()


def redirect_uri():
    base = _cfg('ZIPPER_EXT_BASE')      # the same tailnet https address
    if not base:
        raise RuntimeError('ZIPPER_EXT_BASE is unset; the callback needs a '
                           'public https address to be registered against')
    return base.rstrip('/') + '/oauth/google/callback'


def configured():
    return bool(_cfg('ZIPPER_GOOGLE_CLIENT_ID')
                and _cfg('ZIPPER_GOOGLE_CLIENT_SECRET'))


def authorized():
    return bool(_cfg('ZIPPER_GOOGLE_REFRESH_TOKEN'))


def auth_url():
    """The link he opens once. `prompt=consent` is what yields a refresh token.

    Google only returns a refresh token on the *first* grant unless consent is
    forced, so a second run of this would otherwise hand back an access token
    and nothing durable -- which looks like success and expires in an hour.
    """
    q = {'client_id': _cfg('ZIPPER_GOOGLE_CLIENT_ID'),
         'redirect_uri': redirect_uri(),
         'response_type': 'code',
         'scope': SCOPE,
         'access_type': 'offline',
         'prompt': 'consent',
         'include_granted_scopes': 'true'}
    return AUTH + '?' + urllib.parse.urlencode(q)


def _post(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def exchange(code):
    """Turn the one-time code from the callback into a lasting refresh token."""
    res = _post(TOKEN, {'code': code,
                        'client_id': _cfg('ZIPPER_GOOGLE_CLIENT_ID'),
                        'client_secret': _cfg('ZIPPER_GOOGLE_CLIENT_SECRET'),
                        'redirect_uri': redirect_uri(),
                        'grant_type': 'authorization_code'})
    rt = res.get('refresh_token')
    if not rt:
        raise RuntimeError('Google returned no refresh_token. This happens when '
                           'the account has granted before -- revoke at '
                           'myaccount.google.com/permissions and retry.')
    set_env('ZIPPER_GOOGLE_REFRESH_TOKEN', rt)
    os.environ['ZIPPER_GOOGLE_REFRESH_TOKEN'] = rt
    return res


_ACCESS = {'token': None, 'expires': 0}


def access_token():
    """A cached access token, refreshed a minute before it actually lapses."""
    if _ACCESS['token'] and time.time() < _ACCESS['expires'] - 60:
        return _ACCESS['token']
    rt = _cfg('ZIPPER_GOOGLE_REFRESH_TOKEN')
    if not rt:
        raise RuntimeError('not authorized yet - run `zipper google --auth`')
    res = _post(TOKEN, {'refresh_token': rt,
                        'client_id': _cfg('ZIPPER_GOOGLE_CLIENT_ID'),
                        'client_secret': _cfg('ZIPPER_GOOGLE_CLIENT_SECRET'),
                        'grant_type': 'refresh_token'})
    _ACCESS['token'] = res['access_token']
    _ACCESS['expires'] = time.time() + int(res.get('expires_in', 3600))
    return _ACCESS['token']


def _call(path, method='GET', payload=None, **params):
    url = f'{SHEETS}/{path}'
    if params:
        url += '?' + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + access_token())
    if data:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or '{}')


def read(sheet_id, rng):
    """Raw cell text. UNFORMATTED would turn his times into serial fractions."""
    res = _call(f'{sheet_id}/values/{urllib.parse.quote(rng)}',
                valueRenderOption='FORMATTED_VALUE',
                dateTimeRenderOption='FORMATTED_STRING')
    return res.get('values', [])


def read_formula(sheet_id, rng):
    """The same cells as formulas, for reading a sheet's shape rather than its
    contents -- which week a SUM covers, and how much room is left in it."""
    res = _call(f'{sheet_id}/values/{urllib.parse.quote(rng)}',
                valueRenderOption='FORMULA')
    return res.get('values', [])


def write(sheet_id, updates):
    """Several ranges at once. USER_ENTERED so "1:30" becomes a time and
    "=C31-B31" becomes a formula, exactly as if he had typed them."""
    return _call(f'{sheet_id}/values:batchUpdate', 'POST', {
        'valueInputOption': 'USER_ENTERED',
        'data': [{'range': r, 'values': v} for r, v in updates]})


def batch(sheet_id, requests):
    """spreadsheets.batchUpdate -- structure, as opposed to values."""
    return _call(f'{sheet_id}:batchUpdate', 'POST', {'requests': requests})


def tabs(sheet_id):
    res = _call(sheet_id, fields='sheets.properties')
    return [s['properties'] for s in res.get('sheets', [])]


# ------------------------------------------------------------------- .env I/O

def set_env(key, value):
    """Rewrite one key in .env, in place, without disturbing the rest.

    The refresh token has to survive a restart and belongs with the other
    secrets, and this file is already the one place credentials live. Written
    through a temp file so an interrupted write cannot truncate it -- losing
    .env would take the Discord token and the GitHub App with it.
    """
    try:
        with open(ENV) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    out, done = [], False
    for ln in lines:
        if ln.startswith(key + '='):
            out.append(f'{key}={value}')
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f'{key}={value}')
    tmp = ENV + '.tmp'
    with open(tmp, 'w') as f:
        f.write('\n'.join(out) + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV)


def cmd_google(a):
    if not configured():
        print('ZIPPER_GOOGLE_CLIENT_ID / _SECRET are unset in /opt/zipper/.env')
        return 1
    if getattr(a, 'auth', False) or not authorized():
        print('Open this, signed in as awpierc2@asu.edu:\n')
        print(auth_url())
        print('\nThe callback writes the refresh token into .env itself.')
        if not getattr(a, 'auth', False):
            print('\n(not authorized yet — that is why you are seeing this)')
        return 0
    print('client     : %s' % _cfg('ZIPPER_GOOGLE_CLIENT_ID')[:28] + '...')
    print('redirect   : %s' % redirect_uri())
    print('authorized : yes')
    sid = _cfg('ZIPPER_SHEET_ID')
    if sid:
        try:
            names = [t['title'] for t in tabs(sid)]
            print('sheet      : %s' % ', '.join(names))
        except Exception as e:
            print('sheet      : could not read it — %s' % e)
    return 0
