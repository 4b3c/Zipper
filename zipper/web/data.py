"""What the panels are made of.

Calendar days, Canvas items, tasks and their ranking, and the flags. Reads
what the engine already wrote; owns none of it.

Split out of `zipper/serve.py` on 2026-09-07. That file had grown to 2,788
lines, which meant no part of it could be read without loading all of it.
"""
from .base import *
from .base import core, canvas, conversations, events, gh, ics, metrics, usage


# ---------------------------------------------------------------- data

def _d(iso):
    try:
        return datetime.date(*map(int, iso[:10].split('-')))
    except Exception:
        return None

def upcoming(days=10):
    horizon = (core.TODAY + datetime.timedelta(days=days)).isoformat()
    cstat = canvas.canvas_status_map()
    rows = []
    for f in sorted(glob.glob(os.path.join(core.INBOX, 'calendar-*.json'))):
        blob = json.load(open(f, encoding='utf-8'))
        for e in blob['events']:
            d = e['start'][:10]
            if core.TODAY.isoformat() <= d <= horizon:
                done = cstat.get((d, core._norm_title(e['summary']))) if blob['label'] == 'canvas' else None
                rows.append({'date': d, 'time': e['start'][11:], 'label': blob['label'],
                             'summary': e['summary'], 'loc': e.get('location', ''),
                             'done': done, 'start': e['start'], 'end': e.get('end', ''),
                             'uid': e.get('uid', '')})
    rows.sort(key=lambda r: (r['date'], r['time'] or '00:00'))
    return rows


def day_events(day):
    """Every ingested event on one date. `upcoming()` starts at today, so it
    cannot look backwards; the day arrows need to."""
    cstat = canvas.canvas_status_map()
    rows = []
    for f in sorted(glob.glob(os.path.join(core.INBOX, 'calendar-*.json'))):
        blob = json.load(open(f, encoding='utf-8'))
        for e in blob['events']:
            if e['start'][:10] != day:
                continue
            done = (cstat.get((day, core._norm_title(e['summary'])))
                    if blob['label'] == 'canvas' else None)
            rows.append({'date': day, 'time': e['start'][11:], 'label': blob['label'],
                         'summary': e['summary'], 'loc': e.get('location', ''),
                         'done': done, 'start': e['start'], 'end': e.get('end', ''),
                         'uid': e.get('uid', ''), 'url': e.get('url', '')})
    rows.sort(key=lambda r: (r['time'] or '00:00', r['summary']))
    return rows


def today_split(day=None):
    """One day, split the way it reads: things with a clock on the right,
    things merely due on the left. All-day items strike through when submitted;
    timed ones strike through once the clock has passed — but only on today,
    since 'already happened' is meaningless on a day he is looking ahead to."""
    day = day or core.TODAY.isoformat()
    is_today = day == core.TODAY.isoformat()
    now = datetime.datetime.now().strftime('%H:%M')
    allday, timed = [], []
    for e in day_events(day):
        if e['time']:
            e['past'] = is_today and e['time'] < now
            timed.append(e)
        else:
            allday.append(e)
    return allday, timed


def canvas_items():
    """Read through `canvas.items()`, never from the file directly.

    That is what applies the hand cross-offs -- see `canvas.OVERRIDES`. Loading
    `canvas.json` here for itself is what let a crossed-off assignment come back
    as outstanding the next time he opened Canvas: the extension rewrites that
    file wholesale, and this saw the rewrite without the overrides.
    """
    return canvas.items()


def canvas_outstanding():
    return [r for r in canvas_items()
            if not canvas.is_done(r) and r['due'][:10] >= core.TODAY.isoformat()]


def task_text(raw):
    """Exactly the engine's normalisation, so the dashboard, ledger and queue all
    key a task the same way. A naive character class stops inside [[Note]] and
    leaves a trailing ']]' on every task that names a project."""
    t = re.sub(r'\[[a-z_]+::\s*(?:\[\[[^\]]+\]\]|[^\]]*)\]', '', raw)
    return re.sub(r'#\w+', '', t).strip()


def open_tasks():
    out = []
    for p in sorted(glob.glob(os.path.join(core.VAULT, 'Tasks', '*.md'))):
        for line in open(p, encoding='utf-8'):
            m = core.TASK_RE.match(line)
            if not m or m.group(1).lower() == 'x':
                continue
            raw = m.group(2)
            due = re.search(r'\[due::\s*(\d{4}-\d{2}-\d{2})\]', raw)
            proj = re.search(r'\[project::\s*\[\[([^\]]+)\]\]', raw)
            out.append({'text': task_text(raw),
                        'due': due.group(1) if due else '',
                        'project': proj.group(1) if proj else '',
                        'next': '#next' in raw,
                        'overdue': bool(due and due.group(1) < core.TODAY.isoformat())})
    return out


