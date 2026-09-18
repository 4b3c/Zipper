"""zipper.box

What the machine itself is doing: CPU, memory, disk, uptime, and whether the
services are running the code that is checked in.

Stdlib and `/proc` only -- no psutil, same rule as the rest of the server. Every
number here is read at call time and cached for a few seconds; nothing is
written to the vault, because none of it is a conclusion about anything. It is
the box's vital signs, and a vital sign is only worth reading live.

The one judgement in this file is `stale`: a unit whose process started *before*
the current commit is serving code that is not what HEAD says. On 2026-09-17
exactly that combination -- `zipper-web` restarted thirty seconds before the
commits it needed -- cost an afternoon, and nothing on any surface said so. Now
something does.
"""
import os, json, time, shutil, subprocess, threading

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNITS = ('zipper-web', 'zipper-discord', 'zipper-fetch.timer')

TTL = 5.0
# The server is threaded, so two page loads can land here at once. Both the
# cache and the CPU delta are read-modify-write on module state: without the
# lock, concurrent requests consume each other's `prev` sample and the second
# one divides by a zero interval.
_LOCK = threading.Lock()
_CACHE = {'at': 0.0, 'data': None}
_CPU = {'at': 0.0, 'idle': 0.0, 'total': 0.0}


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


def cpu_pct():
    """Busy percentage since the last call.

    The first call has nothing to difference against, so it takes a short real
    sample rather than reporting a number computed from boot -- an average over
    88 days of uptime is not what anyone reading a dashboard means by "CPU".
    """
    idle, total = _cpu_sample()
    if idle is None:
        return None
    prev_total = _CPU['total']
    if not prev_total or total <= prev_total or time.time() - _CPU['at'] > 300:
        time.sleep(0.12)
        idle2, total2 = _cpu_sample()
        if idle2 is None or total2 <= total:
            return None
        _CPU.update(at=time.time(), idle=idle2, total=total2)
        return max(0.0, min(100.0, 100.0 * (1 - (idle2 - idle) / (total2 - total))))
    pct = 100.0 * (1 - (idle - _CPU['idle']) / (total - prev_total))
    _CPU.update(at=time.time(), idle=idle, total=total)
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


def head_epoch(repo=CODE):
    try:
        v = _run('git', '-C', repo, 'log', '-1', '--format=%ct')
        return int(v) if v else None
    except Exception:
        return None


def _unit(name, head):
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
    stale = bool(started and head and head > started and name != 'zipper-fetch.timer')
    return {'name': name, 'state': state, 'started': started, 'stale': stale}


def read(force=False):
    with _LOCK:
        return _read(force)


def _read(force):
    now = time.time()
    if not force and _CACHE['data'] and now - _CACHE['at'] < TTL:
        return _CACHE['data']
    head = head_epoch()
    data = {'at': now, 'cpu': cpu_pct(), 'mem': meminfo(), 'disk': disk(),
            'uptime': uptime(), 'load': list(os.getloadavg()),
            'cpus': os.cpu_count() or 1, 'head': head,
            'units': [_unit(u, head) for u in UNITS]}
    _CACHE.update(at=now, data=data)
    return data


if __name__ == '__main__':
    print(json.dumps(read(force=True), indent=1))
