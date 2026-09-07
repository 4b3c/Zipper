"""The queue, the SSE bus, and the refresh.

One list of events in `Inbox/feed.json`, the subscribers watching it, and the
fetch that turns changed sources into rows.

Split out of `zipper/serve.py` on 2026-09-07. That file had grown to 2,788
lines, which meant no part of it could be read without loading all of it.
"""
from .base import *
from .base import core, canvas, conversations, events, gh, ics, metrics, usage
from .data import flags


# ---------------------------------------------------------------- refresh

SUBS = []            # live SSE subscriber queues
SUBS_LOCK = threading.Lock()
FEED = []            # the run queue, in arrival order
FEED_LOCK = threading.RLock()

# The queue lives in a file, not just in memory, for two reasons: it survives a
# restart of this server (it used to vanish, so a queue you were halfway through
# was gone), and the Claude session in the terminal can cross items off while the
# dashboard is open. FEED_MTIME remembers our own last write so the watcher can
# tell somebody else's edit from an echo of our own.
FEED_JSON = os.path.join(core.INBOX, 'feed.json')
# 100, pruned by `zipper commit` at the end of a pass. The cap only ever drops
# rows that were ticked off: an open row is outstanding work and must survive
# any amount of history piling up behind it.
FEED_MAX = 100
FEED_MTIME = [0.0]

def _feed_key(text):
    import hashlib
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]

def _feed_transient(text):
    """Belt and braces: anything about the terminal itself is never a queue row.

    Nothing should reach this any more -- conversation lifecycle goes out on
    `status` now -- but publish() is the only chokepoint between a message and
    the queue, so the guard stays.
    """
    return text.startswith('terminal ')

def feed_rows():
    with FEED_LOCK:
        return [dict(r) for r in FEED]

def feed_load():
    # `FEED[:] = rows`, not `FEED = rows`. The list is shared across the modules
    # that split out of this file, and rebinding the name would leave every
    # other module holding the old empty list.
    try:
        blob = json.load(open(FEED_JSON, encoding='utf-8'))
        rows = [r for r in blob.get('rows', []) if r.get('key') and r.get('text')]
    except Exception:
        rows = []
    with FEED_LOCK:
        FEED[:] = rows
    return rows

def feed_prune(rows, cap=FEED_MAX):
    """Trim to `cap`, dropping the oldest *ticked* rows only.

    The old truncation sliced the tail regardless of state, so a busy run could
    silently discard outstanding work to make room for history. Open rows are
    kept whatever the count; ticked ones are the compressible part."""
    rows = [r for r in rows if r.get('key')]
    if len(rows) <= cap:
        return rows
    open_rows = [r for r in rows if not r.get('done')]
    done_rows = [r for r in rows if r.get('done')]
    keep = max(0, cap - len(open_rows))
    keep_done = set(id(r) for r in done_rows[-keep:]) if keep else set()
    return [r for r in rows if not r.get('done') or id(r) in keep_done]


def feed_save():
    with FEED_LOCK:
        rows = feed_prune(list(FEED))
        tmp = FEED_JSON + '.tmp'
        try:
            last = json.load(open(FEED_JSON, encoding='utf-8')).get('last_fetch')
        except Exception:
            last = None
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump({'rows': rows, 'last_fetch': last}, fh, indent=1)
        os.replace(tmp, FEED_JSON)          # atomic: the watcher never sees a half-file
        try:
            FEED_MTIME[0] = os.path.getmtime(FEED_JSON)
        except OSError:
            pass

def feed_mark(key, done=None):
    """Cross a queue item off (or back on). Accepts a unique key prefix so it is
    typeable from the terminal."""
    with FEED_LOCK:
        hits = [r for r in FEED if r['key'] == key] or \
               [r for r in FEED if r['key'].startswith(key)]
        if not hits:
            return {'ok': False, 'error': 'no queue item matching %r' % key}
        if len(hits) > 1:
            return {'ok': False, 'error': '%r matches %d items' % (key, len(hits))}
        r = hits[0]
        want = (not r.get('done')) if done is None else done
        r['done'] = datetime.datetime.now().isoformat(timespec='seconds') if want else None
        feed_save()
        return {'ok': True, 'key': r['key'], 'done': bool(r['done']), 'text': r['text']}

