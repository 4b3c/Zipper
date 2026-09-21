"""zipper.box

What the machine itself is doing: CPU, memory, disk, uptime, and whether the
services are running the code that is checked in.

Stdlib and `/proc` only -- no psutil, same rule as the rest of the server. Every
number here is read at call time and cached for a few seconds; nothing is
written to the vault, because none of it is a conclusion about anything. It is
the box's vital signs, and a vital sign is only worth reading live.

The one judgement in this file is `stale`: a unit whose process started *before
the code it runs was last edited*. On 2026-09-17 exactly that combination --
`zipper-web` restarted thirty seconds before the change it needed -- cost an
afternoon, and nothing on any surface said so. Now something does.

**Against file mtimes, not the commit.** The first version compared the start
time to HEAD's timestamp, which is a different question and gets the common case
backwards: the normal order here is edit, restart, verify, commit, so HEAD lands
seconds *after* a restart that already picked the change up, and every commit
lit the flag on a process running exactly the right code. Committing changes no
code. Editing does.
"""
import os, json, time, shutil, subprocess, threading

from .core import INBOX

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNITS = ('zipper-web', 'zipper-discord', 'zipper-fetch.timer')

# A day of samples at a minute each. The file is machine state -- `Inbox/` is
# gitignored, and losing it costs a day of history and nothing else.
HISTORY_JSON = os.path.join(INBOX, 'box-history.json')
SAMPLE_EVERY = 60
WINDOW = 24 * 3600

TTL = 5.0
# The server is threaded, so two page loads can land here at once. Both the
# cache and the CPU delta are read-modify-write on module state: without the
# lock, concurrent requests consume each other's `prev` sample and the second
# one divides by a zero interval.
_LOCK = threading.Lock()
_CACHE = {'at': 0.0, 'data': None}

# Two baselines, deliberately. A CPU percentage is meaningless without the
# window it covers, and the two readers want different windows: the card wants
# "busy right now" and is read whenever a page renders, the series wants a true
# minute and is read on a timer. Sharing one baseline let a page load consume
# the sampler's `prev`, so a plotted point silently covered however long it had
# been since someone last opened the dashboard rather than the minute it claims.
_CPU = {'at': 0.0, 'idle': 0.0, 'total': 0.0}          # the live card
_CPU_SERIES = {'at': 0.0, 'idle': 0.0, 'total': 0.0}   # the minute series


def _run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=4).stdout.strip()
    except Exception:
        return ''


def _cpu_sample():
    try:
        with open('/proc/stat') as fh:
            f = fh.readline().split()[1:]
    except Exception:
        return None, None
    v = [float(x) for x in f]
    return v[3] + (v[4] if len(v) > 4 else 0.0), sum(v)


def cpu_pct(state=None):
    """Busy percentage over the window since `state` was last read.

    `state` is the caller's own baseline -- `_CPU` for the live card, or
    `_CPU_SERIES` for the plotted minute -- so that two readers on different
    schedules cannot consume each other's `prev` and shorten each other's
    windows.

    The first call has nothing to difference against, so it takes a short real
    sample rather than reporting a number computed from boot -- an average over
    88 days of uptime is not what anyone reading a dashboard means by "CPU".
    That 120ms reading is honest for the 120ms it covers, and on a one-core box
    a service starting up genuinely pins the core for about that long. It is
    still the wrong number to *plot*: see `_sampler`, which throws its first one
    away rather than filing a peak in a slot that means an average.
    """
    st = _CPU if state is None else state
    idle, total = _cpu_sample()
    if idle is None:
        return None
    prev_total = st['total']
    if not prev_total or total <= prev_total or time.time() - st['at'] > 300:
        time.sleep(0.12)
        idle2, total2 = _cpu_sample()
        if idle2 is None or total2 <= total:
            return None
        st.update(at=time.time(), idle=idle2, total=total2)
        return max(0.0, min(100.0, 100.0 * (1 - (idle2 - idle) / (total2 - total))))
    pct = 100.0 * (1 - (idle - st['idle']) / (total - prev_total))
    st.update(at=time.time(), idle=idle, total=total)
    return max(0.0, min(100.0, pct))


def meminfo():
    out = {}
    try:
        with open('/proc/meminfo') as fh:
            for ln in fh:
                k, _, v = ln.partition(':')
                out[k] = float(v.split()[0]) * 1024
    except Exception:
        return None
    total = out.get('MemTotal')
    if not total:
        return None
    # MemAvailable, not MemFree: page cache is memory the box can have back on
    # demand, and counting it as used reads as a machine permanently at 90%.
    avail = out.get('MemAvailable', out.get('MemFree', 0))
    swt, swf = out.get('SwapTotal', 0), out.get('SwapFree', 0)
    return {'total': total, 'used': total - avail, 'pct': 100.0 * (1 - avail / total),
            'swap_total': swt, 'swap_used': swt - swf}


def disk(path='/'):
    try:
        u = shutil.disk_usage(path)
    except Exception:
        return None
    return {'total': float(u.total), 'used': float(u.used),
            'pct': 100.0 * u.used / u.total if u.total else 0.0}


