"""One ttyd per conversation.

ttyd serves exactly one command per port and ours attaches one tmux session, so
watching several conversations means one ttyd each. Ports are handed out from a
fixed range; a restart must be able to adopt the ttyd it left behind rather than
spawn a second one on top of it.

Split out of `zipper/conversations.py` on 2026-09-07.
"""
import os, time, shutil, signal, socket, subprocess

from .core import *          # noqa: F401,F403
from . import core
from .convcore import _tmux, alive, load, save, target, touch, tmux_name

# ---------------------------------------------------------------- viewing
#
# ttyd serves exactly one command per port, and ours attaches one tmux session,
# so watching several conversations means one ttyd each. Ports are handed out
# from a base and remembered in the registry; the process is not, because a
# serve.py restart must be able to adopt the ttyd it left behind rather than
# fight it for the port. Whether the port answers is the only durable truth.

TTYD_BASE = int(os.environ.get('ZIPPER_TTYD_BASE', 8810))
TTYD_SPAN = 20
PROCS = {}


def _port_open(host, port, timeout=0.3):
    import socket
    c = socket.socket()
    c.settimeout(timeout)
    try:
        return c.connect_ex((host, port)) == 0
    finally:
        c.close()


def _pick_port(thread_id, host):
    row = load().get(str(thread_id)) or {}
    if row.get('port'):
        return int(row['port'])
    taken = {int(r['port']) for r in load().values() if r.get('port')}
    for p in range(TTYD_BASE, TTYD_BASE + TTYD_SPAN):
        if p in taken or _port_open(host, p):
            continue
        return p
    raise RuntimeError('no free ttyd port in %d-%d' % (TTYD_BASE, TTYD_BASE + TTYD_SPAN))


def ensure_ttyd(thread_id, host='127.0.0.1', cred='', font=13):
    """A ttyd serving this conversation's pane, started if it isn't already.

    Attaches with `tmux attach`, so the websocket owns nothing: closing the tab
    detaches and the conversation keeps running.

    **`attach`, never `new -A`.** ttyd re-runs its command for every connection,
    and the browser reconnects on its own when one drops. With `new -A` that
    made Ctrl-C unkillable: ending Claude ended the pane, ttyd's client exited,
    the page reconnected, `new -A` built the session again out of nothing, and
    the sweep tore it down a few seconds later -- a conversation flickering back
    to life instead of going grey. `attach` can only ever join a session that
    exists; when it doesn't, the command exits and the conversation stays dead,
    which is the whole point of pressing Ctrl-C.

    Bound to loopback and served through nginx under `/t/<port>/`, which is what
    makes the dashboard's origin the only one a browser ever sees. Pointed
    straight at these ports instead, every conversation is a separate origin and
    basic auth prompts again for each -- being signed in to the dashboard has to
    be enough. `--base-path` is load-bearing for that: without it ttyd's own
    asset and websocket URLs are absolute and miss the prefix.

    A credential still applies if one is configured, and is *required* on any
    host but loopback -- ttyd -W hands out a live shell.
    """
    if not alive(thread_id):
        return {'ok': False, 'error': 'conversation not running'}
    port = _pick_port(thread_id, host)
    if _port_open(host, port):
        touch(thread_id, port=port, host=host)
        return {'ok': True, 'port': port, 'adopted': True}
    exe = shutil.which('ttyd')
    if not exe:
        return {'ok': False, 'error': 'ttyd not installed'}
    if host != '127.0.0.1' and not cred:
        return {'ok': False, 'error': 'refusing to expose an unauthenticated shell'}
    tmux = _tmux()
    args = [exe, '-p', str(port), '-i', host, '-W']
    if cred:
        args += ['-c', cred]
    args += ['-b', '/t/%d' % port,
             # With mouse reporting on, macOS needs this before Option-drag
             # can select anything.
             '-t', 'macOptionClickForcesSelection=true',
             '-t', 'rightClickSelectsWord=true',
             # ttyd's client installs a `beforeunload` handler, so the browser
             # asked "leave site?" on every refresh. That warning was true once
             # and is not any more: the pane lives in tmux and ttyd `attach`es
             # to it rather than running the command itself, so a reload drops a
             # websocket and reattaches to the same conversation. There is
             # nothing left to lose by closing the tab, and a prompt guarding
             # nothing only trains you to click through prompts.
             '-t', 'disableLeaveAlert=true',
             '-t', 'fontSize=%d' % font,
             '-t', 'fontFamily=SFMono-Regular,Menlo,monospace',
             '-t', 'theme={"background":"#171614","foreground":"#ece8e1"}',
             tmux, 'attach-session', '-t', target(thread_id)]
    PROCS[str(thread_id)] = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
    end = time.time() + 6
    while time.time() < end:
        if _port_open(host, port):
            touch(thread_id, port=port, host=host)
            return {'ok': True, 'port': port}
        time.sleep(0.15)
    return {'ok': False, 'error': 'ttyd did not come up on port %d' % port}


def kill_ttyd_on(port, host='127.0.0.1'):
    """Terminate the ttyd listening on `port`, whoever started it.

    A handle is not enough. A ttyd outlives the `serve.py` that spawned it -- on
    a restart the new process *adopts* the port and holds no handle to kill --
    and an adopted ttyd is exactly the one that has to go when a session dies,
    because it is still running whatever command it was born with. Until this,
    a restart across a change to that command left the old command serving:
    ttyds spawned with `tmux new -A` went on resurrecting killed conversations
    for as long as they lived, which looked exactly like the fix not working.

    Only a process whose comm is `ttyd` is signalled, so a mistaken port cannot
    take something else down with it.
    """
    try:
        r = subprocess.run(['ss', '-lntpH', 'sport = :%d' % int(port)],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    killed = False
    for pid in set(re.findall(r'pid=(\d+)', r.stdout)):
        try:
            with open('/proc/%s/comm' % pid) as fh:
                if fh.read().strip() != 'ttyd':
                    continue
            os.kill(int(pid), signal.SIGTERM)
            killed = True
        except OSError:
            continue
    return killed


def stop_ttyd(thread_id):
    row = load().get(str(thread_id)) or {}
    proc = PROCS.pop(str(thread_id), None)
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
    elif row.get('port'):
        kill_ttyd_on(row['port'], row.get('host') or '127.0.0.1')
    d = load()
    if str(thread_id) in d:
        d[str(thread_id)].pop('port', None)
        save(d)


# Claude Code names its own conversations: it writes an `ai-title` line into the
# transcript and rewrites it as the subject moves. That is the right name for
# the list -- it describes the conversation rather than its delivery mechanism,
# and it exists for sessions that never touched Discord at all.