def feed_mark_all():
    """Cross off every row still open, in one write. --mark takes a single key, so
    clearing a whole run used to be a shell loop over --queue -- ten separate saves,
    ten watcher pushes. This is one save and one push. It only ever crosses off:
    nothing here restores a row, so it cannot undo a deliberate un-tick."""
    with FEED_LOCK:
        now = datetime.datetime.now().isoformat(timespec='seconds')
        hits = [r for r in FEED if not r.get('done')]
        for r in hits:
            r['done'] = now
        if hits:
            feed_save()
        return {'ok': True, 'marked': len(hits)}


NOTES = [[], 0.0]        # last computed note changes, and when
NOTES_TTL = 4.0

def note_rows(force=False):
    """Notes changed since the last bookkeeping pass, straight from git.

    Not queue rows: they are cleared by committing, never by ticking, so they
    render as their own section with no tick boxes. Recomputed on a short TTL
    rather than per request -- another conversation editing the vault is the
    normal case, and the card has to show that without waiting for a refresh.
    """
    now = time.time()
    if not force and now - NOTES[1] < NOTES_TTL:
        return NOTES[0]
    from .. import runqueue
    try:
        rows = runqueue.note_changes()
    except Exception:
        rows = NOTES[0]
    NOTES[0], NOTES[1] = rows, now
    return rows


def publish(kind, text='', **extra):
    ev = {'kind': kind, 'text': text, 'at': datetime.datetime.now().strftime('%H:%M:%S')}
    ev.update(extra)
    if kind == 'diff' and not _feed_transient(text):
        with FEED_LOCK:
            key = _feed_key(text)
            if any(r['key'] == key for r in FEED):
                return                      # same fact twice is not two queue items
            # who/when are optional by design: a push knows both, a calendar
            # entry knows when it is but not who added it, a note edit knows
            # neither. Absent beats guessed -- a fabricated author is worse
            # than no author.
            row = {'key': key, 'at': ev['at'], 'text': text, 'done': None,
                   'system': extra.get('system', 'other'),
                   'action': extra.get('action', ''),
                   'clears': 'tick'}
            for f in ('who', 'when', 'target'):
                if extra.get(f):
                    row[f] = extra[f]
            FEED.append(row)
            feed_save()
        ev['key'] = key
        ev['done'] = None
    with SUBS_LOCK:
        for q in list(SUBS):
            q.append(ev)

def feed_watch(interval=1.0):
    """Push the queue when the file moves under us — that is how a cross-off from
    the terminal reaches an open dashboard."""
    last = None
    while True:
        time.sleep(interval)
        try:
            m = os.path.getmtime(FEED_JSON)
        except OSError:
            continue
        if last is None or m == last:
            last = m
            continue
        last = m
        if abs(m - FEED_MTIME[0]) < 1e-6:
            continue                        # our own write, already broadcast
        feed_load()
        publish('feed', rows=feed_rows())

def notes_watch(interval=4.0):
    """Push the changed-note list when the working tree moves.

    Nothing writes a file we could watch for this -- the signal is git's own
    view of the tree, and another conversation editing the vault produces no
    event here at all. So it is polled, and only published when it differs."""
    last = None
    while True:
        time.sleep(interval)
        try:
            rows = note_rows(force=True)
        except Exception:
            continue
        key = [(r['action'], r['path']) for r in rows]
        if key == last:
            continue
        last = key
        publish('notes', rows=rows)

