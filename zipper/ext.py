#!/usr/bin/env python3
"""
Building and shipping the browser extension.

The extension is the one part of Zipper that runs somewhere this box cannot
reach: a browser on his desktop. Everything else is deployed by editing a file
here and restarting a service. This is the seam, and it needs a real path across
it or the extension quietly drifts a version behind the engine it talks to.

Three facts shape the design:

  * Release Firefox will not permanently install an unsigned extension, so a
    build is an upload to AMO and a signed `.xpi` coming back.
  * An installed `.xpi` is a copy inside the browser profile. Pulling this repo
    on the desktop changes nothing the browser will ever read.
  * `update_url` lives *inside* the manifest, so it is covered by the signature.
    It has to be right before the first signing, not added afterwards.

So: `zipper ext build` bumps the version, signs, and writes both the `.xpi` and
an update manifest into `data/ext/`, which the dashboard serves over the tailnet
at `/ext/`. Firefox polls that URL on its own schedule and installs what it
finds. Shipping a change becomes one command here and nothing at all there.
"""
import os, sys, json, glob, time, hashlib, subprocess

HERE   = os.path.dirname(os.path.abspath(__file__))
ROOT   = os.path.dirname(HERE)
SRC    = os.path.join(ROOT, 'extension')
OUT    = os.path.join(ROOT, 'data', 'ext')
MANIF  = os.path.join(SRC, 'manifest.json')

# Where the browser will look. Must match `update_url` in the manifest, and must
# be https: Firefox refuses a plain-http update URL outright.
BASE = os.environ.get('ZIPPER_EXT_BASE', '')


def _manifest():
    with open(MANIF, encoding='utf-8') as fh:
        return json.load(fh)


def addon_id():
    return _manifest()['browser_specific_settings']['gecko']['id']


def _bump(version, part='patch'):
    bits = [int(x) for x in version.split('.')] + [0, 0]
    i = {'major': 0, 'minor': 1, 'patch': 2}[part]
    bits = bits[:3]
    bits[i] += 1
    for j in range(i + 1, 3):
        bits[j] = 0
    return '.'.join(str(b) for b in bits)


def set_version(version):
    """Rewrite the version in place, without reformatting the whole file.

    Deliberately textual rather than json.dump: the manifest is hand-maintained
    and commented in spirit, and a build should not reflow a file the author is
    still reading.
    """
    with open(MANIF, encoding='utf-8') as fh:
        s = fh.read()
    old = _manifest()['version']
    s = s.replace('"version": "%s"' % old, '"version": "%s"' % version, 1)
    with open(MANIF, 'w', encoding='utf-8') as fh:
        fh.write(s)
    return old, version


PLACEHOLDER = '__ZIPPER_EXT_BASE__'


def set_update_url(base=None):
    """Point the manifest at this box, just before it is signed.

    `update_url` is inside the manifest and therefore covered by the signature,
    so it cannot be patched in afterwards -- but it is also a personal hostname,
    and this repo is public. The tracked manifest carries a placeholder and the
    real address arrives from `ZIPPER_EXT_BASE` at build time, which keeps the
    two in step: before, the same host was written out in two places and the
    .env comment could only ask that they match.

    Textual, for the same reason `set_version` is: a build should not reflow a
    file its author is still reading.
    """
    base = (base or BASE).rstrip('/')
    if not base:
        raise RuntimeError('ZIPPER_EXT_BASE is unset - see .env.example')
    with open(MANIF, encoding='utf-8') as fh:
        s = fh.read()
    cur = _manifest()['browser_specific_settings']['gecko'].get('update_url', '')
    s = s.replace('"update_url": "%s"' % cur,
                  '"update_url": "%s/ext/updates.json"' % base, 1)
    with open(MANIF, 'w', encoding='utf-8') as fh:
        fh.write(s)
    return base


def clear_update_url():
    """Put the placeholder back, so the working tree stays publishable."""
    with open(MANIF, encoding='utf-8') as fh:
        s = fh.read()
    cur = _manifest()['browser_specific_settings']['gecko'].get('update_url', '')
    s = s.replace('"update_url": "%s"' % cur,
                  '"update_url": "%s/ext/updates.json"' % PLACEHOLDER, 1)
    with open(MANIF, 'w', encoding='utf-8') as fh:
        fh.write(s)


