#!/usr/bin/env python3
"""
The GitHub App identity - how Zipper acts on GitHub as itself.

Before this existed, everything Zipper did on GitHub borrowed Abram: his personal
token in `.env`, reaching all 144 repos including the 65 NDA'd ASU-LL ones, with
every commit and every push indistinguishable from him at a keyboard. The App is a
separate actor - `zipper-4b3c[bot]`, its own profile, its own noreply address, no
square on his contribution graph.

Two credentials, and the difference matters:

  the private key   long-lived, on disk, and ONLY able to mint tokens
  an install token  what actually touches the API - expires in an hour

So the secret at rest is not a key to the account; it is a key to a one-hour,
one-permission lease. `GITHUB_TOKEN` stays in `.env` because `zipper github` reads
144 repos and the App is installed on his account only - fetching is still his.
This module is about *writing*.

Stdlib only, like the rest of the engine: RS256 is signed by shelling out to
`openssl`, which is already on the box, rather than taking a `cryptography`
dependency for one signature every hour.
"""
import os, sys, json, time, base64, subprocess, urllib.request, datetime

HERE     = os.path.dirname(os.path.abspath(__file__))
ROOT     = os.path.dirname(HERE)
APP_ID   = os.environ.get('ZIPPER_GH_APP_ID', '')
INSTALL  = os.environ.get('ZIPPER_GH_APP_INSTALL_ID', '')
KEYPATH  = os.environ.get('ZIPPER_GH_APP_KEY') or os.path.join(ROOT, 'zipper-app.pem')
CACHE    = os.path.join(ROOT, 'data', 'gh-app-token.json')

# Written by GitHub when the App was registered; read back by `identity()` so a
# commit's author line is never guessed. The number is the *bot user's* id, not
# the App's - a wrong one still commits, but the avatar never resolves.
BOT_SLUG = os.environ.get('ZIPPER_GH_APP_SLUG', 'zipper-4b3c')
BOT_UID  = os.environ.get('ZIPPER_GH_APP_UID', '330611607')


def identity():
    """(name, email) for git. The email is what makes GitHub show the bot."""
    return ('%s[bot]' % BOT_SLUG,
            '%s+%s[bot]@users.noreply.github.com' % (BOT_UID, BOT_SLUG))


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=')


def _jwt():
    """A 10-minute assertion that we hold the App's key. Not an access token."""
    if not APP_ID:
        raise RuntimeError('ZIPPER_GH_APP_ID is unset - see .env.example')
    if not os.path.exists(KEYPATH):
        raise RuntimeError('no private key at %s' % KEYPATH)
    now = int(time.time())
    # iat is backdated a minute: GitHub rejects a JWT from the future, and this
    # box's clock and theirs are only approximately the same.
    head = _b64(json.dumps({'alg': 'RS256', 'typ': 'JWT'},
                           separators=(',', ':')).encode())
    body = _b64(json.dumps({'iat': now - 60, 'exp': now + 540, 'iss': APP_ID},
                           separators=(',', ':')).encode())
    signing = head + b'.' + body
    p = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', KEYPATH],
                       input=signing, capture_output=True)
    if p.returncode:
        raise RuntimeError('openssl could not sign: %s'
                           % p.stderr.decode(errors='replace').strip())
    return (signing + b'.' + _b64(p.stdout)).decode()


def api(path, token, method='GET'):
    r = urllib.request.Request('https://api.github.com' + path, method=method)
    r.add_header('Authorization', 'Bearer ' + token)
    r.add_header('Accept', 'application/vnd.github+json')
    r.add_header('User-Agent', 'zipper')
    with urllib.request.urlopen(r, timeout=30) as fh:
        return json.load(fh)


def token(force=False):
    """An installation token, cached until five minutes before it expires.

    Cached because a bookkeeping pass may push more than once and each mint is a
    signature plus a round trip; five minutes of slack because a token that
    expires mid-push fails in the middle of writing refs, which is worse than
    minting one more than strictly needed.
    """
    if not force:
        try:
            with open(CACHE, encoding='utf-8') as fh:
                c = json.load(fh)
            if c.get('expires_at', '') > _utcnow(300):
                return c['token']
        except Exception:
            pass
    if not INSTALL:
        raise RuntimeError('ZIPPER_GH_APP_INSTALL_ID is unset - see .env.example')
    got = api('/app/installations/%s/access_tokens' % INSTALL, _jwt(), 'POST')
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    # The cache is a live credential: written 600, like the key itself.
    fd = os.open(CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump({'token': got['token'], 'expires_at': got['expires_at']}, fh)
    return got['token']


def _utcnow(plus=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=plus)).strftime('%Y-%m-%dT%H:%M:%SZ')


def push_url(remote_url, tok):
    """Rewrite a github.com remote to authenticate as the installation.

    `x-access-token` is the literal username GitHub expects here; the token is
    the password. Returned rather than stored, so the credential never lands in
    `.git/config` where a later `git remote -v` would print it.
    """
    for pre in ('https://github.com/', 'git@github.com:',
                'ssh://git@github.com/'):
        if remote_url.startswith(pre):
            return 'https://x-access-token:%s@github.com/%s' % (
                tok, remote_url[len(pre):])
    raise RuntimeError('not a github.com remote: %s' % remote_url)


def cmd_ghapp(a):
    if getattr(a, 'push', False):
        return _push(getattr(a, 'repo', None) or ROOT)
    if getattr(a, 'print_token', False):
        print(token()); return 0
    return _show()


def _show():
    name, email = identity()
    print('app id     : %s' % (APP_ID or '(unset)'))
    print('install id : %s' % (INSTALL or '(unset)'))
    print('key        : %s' % (KEYPATH if os.path.exists(KEYPATH)
                               else KEYPATH + '  MISSING'))
    print('commits as : %s <%s>' % (name, email))
    try:
        tok = token()
    except Exception as e:
        print('token      : FAILED - %s' % e); return 1
    try:
        d = api('/installation/repositories?per_page=1', tok)
        n, sel = d['total_count'], ''
        inst = api('/app/installations/%s' % INSTALL, _jwt())
        sel = inst.get('repository_selection', '?')
        print('token      : ok')
        print('reaches    : %d repo(s), selection=%s%s'
              % (n, sel, '   <-- wider than it needs to be' if sel == 'all' else ''))
        print('account    : %s' % inst['account']['login'])
    except Exception as e:
        print('token      : ok, but the API call failed - %s' % e); return 1
    return 0


def _run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _push(repo):
    """Push `repo` as the App, without ever writing the token into git config."""
    r = _run(['git', 'remote', 'get-url', 'origin'], repo)
    if r.returncode:
        print('no origin in %s' % repo, file=sys.stderr); return 1
    url = r.stdout.strip()
    branch = _run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], repo).stdout.strip()
    try:
        auth = push_url(url, token())
    except Exception as e:
        print('cannot authenticate: %s' % e, file=sys.stderr); return 1
    p = _run(['git', 'push', auth, 'HEAD:' + branch], repo)
    # A failed push prints the URL back at you, token and all. Never let that
    # reach a terminal, a log, or the Discord thread.
    out = (p.stdout + p.stderr).replace(auth, '<origin>')
    print(out.strip())
    if p.returncode == 0:
        print('pushed %s %s as %s' % (os.path.basename(repo), branch, identity()[0]))
    return p.returncode