def snapshot_data():
    """What the diff is measured against. Keys only — cheap to compare."""
    cal = set()
    for f in glob.glob(os.path.join(core.INBOX, 'calendar-*.json')):
        try:
            for e in json.load(open(f, encoding='utf-8'))['events']:
                # Carry the uid: it is the same across every occurrence of a
                # recurring series, and emit_diff groups on it. Without it a new
                # weekly class reports one row per expanded date.
                cal.add((e['start'], e['summary'], e.get('uid') or ''))
        except Exception:
            pass
    canvas = {}
    try:
        for r in json.load(open(canvas.CANVAS_JSON, encoding='utf-8'))['items']:
            canvas[r['title']] = r['submitted']
    except Exception:
        pass
    repos = {}
    try:
        for r in json.load(open(core.GH_JSON, encoding='utf-8'))['repos']:
            repos[r['name']] = r.get('pushed_at', '')
    except Exception:
        pass
    return {'cal': cal, 'canvas': canvas, 'repos': repos, 'flags': set(flags())}

CADENCE = {1: 'daily', 7: 'weekly', 14: 'fortnightly', 28: '4-weekly'}


def _shape(starts):
    """Describe a run of occurrence dates: '(weekly x58, through 2027-10-06)'.

    Only names a cadence when every gap is the same; a series with holidays cut
    out of it says 'repeats' rather than inventing a rhythm it does not have.
    """
    if len(starts) < 2:
        return ''
    days = sorted({(datetime.date.fromisoformat(b[:10])
                    - datetime.date.fromisoformat(a[:10])).days
                   for a, b in zip(starts, starts[1:])})
    word = CADENCE.get(days[0]) if len(days) == 1 else None
    return '  (%s \u00d7%d, through %s)' % (word or 'repeats', len(starts), starts[-1][:10])


def _window_guard():
    """The slice of the ingest window that did not move since the last fetch.

    parse_ics expands recurrence into a -180/+400 day window *relative to today*,
    so the window slides. An event 400 days out crosses the front edge simply
    because a day passed, and reporting that as `+ calendar` is a lie: nobody
    added anything. Same at the back edge when a series scrolls out of range.

    Only events inside the intersection of the old and new windows can have
    genuinely changed, so that is what gets reported. The intersection is the
    window shrunk by however many days actually elapsed -- read from the last
    fetch stamp, not assumed to be one, since a box that was off for a week
    slides seven days at once.
    """
    last = None
    try:
        last = json.load(open(FEED_JSON, encoding='utf-8')).get('last_fetch')
    except Exception:
        pass
    try:
        slide = (core.TODAY - datetime.date.fromisoformat(last[:10])).days
    except Exception:
        slide = 1
    slide = max(1, min(slide, 400))
    return ((core.TODAY - datetime.timedelta(days=180 - slide)).isoformat(),
            (core.TODAY + datetime.timedelta(days=400 - slide)).isoformat())


def _cal_events(keys, action, lo, hi):
    """One event per series, not per occurrence.

    parse_ics expands RRULE into concrete dates, so before this the day CSE 434's
    lab appeared the feed published 58 identical-looking rows -- one calendar
    entry drowning out the two real changes in the same run. Every occurrence of
    a series shares an ICS uid, so group on it and state the shape of the run.
    Events with no uid fall back to their own start+summary and stay separate.
    """
    groups = {}
    for start, summary, uid in keys:
        groups.setdefault(uid or '%s|%s' % (start, summary), (summary, []))[1].append(start)
    out = []
    for summary, starts in groups.values():
        starts.sort()
        if not (lo <= starts[0][:10] <= hi):
            continue                    # window edge, not a real change
        out.append({'system': 'calendar', 'action': action, 'when': starts[0],
                    'text': '%s calendar  %s  %s%s'
                            % ('+' if action == 'add' else '-', starts[0],
                               summary, _shape(starts))})
    return sorted(out, key=lambda e: e['when'])


def _push_who(name, after_repos):
    """'Abram' when the newest commits in the window are his.

    On a personal repo that is always true and says nothing. On an ASU-LL repo
    it is the whole point: commits_recent is the team's and only his own work
    should move last_touched, so a push that is not his is a different fact.
    """
    try:
        for r in json.load(open(core.GH_JSON, encoding='utf-8'))['repos']:
            if r['name'] == name and r.get('commits'):
                return 'Abram' if any(c.get('mine') for c in r['commits'][:5]) else 'team'
    except Exception:
        pass
    return None