def uptime():
    try:
        with open('/proc/uptime') as fh:
            return float(fh.read().split()[0])
    except Exception:
        return None


def code_epoch(root=CODE):
    """When the source this box runs was last written.

    `.py` under the package and the hooks -- the files a running process has
    already imported and will not pick up again. `__pycache__` is skipped: it is
    written *by* the import, so counting it would make every start look stale
    against itself.
    """
    newest = None
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs
                   if d not in ('__pycache__', '.git', 'node_modules')]
        for fn in files:
            if not fn.endswith('.py'):
                continue
            try:
                m = os.path.getmtime(os.path.join(base, fn))
            except OSError:
                continue
            if newest is None or m > newest:
                newest = m
    return None if newest is None else int(newest)


def _unit(name, code_at):
    """One service: is it up, since when, and is that before the code it runs.

    `ActiveEnterTimestampMonotonic` would be the precise field, but it is
    measured from boot and would have to be converted back against
    `/proc/uptime` to compare with a commit; the wall-clock field is already in
    the units the question is asked in.
    """
    state = _run('systemctl', 'is-active', name) or 'unknown'
    started = None
    raw = _run('systemctl', 'show', name, '-p', 'ActiveEnterTimestamp')
    _, _, val = raw.partition('=')
    val = val.strip()
    if val:
        out = _run('date', '-d', val, '+%s')
        if out.isdigit():
            started = int(out)
    # A timer is not a long-lived process holding imported code, so the
    # question does not apply to it.
    stale = bool(started and code_at and code_at > started
                 and name != 'zipper-fetch.timer')
    return {'name': name, 'state': state, 'started': started, 'stale': stale}


def read(force=False):
    with _LOCK:
        return _read(force)


def _read(force):
    now = time.time()
    if not force and _CACHE['data'] and now - _CACHE['at'] < TTL:
        return _CACHE['data']
    code_at = code_epoch()
    data = {'at': now, 'cpu': cpu_pct(), 'mem': meminfo(), 'disk': disk(),
            'uptime': uptime(), 'load': list(os.getloadavg()),
            'cpus': os.cpu_count() or 1, 'code_at': code_at,
            'units': [_unit(u, code_at) for u in UNITS]}
    _CACHE.update(at=now, data=data)
    return data


# ------------------------------------------------------------------ history
#
# A single reading answers "is it fine now"; the question he actually asks of a
# server is "is this going somewhere". That needs a series, and nothing on this
# box kept one -- so the dashboard keeps it: one sample a minute, a day deep,
# written by the web process because that is the one thing always running.
#
# Persisted rather than held in memory: `systemctl restart zipper-web` is a
# normal move here, and history that a restart erases would be empty exactly
# when something has just gone wrong.

_HLOCK = threading.Lock()


def history():
    """The samples, oldest first. Anything older than the window is dropped on
    read as well as on write, so a long gap cannot leave a stale head."""
    try:
        with open(HISTORY_JSON, encoding='utf-8') as fh:
            rows = json.load(fh).get('samples', [])
    except Exception:
        return []
    cut = time.time() - WINDOW
    return [r for r in rows if isinstance(r, dict) and (r.get('t') or 0) >= cut]


def sample():
    """Append one reading. Safe to call from anywhere; only the sampler does.

    The CPU number is taken against `_CPU_SERIES` rather than reused from
    `read()`, so every plotted point covers the same width of time whether or
    not anyone loaded the dashboard in between.
    """
    b = read(force=True)
    cpu = cpu_pct(_CPU_SERIES)
    mem, dsk = b.get('mem'), b.get('disk')
    row = {'t': round(b['at']),
           'cpu': None if cpu is None else round(cpu, 1),
           'mem': None if not mem else round(mem['pct'], 1),
           'disk': None if not dsk else round(dsk['pct'], 1)}
    with _HLOCK:
        rows = history()
        rows.append(row)
        tmp = HISTORY_JSON + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump({'samples': rows}, fh)
            # Rename, not write-in-place: a process killed mid-write would
            # otherwise leave truncated JSON, and `history()` would read that as
            # no history at all rather than as a damaged file.
            os.replace(tmp, HISTORY_JSON)
        except Exception:
            pass
    return row


def _sampler():
    # Prime the series baseline and drop what it returns. That first reading is
    # a 120ms sample taken while this very process is starting -- imports, the
    # feed watcher, ttyd -- and on one core that really is ~100%. True, and the
    # wrong shape for this series: every other point is a 60s average, so
    # plotting it put a half-second peak in a slot that means a minute and drew
    # a spike on every restart that looked exactly like the trouble the graph
    # exists to warn about. Cost: the first point arrives a minute in.
    cpu_pct(_CPU_SERIES)
    while True:
        time.sleep(SAMPLE_EVERY)
        try:
            sample()
        except Exception:
            pass


def start_sampler():
    threading.Thread(target=_sampler, daemon=True, name='box-sampler').start()


if __name__ == '__main__':
    print(json.dumps(read(force=True), indent=1))
    print('samples on disk:', len(history()))