def priority(it):
    """Deliberately simple and explainable — urgency, then a little weight.

    Not a metric anyone is scored on, just a sort order. Kept legible so a
    surprising position can be argued with rather than trusted.
    """
    d = _d(it['due']) if it['due'] else None
    days = (d - core.TODAY).days if d else None
    base = ({None: 5}.get(days) if days is None else
            100 if days < 0 else 70 if days == 0 else 55 if days == 1 else
            40 if days <= 3 else 25 if days <= 7 else 10)
    bonus = 12 if it.get('next') else 0
    pts = it.get('points') or 0
    bonus += 8 if pts >= 100 else 4 if pts >= 50 else 0
    if it['source'] == 'canvas':
        bonus += 3                      # somebody else set this deadline
    return base + bonus


def ranked(limit=10):
    """Canvas work and self-reported tasks in one list, most pressing first."""
    items = []
    # Not `canvas_outstanding()`: crossed-off work stays on this list and sinks,
    # rather than disappearing from it. A struck-through row is him seeing his
    # own decision reflected back; a row that vanishes is indistinguishable from
    # the cross-off having failed, which is the complaint this whole mechanism
    # exists to answer.
    for r in canvas_items():
        if r['submitted'] or r['due'][:10] < core.TODAY.isoformat():
            continue
        items.append({'source': 'canvas', 'title': r['title'], 'due': r['due'][:10],
                      'tag': r['course'], 'url': r['url'], 'points': r.get('points'),
                      'next': False, 'elsewhere': r.get('elsewhere', ''),
                      'done': bool(r.get('done_by_hand'))})
    for t in open_tasks():
        items.append({'source': 'task', 'title': t['text'], 'due': t['due'],
                      'tag': t['project'], 'url': '', 'points': 0,
                      'next': t['next'], 'done': False})
    for it in items:
        it['score'] = priority(it)
        it['overdue'] = bool(it['due'] and it['due'] < core.TODAY.isoformat())
        it['key'] = override_key(it)
    # Crossed-off work sinks, whatever it scores. The partition this replaces was undone
    # by the sort on the very next line, so a struck-through row kept its place at the top
    # and spent a slot in the top ten on something already handled — which reads from the
    # browser as the cross-off not having worked.
    items.sort(key=lambda i: (i['done'], -i['score'], i['due'] or '9999', i['title']))
    return items[:limit], items


def override_key(it):
    """The name the browser sends back to cross something off.

    Canvas keys come from `canvas._ov_key` -- the same normalization the store
    and the agenda use, so a key minted here matches the one looked up there.
    The store itself belongs to `canvas.py`, which is the module every surface
    now reads Canvas through.
    """
    if it['source'] == 'canvas':
        return canvas._ov_key(it['tag'], it['title'])
    return 'task:%s|%s' % (it['tag'], it['title'])

def toggle_done(key):
    """Cross something off by hand.

    A task is ticked in its own markdown file, so the vault stays the source of
    truth and the ledger sees the close. Canvas cannot be written to, so those
    live in overrides.json and are purely a display override.
    """
    if key.startswith('task:'):
        title = key.split('|', 1)[1]
        for p in sorted(glob.glob(os.path.join(core.VAULT, 'Tasks', '*.md'))):
            lines = open(p, encoding='utf-8').read().split('\n')
            hit = False
            for i, line in enumerate(lines):
                m = core.TASK_RE.match(line)
                if not m:
                    continue
                if task_text(m.group(2)) != title:
                    continue
                done = m.group(1).lower() == 'x'
                lines[i] = line.replace('[x]' if done else '[ ]',
                                        '[ ]' if done else '[x]', 1)
                hit = True
                break
            if hit:
                open(p, 'w', encoding='utf-8').write('\n'.join(lines))
                return {'ok': True, 'where': os.path.basename(p), 'done': not done}
        return {'ok': False, 'error': 'task not found'}
    return {'ok': True, 'where': 'overrides.json',
            'done': canvas.toggle_override(key)}

def flags():
    try:
        return json.load(open(os.path.join(core.INBOX, 'queue.json'), encoding='utf-8')).get('flags', [])
    except Exception:
        return []


def content_sig():
    """Hash of what the page shows, excluding fetch timestamps."""
    import hashlib
    h = hashlib.sha1()
    for e in upcoming():
        h.update(('%s|%s|%s|%s' % (e['date'], e['time'], e['summary'], e['done'])).encode())
    for r in canvas_outstanding():
        h.update(('%s|%s' % (r['due'], r['title'])).encode())
    for t in open_tasks():
        h.update(('%s|%s' % (t['text'], t['due'])).encode())
    for x in flags():
        h.update(x.encode())
    return h.hexdigest()[:12]