def emit_diff(before, after):
    """Turn the difference between two fetches into typed events.

    Every row is {system, action, details} with `who` and `when` where the
    source actually knows them -- a push knows both, a calendar entry knows
    when it is but not who put it there. Optional means absent, never guessed.
    """
    lo, hi = _window_guard()
    evs = []
    evs += _cal_events(after['cal'] - before['cal'], 'add', lo, hi)
    evs += _cal_events(before['cal'] - after['cal'], 'remove', lo, hi)
    for title, done in sorted(after['canvas'].items()):
        was = before['canvas'].get(title)
        if was is None:
            evs.append({'system': 'canvas', 'action': 'add', 'who': 'Abram',
                        'text': '+ canvas    %s%s'
                                % (title, '  (already submitted)' if done else '')})
        elif done and not was:
            evs.append({'system': 'canvas', 'action': 'submit', 'who': 'Abram',
                        'text': 'submitted   %s' % title})
    for name, ts in sorted(after['repos'].items()):
        if before['repos'].get(name, '') != ts and before['repos'].get(name) is not None:
            evs.append({'system': 'github', 'action': 'push', 'when': ts[:16],
                        'who': _push_who(name, after['repos']),
                        'text': 'pushed      %s  %s' % (name, ts[:16])})
    for e in evs:
        publish('diff', e.pop('text'), **e)
    # Flags deliberately do NOT become queue rows. A flag is a condition derived
    # fresh from current state, not an event: ticking one off is meaningless
    # because it re-fires on the next run, and worse, it reads as handled while
    # the project it names goes on stalling. They surface on Signals, which
    # renders whatever is true right now. See zipper/runqueue.py.
    with FEED_LOCK:
        try:
            blob = json.load(open(FEED_JSON, encoding='utf-8'))
        except Exception:
            blob = {'rows': []}
        blob['last_fetch'] = datetime.datetime.now().isoformat(timespec='seconds')
        with open(FEED_JSON + '.tmp', 'w', encoding='utf-8') as fh:
            json.dump(blob, fh, indent=1)
        os.replace(FEED_JSON + '.tmp', FEED_JSON)
        try:
            FEED_MTIME[0] = os.path.getmtime(FEED_JSON)
        except OSError:
            pass
    return len(evs)

def do_refresh():
    """Runs ONCE per launch, never on a page reload. Each source publishes as it
    lands, so the page fills in live instead of waiting for the slowest one."""
    with LOCK:
        if STATE['refreshing']:
            return
        STATE['refreshing'] = True
    publish('status', 'fetching\u2026')
    before = snapshot_data()
    err, total = [], 0
    try:
        core.TODAY = datetime.date.today()
        steps = [('calendars', ics.cmd_calendars, {}),
                 ('github', gh.cmd_github, {'since_days': 30, 'full': False})]
        if os.environ.get('CANVAS_TOKEN'):
            steps.append(('canvas', canvas.cmd_canvas, {'file': None, 'days': 60}))
        # Run the sources concurrently: a Canvas ICS feed alone can take ~6s to
        # generate, and GitHub has no reason to queue behind it.
        dlock = threading.Lock()

        def run(label, fn, kw):
            nonlocal before, total
            try:
                fn(argparse.Namespace(**kw))
            except Exception as e:
                err.append('%s: %s' % (label, e))
                publish('diff', 'error       %s: %s' % (label, e))
            with dlock:                 # snapshot/diff is shared state
                after = snapshot_data()
                total += emit_diff(before, after)
                before = after
            publish('source', label)

        threads = [threading.Thread(target=run, args=st, daemon=True) for st in steps]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        with LOCK:
            STATE['refreshing'] = False
            STATE['generation'] += 1
            STATE['last_error'] = '; '.join(err)
            STATE['last_refresh'] = datetime.datetime.now().isoformat(timespec='seconds')
        # Nothing is not an event. A "no changes" row was a queue item that
        # said no work had arrived, which is the one thing a queue of work
        # should never contain -- it read as something to deal with and could
        # be ticked off. Silence says it better.
        publish('notes', rows=note_rows())
        publish('done', '')