def write_update_manifest(version, xpi_name, base=None):
    """The JSON Firefox polls. Its shape is fixed by Mozilla, not by us."""
    base = (base or BASE).rstrip('/')
    if not base:
        raise RuntimeError('ZIPPER_EXT_BASE is unset - see .env.example')
    path = os.path.join(OUT, xpi_name)
    with open(path, 'rb') as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    blob = {'addons': {addon_id(): {'updates': [{
        'version': version,
        'update_link': '%s/ext/%s' % (base, xpi_name),
        # Firefox verifies this before installing. It is not security -- the
        # signature is -- but it catches a truncated download, which otherwise
        # fails as a baffling "corrupt file" with no clue where the corruption
        # came from.
        'update_hash': 'sha256:' + digest,
    }]}}}
    out = os.path.join(OUT, 'updates.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(blob, fh, indent=2)
    return out


def cmd_ext(a):
    if getattr(a, 'clean', False):
        clear_update_url()
        print('update_url : %s/ext/updates.json' % PLACEHOLDER)
        return 0
    if getattr(a, 'show', False) or not getattr(a, 'build', False):
        return _show()
    return _build(a)


def _show():
    m = _manifest()
    print('source     : %s' % SRC)
    print('version    : %s' % m['version'])
    print('addon id   : %s' % addon_id())
    url = m['browser_specific_settings']['gecko'].get(
        'update_url', '(none - auto-update is off)')
    if PLACEHOLDER in url:
        url = '%s  (resolved from ZIPPER_EXT_BASE=%s at build time)' % (
            url, BASE or 'UNSET')
    print('update_url : %s' % url)
    print('artifacts  : %s' % OUT)
    for f in sorted(glob.glob(os.path.join(OUT, '*'))):
        print('   %-42s %6.1f kB' % (os.path.basename(f),
                                     os.path.getsize(f) / 1024.0))
    try:
        with open(os.path.join(OUT, 'updates.json'), encoding='utf-8') as fh:
            u = json.load(fh)['addons'][addon_id()]['updates'][0]
        print('serving    : %s  ->  %s' % (u['version'], u['update_link']))
        if u['version'] != m['version']:
            print('             NOTE: the manifest is at %s, so the served '
                  'build is behind the source' % m['version'])
    except Exception:
        print('serving    : nothing built yet')
    return 0


def _build(a):
    issuer = os.environ.get('AMO_JWT_ISSUER', '')
    secret = os.environ.get('AMO_JWT_SECRET', '')
    if not (issuer and secret):
        print('AMO_JWT_ISSUER / AMO_JWT_SECRET are unset. Generate a key at\n'
              '  https://addons.mozilla.org/en-US/developers/addon/api/key/\n'
              'and put both in /opt/zipper/.env', file=sys.stderr)
        return 1

    version = getattr(a, 'set_version', None)
    if not version:
        version = _bump(_manifest()['version'], getattr(a, 'bump', None) or 'patch')
    old, new = set_version(version)
    print('version    : %s -> %s' % (old, new))
    print('update_url : %s/ext/updates.json' % set_update_url())

    os.makedirs(OUT, exist_ok=True)
    cmd = ['web-ext', 'sign', '--channel=unlisted',
           '--source-dir', SRC, '--artifacts-dir', OUT,
           '--api-key', issuer, '--api-secret', secret]
    print('signing    : uploading to AMO, this takes a minute...')
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout + p.stderr)
    # The secret is on the command line, so it can surface in an error echo.
    # Scrub before anything is printed, logged, or forwarded to Discord.
    print(out.replace(secret, '<secret>').replace(issuer, '<issuer>').strip()[-2000:])
    if p.returncode:
        # Leave the manifest at the bumped version anyway: AMO rejects a repeat
        # version, and a failed upload may still have consumed the number.
        print('signing failed - the version stays bumped, because AMO may have '
              'taken it either way, and update_url stays resolved for the '
              'retry. `zipper ext --clean` puts the placeholder back.',
              file=sys.stderr)
        return p.returncode

    # Signed: the address is baked into the .xpi now, so the working copy goes
    # back to the placeholder. Doing it here rather than in a finally block is
    # deliberate -- a failed signing leaves the real URL in place, which is what
    # a retry needs, and the failure path already says the tree was touched.
    clear_update_url()

    xpis = sorted(glob.glob(os.path.join(OUT, '*.xpi')), key=os.path.getmtime)
    if not xpis:
        print('web-ext reported success but produced no .xpi', file=sys.stderr)
        return 1
    xpi = os.path.basename(xpis[-1])
    path = write_update_manifest(new, xpi)
    print('signed     : %s' % xpi)
    print('update     : %s' % path)
    print('\nInstall once by hand, then it updates itself:')
    print('  %s/ext/%s' % ((BASE or '<ZIPPER_EXT_BASE>').rstrip('/'), xpi))
    return 0
