#!/usr/bin/env python3
"""
brain.py - the command line for the space/ vault.

Stdlib only. Run it from anywhere:

    BRAIN_VAULT=~/path/to/vault python3 brain/brain.py <command>

Commands:
    today                       create (or find) today's log note
    lint                        validate every note's frontmatter against the schema
    status                      regenerate Meta/Status.md - the generated snapshot
    sync                        derive last_touched on notes from Log/ backlinks
    touch <Note> [--date D]     mark a note as worked on
    metric <key> <value>        append a dated row to Metrics/metrics.csv
    metrics                     print the metric history as a table
    decide "<title>"            scaffold a decision note
    ingest-ics <url|file>       pull a calendar/Canvas ICS feed into Inbox/
                                --match REGEX keeps only matching events; a URL is
                                remembered and refetched by `catchup`
    calendars                   refetch every remembered calendar URL
    ingest-budget <csv>         summarize a bank/budget CSV into metrics
    agenda [--days 14]          render Meta/Agenda.md from ingested calendars
"""
import os, sys, re, csv, json, argparse, datetime, urllib.request, glob

HERE    = os.path.dirname(os.path.abspath(__file__))
# The code no longer has to live inside the data. BRAIN_VAULT points at the
# notes; without it we fall back to the parent dir, which is the layout you get
# when this is dropped in as `<vault>/Scripts/`.
VAULT   = os.environ.get('BRAIN_VAULT') or os.path.dirname(HERE)
LOGDIR  = os.path.join(VAULT, 'Log')
METDIR  = os.path.join(VAULT, 'Metrics')
DECDIR  = os.path.join(VAULT, 'Decisions')
EVTDIR  = os.path.join(VAULT, 'Events')
INBOX   = os.path.join(VAULT, 'Inbox')
METADIR = os.path.join(VAULT, 'Meta')
METCSV  = os.path.join(METDIR, 'metrics.csv')
TODAY   = datetime.date.today()

SKIP_DIRS = {'.obsidian', '.git', 'Scripts', 'Inbox', 'Log'}
SKIP_FILES = {'CLAUDE.md', 'README.md'}

ENUMS = {
    'type':   ['project', 'area', 'topic', 'person', 'life', 'moc',
               'decision', 'log', 'tasks', 'class', 'reference', 'view',
               'event'],
    'status': ['active', 'dormant', 'handing-off', 'shipped', 'retired',
               'archived', 'idea', 'past', 'living', 'ongoing',
               'open', 'settled', 'superseded', 'reversed',
               'scheduled', 'debriefed', 'cancelled'],
    'stage':  ['idea', 'designing', 'building', 'shipped', 'selling',
               'verifying', 'closed'],
    'kind':   ['taste', 'tooling', 'skill', 'thread', 'tension', 'channel',
               'record', 'personal'],
    'view_kind': ['query', 'generated'],
}
DATE_RE  = re.compile(r'^\d{4}-\d{2}-\d{2}$')
EVT_DT_RE = re.compile(r'^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$')
MONTH_RE = re.compile(r'^\d{4}(-\d{2})?(-\d{2})?$')
DATE_FIELDS  = ['review', 'note_updated', 'since', 'decided', 'debriefed']
MONTH_FIELDS = ['started', 'ended', 'last_touched', 'shipped', 'target_by', 'met']
NUM_FIELDS   = ['revenue_to_date', 'paying_users', 'customers', 'team_size', 'target']
BOOL_FIELDS  = ['revenue_intent', 'open_loop', 'open_problem', 'open_question',
                'learning', 'dormant_deliberately', 'decision_open']

# ---------------------------------------------------------------- frontmatter

def parse_fm(text):
    """Return (ordered list of (key, raw_value), body). Minimal YAML subset."""
    m = re.match(r'^---\n(.*?)\n---\n?', text, re.S)
    if not m:
        return [], text
    pairs = []
    for line in m.group(1).split('\n'):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if ':' not in line:
            continue
        k, v = line.split(':', 1)
        pairs.append((k.strip(), v.strip()))
    return pairs, text[m.end():]

def fm_dict(pairs):
    return {k: v for k, v in pairs}

def as_list(v):
    v = v.strip()
    if v.startswith('[') and v.endswith(']'):
        inner = v[1:-1].strip()
        return [x.strip() for x in inner.split(',') if x.strip()] if inner else []
    return [v] if v else []

def dump_fm(pairs, body):
    lines = ['---']
    for k, v in pairs:
        lines.append('%s: %s' % (k, v))
    lines.append('---')
    return '\n'.join(lines) + '\n\n' + body.lstrip('\n')

def iter_notes():
    for dp, dn, fn in os.walk(VAULT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in sorted(fn):
            if f.endswith('.md') and f not in SKIP_FILES:
                yield os.path.join(dp, f)

def read_note(p):
    with open(p, encoding='utf-8') as fh:
        return parse_fm(fh.read())

def write_note(p, pairs, body):
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(dump_fm(pairs, body))

def title_of(p):
    return os.path.splitext(os.path.basename(p))[0]

def rel(p):
    return os.path.relpath(p, VAULT)

def set_field(pairs, key, value):
    for i, (k, _) in enumerate(pairs):
        if k == key:
            pairs[i] = (key, value)
            return pairs
    # insert before note_updated if present, else append
    for i, (k, _) in enumerate(pairs):
        if k == 'note_updated':
            pairs.insert(i, (key, value))
            return pairs
    pairs.append((key, value))
    return pairs

# ---------------------------------------------------------------- lint

def cmd_lint(a):
    problems = []
    for p in iter_notes():
        pairs = read_note(p)[0]
        d = fm_dict(pairs)
        name = rel(p)
        if not pairs:
            problems.append((name, 'no frontmatter at all'))
            continue
        required = ('type',) if d.get('type') == 'log' else ('type', 'status')
        for req in required:
            if req not in d:
                problems.append((name, 'missing required field: %s' % req))
        if d.get('type') == 'view':
            if 'view_kind' not in d:
                problems.append((name, 'view with no view_kind (query|generated)'))
            elif d['view_kind'] == 'generated':
                for req in ('source', 'generated'):
                    if req not in d:
                        problems.append((name, 'generated view missing %s' % req))
        if d.get('type') != 'view' and d.get('view_kind'):
            problems.append((name, 'view_kind on a non-view note'))
        if d.get('type') == 'event':
            if d.get('status') not in ('scheduled', 'debriefed', 'cancelled'):
                problems.append((name, 'event status should be scheduled/debriefed/cancelled'))
            for req in ('event_uid', 'event_start'):
                if not d.get(req):
                    problems.append((name, 'event note missing %s' % req))
            if d.get('event_start') and not EVT_DT_RE.match(d['event_start']):
                problems.append((name, 'event_start="%s" should be YYYY-MM-DD or '
                                       'YYYY-MM-DDTHH:MM' % d['event_start']))
            if d.get('status') == 'debriefed' and not d.get('debriefed'):
                problems.append((name, 'debriefed event with no debriefed date'))
        elif d.get('event_uid') or d.get('event_start'):
            problems.append((name, 'event_uid/event_start on a non-event note'))
        if d.get('type') == 'decision':
            if d.get('status') not in ('open', 'settled', 'superseded', 'reversed'):
                problems.append((name, 'decision status should be open/settled/superseded/reversed'))
            if 'review' not in d:
                problems.append((name, 'decision with no review date'))
        for k, allowed in ENUMS.items():
            if k in d and d[k] not in allowed:
                problems.append((name, '%s="%s" not in %s' % (k, d[k], '/'.join(allowed))))
        for k in DATE_FIELDS:
            if k in d and d[k] and not DATE_RE.match(d[k]):
                problems.append((name, '%s="%s" should be YYYY-MM-DD' % (k, d[k])))
        for k in MONTH_FIELDS:
            if k in d and d[k] and not MONTH_RE.match(d[k]):
                problems.append((name, '%s="%s" should be YYYY-MM or YYYY' % (k, d[k])))
        for k in NUM_FIELDS:
            if k in d and d[k] and not re.match(r'^-?\d+(\.\d+)?$', d[k]):
                problems.append((name, '%s="%s" should be a number' % (k, d[k])))
        for k in BOOL_FIELDS:
            if k in d and d[k] not in ('true', 'false'):
                problems.append((name, '%s="%s" should be true/false' % (k, d[k])))
        if d.get('type') == 'project':
            if d.get('revenue_intent') == 'true' and 'revenue_to_date' not in d:
                problems.append((name, 'revenue_intent=true but no revenue_to_date'))
            if d.get('status') == 'active' and not d.get('next_action'):
                problems.append((name, 'active project with no next_action'))
        if d.get('next_action') and len(d['next_action'].split()) < 3:
            problems.append((name, 'next_action looks too vague: "%s"' % d['next_action']))
    if not problems:
        print('lint: clean (%d notes)' % len(list(iter_notes())))
        return 0
    for name, msg in problems:
        print('%-45s %s' % (name, msg))
    print('\n%d problem(s)' % len(problems))
    return 1

# ---------------------------------------------------------------- sync / touch

LINK_RE = re.compile(r'\[\[([^\]|#]+)')
ANCHOR_RE = re.compile(r'\[\[([^\]|]+)')   # keeps the #section; see cmd_status

def cmd_sync(a):
    """Derive last_touched from Log/ backlinks: newest log note that mentions a note wins."""
    latest = {}
    for lp in sorted(glob.glob(os.path.join(LOGDIR, '*.md'))):
        stem = title_of(lp)
        if not DATE_RE.match(stem):
            continue
        with open(lp, encoding='utf-8') as fh:
            text = fh.read()
        for link in LINK_RE.findall(text):
            link = link.strip()
            if link > '' and (link not in latest or stem > latest[link]):
                latest[link] = stem
    changed = 0
    for p in iter_notes():
        t = title_of(p)
        if t not in latest:
            continue
        pairs, body = read_note(p)
        d = fm_dict(pairs)
        month = latest[t][:7]
        cur = d.get('last_touched') or ''
        # Forward only. Log evidence can prove a note was touched, never that it
        # wasn't: `github` dates last_touched from commits, and an unconditional
        # write dragged those back to whatever the newest log mention happened to
        # say. Editing a link out of a log used to silently regress the note.
        if month > cur:
            set_field(pairs, 'last_touched', month)
            write_note(p, pairs, body)
            changed += 1
            print('touched %-40s -> %s' % (t, month))
    print('sync: %d note(s) updated from %d log entr(ies)' %
          (changed, len(glob.glob(os.path.join(LOGDIR, '*.md')))))
    return 0

def cmd_touch(a):
    when = a.date or TODAY.isoformat()
    hits = [p for p in iter_notes() if title_of(p).lower() == a.note.lower()]
    if not hits:
        print('no note titled "%s"' % a.note); return 1
    pairs, body = read_note(hits[0])
    set_field(pairs, 'last_touched', when[:7])
    write_note(hits[0], pairs, body)
    print('%s last_touched -> %s' % (title_of(hits[0]), when[:7]))
    return 0

# ---------------------------------------------------------------- log

LOG_TEMPLATE = """---
type: log
date: {date}
---
# {date} ({day})

## Did
- 

## Decided
- 

## Friction
- 

## Tomorrow
- 
"""

def cmd_today(a):
    os.makedirs(LOGDIR, exist_ok=True)
    day = a.date or TODAY.isoformat()
    p = os.path.join(LOGDIR, day + '.md')
    if not os.path.exists(p):
        dt = datetime.date(*[int(x) for x in day.split('-')])
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write(LOG_TEMPLATE.format(date=day, day=dt.strftime('%A')))
        print('created %s' % rel(p))
    else:
        print('exists  %s' % rel(p))
    return 0

# ---------------------------------------------------------------- metrics

MET_HEADER = ['date', 'key', 'value', 'note', 'source']

def _ensure_metrics():
    os.makedirs(METDIR, exist_ok=True)
    if not os.path.exists(METCSV):
        with open(METCSV, 'w', newline='', encoding='utf-8') as fh:
            csv.writer(fh).writerow(MET_HEADER)

def add_metric(key, value, when=None, note='', source='manual'):
    _ensure_metrics()
    with open(METCSV, 'a', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerow([when or TODAY.isoformat(), key, value, note, source])

def cmd_metric(a):
    add_metric(a.key, a.value, a.date, a.note or '', 'manual')
    print('%s  %s = %s' % (a.date or TODAY.isoformat(), a.key, a.value))
    return 0

def cmd_metrics(a):
    if not os.path.exists(METCSV):
        print('no metrics yet'); return 0
    rows = list(csv.DictReader(open(METCSV, encoding='utf-8')))
    keys = sorted(set(r['key'] for r in rows))
    for k in keys:
        # Several score runs on one date are one measurement re-taken, not a
        # slope. Collapse to the last row per date before reading a trend.
        by_date = {}
        for r in rows:
            if r['key'] == k:
                by_date[r['date']] = r
        series = [by_date[dt] for dt in sorted(by_date)]
        trail = '  '.join('%s:%s' % (r['date'][:7], r['value']) for r in series[-6:])
        first, last = series[0], series[-1]
        delta = ''
        try:
            d = float(last['value']) - float(first['value'])
            if len(series) > 1:
                delta = '  (%+g since %s)' % (d, first['date'][:7])
        except ValueError:
            pass
        print('%-28s %s%s' % (k, trail, delta))
    return 0

# ---------------------------------------------------------------- decisions

DEC_TEMPLATE = """---
type: decision
status: open
decided: {date}
review: {review}
confidence: medium
reversible: true
---
# {title}

## The call


## Why


## What I'm giving up


## What would change my mind
*Be specific. This is the field that makes the decision worth re-reading.*
- 

## Revisit
Set for {review}.

Related: [[Home]]
"""

def cmd_decide(a):
    os.makedirs(DECDIR, exist_ok=True)
    day = a.date or TODAY.isoformat()
    slug = re.sub(r'[^a-z0-9]+', '-', a.title.lower()).strip('-')[:60]
    p = os.path.join(DECDIR, '%s-%s.md' % (day, slug))
    review = (datetime.date(*[int(x) for x in day.split('-')]) +
              datetime.timedelta(days=a.review_days)).isoformat()
    if os.path.exists(p):
        print('exists %s' % rel(p)); return 0
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(DEC_TEMPLATE.format(date=day, review=review, title=a.title))
    print('created %s' % rel(p))
    return 0

# ---------------------------------------------------------------- events

EVT_TEMPLATE = """---
type: event
status: scheduled
event_uid: {uid}
event_start: {start}
event_summary: {summary}
calendar: {label}
about: {about}
---
# {summary} — {day}

## Why this is on the calendar
{why}

## Going in
- 

## How it went
*Empty until it happens. `brain.py queue` will ask.*

Related: [[Agenda]] · [[Home]]
"""

def _dt(s):
    """'YYYY-MM-DD HH:MM' | 'YYYY-MM-DDTHH:MM' | 'YYYY-MM-DD' -> datetime."""
    if not s:
        return None
    s = str(s).strip().replace('T', ' ')
    try:
        y, mo, dy = [int(x) for x in s[:10].split('-')]
        if len(s) <= 10:
            return datetime.datetime(y, mo, dy)
        hh, mm = [int(x) for x in s[11:16].split(':')]
        return datetime.datetime(y, mo, dy, hh, mm)
    except (ValueError, TypeError):
        return None

def _fmt_dt(s):
    """Calendar 'YYYY-MM-DD HH:MM' -> frontmatter 'YYYY-MM-DDTHH:MM'.

    The space form would still parse here, but it makes YAML ambiguous in
    Obsidian and needs quoting; the T form is a plain scalar everywhere.
    """
    s = (s or '').strip()
    return s.replace(' ', 'T') if len(s) > 10 else s

def load_calendar_events():
    """Every ingested event, tagged with the calendar it came from."""
    out = []
    for f in sorted(glob.glob(os.path.join(INBOX, 'calendar-*.json'))):
        blob = json.load(open(f, encoding='utf-8'))
        for e in blob.get('events', []):
            e = dict(e)
            e['_label'] = blob.get('label', '?')
            out.append(e)
    return out

def _over_at(ev, start):
    """When an event is finished enough to be worth asking about.

    The feed's end time when there is one. Otherwise the end of the day it
    starts on: asking "how did it go" five minutes into a meeting is worse
    than asking the next morning.
    """
    end = _dt((ev or {}).get('end'))
    if end:
        return end
    st = _dt(start)
    if st is None:
        return None
    return datetime.datetime.combine(st.date() + datetime.timedelta(days=1),
                                     datetime.time.min)

def resolve_events(fix=False):
    """Match every Events/ note to the calendar event it was written for.

    The key is the pair (uid, start). A uid on its own names a whole recurring
    series, so it cannot say *which* Friday's Orbitscape meeting a note is
    about. But a uid that occurs exactly once and has moved is a reschedule,
    not a mystery — people move things, and a note still claiming the old time
    is exactly the stale copy this vault exists not to keep. With `fix`, the
    note follows the calendar; the move is reported either way.

    States: upcoming · due (it happened, no debrief yet) · debriefed ·
    cancelled · dangling (nothing on the calendar answers to it any more).
    """
    cal = load_calendar_events()
    by_key, by_uid = {}, {}
    for e in cal:
        by_key[(e.get('uid', ''), _fmt_dt(e.get('start', '')))] = e
        by_uid.setdefault(e.get('uid', ''), []).append(e)
    now = datetime.datetime.now()
    out = []
    for path in sorted(glob.glob(os.path.join(EVTDIR, '*.md'))):
        pairs, body = read_note(path)
        d = fm_dict(pairs)
        if d.get('type') != 'event':
            continue
        uid = d.get('event_uid', '')
        start = _fmt_dt(d.get('event_start', ''))
        ev = by_key.get((uid, start))
        moved = None
        if ev is None and len(by_uid.get(uid, [])) == 1:
            # one occurrence, different time: a reschedule, not an ambiguity
            ev = by_uid[uid][0]
            moved = _fmt_dt(ev.get('start', ''))
            if fix:
                set_field(pairs, 'event_start', moved)
                write_note(path, pairs, body)
                start = moved
        # The Why section is hard-wrapped prose, so read it by paragraph and
        # join the lines back up -- taking the first *line* stopped mid-sentence
        # wherever the author happened to hit the margin.
        paras = []
        m = re.search(r'^## Why this is on the calendar\s*\n(.*?)(?=\n##|\Z)',
                      body, re.S | re.M)
        if m:
            for chunk in re.split(r'\n\s*\n', m.group(1).strip()):
                raw = ' '.join(chunk.split())
                if not raw or (raw.startswith('*') and raw.endswith('*')
                               and not raw.startswith('**')):
                    continue          # the scaffold's italic placeholder
                paras.append(re.sub(r'[*_`]', '', raw).strip())
        why = paras[0] if paras else ''
        rec = {'path': path, 'title': title_of(path), 'fm': d, 'uid': uid,
               'start': start, 'moved_to': moved, 'event': ev, 'why': why,
               'why_all': paras,
               'about': d.get('about', ''),
               'summary': d.get('event_summary') or (ev or {}).get('summary', '')}
        if d.get('status') in ('debriefed', 'cancelled'):
            rec['state'] = d['status']
        elif ev is None:
            rec['state'] = 'dangling'
        else:
            over = _over_at(ev, start)
            rec['state'] = 'due' if over and over <= now else 'upcoming'
        out.append(rec)
    out.sort(key=lambda r: r['start'] or '')
    return out

def event_note_map():
    """(uid, start) -> record, for annotating the agenda."""
    return {(r['uid'], r['start']): r for r in resolve_events()}

def cmd_event(a):
    """Scaffold an event note against an already-ingested calendar event."""
    os.makedirs(EVTDIR, exist_ok=True)
    q = a.match.lower()
    cands = [e for e in load_calendar_events()
             if q in e.get('summary', '').lower()
             and (not a.date or e.get('start', '').startswith(a.date))]
    floor = datetime.datetime.combine(TODAY, datetime.time.min)
    ahead = [e for e in cands if (_dt(e.get('start')) or floor) >= floor]
    pool = sorted(ahead or cands, key=lambda e: e.get('start', ''))
    if not pool:
        print('no ingested event matches "%s"%s'
              % (a.match, ' on ' + a.date if a.date else ''))
        print('run `brain.py calendars` first, or widen the match')
        return 1
    if len(pool) > 1 and not a.date:
        print('%d events match "%s" — pick one with --date:' % (len(pool), a.match))
        for e in pool[:12]:
            print('  %s  %s  (%s)' % (e.get('start', ''), e.get('summary', ''),
                                      e['_label']))
        return 1
    ev = pool[0]
    start = _fmt_dt(ev.get('start', ''))
    summary = ev.get('summary', 'event')
    slug = re.sub(r'\s+', ' ', re.sub(r'[^\w \-]', '', summary)).strip()[:60] or 'event'
    path = os.path.join(EVTDIR, '%s %s.md' % (start[:10], slug))
    if os.path.exists(path):
        print('exists %s' % rel(path))
        return 0
    about = (a.about or '').strip()
    if about and not about.startswith('[['):
        about = '[[%s]]' % about
    why = a.why or ('*Why is this on the calendar? Write it now — the debrief is '
                    'only worth anything if there was something to check against.*')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(EVT_TEMPLATE.format(uid=ev.get('uid', ''), start=start,
                                     summary=summary, label=ev['_label'],
                                     about=about, day=start[:10], why=why))
    print('created %s' % rel(path))
    print('  %s · %s · %s' % (start.replace('T', ' '), summary, ev['_label']))
    return 0

def cmd_events(a):
    """Read-only listing. The reconcile and the flags live in `queue`."""
    recs = resolve_events(fix=False)
    if not recs:
        print('no event notes yet — make one with `brain.py event "<summary>"`')
        return 0
    order = ['due', 'dangling', 'upcoming', 'debriefed', 'cancelled']
    label = {'due': 'Needs a debrief', 'dangling': 'Points at nothing on the calendar',
             'upcoming': 'Upcoming', 'debriefed': 'Debriefed', 'cancelled': 'Cancelled'}
    for st in order:
        group = [r for r in recs if r['state'] == st]
        if not group or (a.pending and st in ('debriefed', 'cancelled', 'upcoming')):
            continue
        print('\n%s' % label[st])
        for r in group:
            moved = '  (moved from the time in the note)' if r['moved_to'] else ''
            print('  %-16s %-42s %s%s' % ((r['start'] or '?').replace('T', ' '),
                                          (r['summary'] or r['title'])[:42],
                                          rel(r['path']), moved))
    counts = {st: len([r for r in recs if r['state'] == st]) for st in order}
    print('\n%d event note(s): %d due, %d upcoming, %d debriefed'
          % (len(recs), counts['due'], counts['upcoming'], counts['debriefed']))
    return 0

# ---------------------------------------------------------------- ICS ingest

def _unfold(text):
    return text.replace('\r\n', '\n').replace('\r', '\n').replace('\n ', '').replace('\n\t', '')

def _ics_dt(params, val):
    """An ICS date-time -> ('YYYY-MM-DD HH:MM' | 'YYYY-MM-DD', all_day).

    Three forms appear in the wild and they are not interchangeable:
      DTSTART;VALUE=DATE:20260909              -> a date, no time, no zone
      DTSTART:20260909T160000Z                 -> UTC, must be converted
      DTSTART;TZID=America/Phoenix:20260909T090000 -> already local to that zone
    The vault dates everything in local time, so anything zoned is converted.
    Slicing the digits instead is how an 09:00 meeting became 16:00.
    """
    val = val.strip()
    m = re.search(r'(\d{8})(?:T(\d{6}))?(Z)?', val)
    if not m:
        return None, False
    d, t, zulu = m.group(1), m.group(2), m.group(3)
    y, mo, dy = int(d[0:4]), int(d[4:6]), int(d[6:8])
    if not t or 'VALUE=DATE' in params.upper():
        return '%04d-%02d-%02d' % (y, mo, dy), True
    dt = datetime.datetime(y, mo, dy, int(t[0:2]), int(t[2:4]), int(t[4:6]))
    tzm = re.search(r'TZID=([^;:]+)', params)
    tz = None
    if zulu:
        tz = datetime.timezone.utc
    elif tzm:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tzm.group(1).strip())
        except Exception:
            tz = None
    if tz is not None:
        dt = dt.replace(tzinfo=tz).astimezone().replace(tzinfo=None)
    return dt.strftime('%Y-%m-%d %H:%M'), False

_WD = {'MO': 0, 'TU': 1, 'WE': 2, 'TH': 3, 'FR': 4, 'SA': 5, 'SU': 6}

def _expand(start, all_day, rrule, exdates, back=180, ahead=400):
    """Expand an RRULE into concrete start strings inside a bounded window.

    Google emits one VEVENT per recurring series, not one per occurrence. Without
    this a weekly class contributes exactly one dated event -- its first -- and the
    agenda silently loses the entire semester.
    """
    fmt = '%Y-%m-%d' if all_day else '%Y-%m-%d %H:%M'
    try:
        base = datetime.datetime.strptime(start, fmt)
    except ValueError:
        return [start]
    if not rrule:
        return [start]
    r = {}
    for part in rrule.split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            r[k.strip().upper()] = v.strip()
    freq = r.get('FREQ', '').upper()
    if freq not in ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'):
        return [start]
    interval = int(r.get('INTERVAL', 1) or 1)
    count = int(r['COUNT']) if r.get('COUNT', '').isdigit() else None
    until = None
    if r.get('UNTIL'):
        u, _ = _ics_dt('', r['UNTIL'])
        if u:   # parse UNTIL by its own shape, not the event's
            until = datetime.datetime.strptime(
                u, '%Y-%m-%d %H:%M' if len(u) > 10 else '%Y-%m-%d')
    lo = datetime.datetime.combine(TODAY - datetime.timedelta(days=back), datetime.time())
    hi = datetime.datetime.combine(TODAY + datetime.timedelta(days=ahead), datetime.time())
    days = [_WD[x[-2:]] for x in r.get('BYDAY', '').split(',') if x[-2:] in _WD]
    out, cur, n = [], base, 0
    step = {'DAILY': datetime.timedelta(days=interval),
            'WEEKLY': datetime.timedelta(weeks=interval)}.get(freq)
    while cur <= hi and n < 2000:
        n += 1
        occs = [cur]
        if freq == 'WEEKLY' and days:
            wk = cur - datetime.timedelta(days=cur.weekday())
            occs = [wk + datetime.timedelta(days=d) for d in days]
        for o in occs:
            if o < base or o > hi:
                continue
            if until and o > until:
                continue
            s = o.strftime(fmt)
            if s in exdates:
                continue
            if o >= lo:
                out.append(s)
            if count and len(out) >= count:
                return out
        if step:
            cur = cur + step
        elif freq == 'MONTHLY':
            y, mth = cur.year, cur.month + interval
            y, mth = y + (mth - 1) // 12, (mth - 1) % 12 + 1
            try:
                cur = cur.replace(year=y, month=mth)
            except ValueError:
                cur = cur.replace(year=y, month=mth, day=28)
        else:
            try:
                cur = cur.replace(year=cur.year + interval)
            except ValueError:
                cur = cur.replace(year=cur.year + interval, day=28)
    return sorted(set(out))

def parse_ics(text):
    events, overrides = [], set()
    blocks = re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', _unfold(text), re.S)
    for block in blocks:
        m = re.search(r'^RECURRENCE-ID([^:\n]*):(.+)$', block, re.M)
        u = re.search(r'^UID[^:\n]*:(.+)$', block, re.M)
        if m and u:
            s, _ad = _ics_dt(m.group(1), m.group(2))
            overrides.add((u.group(1).strip(), s))
    for block in blocks:
        ev, rrule, exdates, recur_id, uid = {}, None, set(), None, None
        for line in block.split('\n'):
            if ':' not in line:
                continue
            name, val = line.split(':', 1)
            key = name.split(';')[0].strip().upper()
            clean = val.strip().replace('\\,', ',').replace('\\n', ' ')
            if key == 'SUMMARY':
                ev['summary'] = clean
            elif key == 'DTSTART':
                ev['start'], ev['all_day'] = _ics_dt(name, val)
            elif key == 'DTEND':
                ev['end'], _ = _ics_dt(name, val)
            elif key == 'LOCATION':
                ev['location'] = clean
            elif key == 'UID':
                uid = clean; ev['uid'] = clean
            elif key == 'URL':
                ev['url'] = clean
            elif key == 'RRULE':
                rrule = val.strip()
            elif key == 'EXDATE':
                for piece in val.split(','):
                    s, _ = _ics_dt(name, piece)
                    if s:
                        exdates.add(s)
            elif key == 'RECURRENCE-ID':
                recur_id, _ = _ics_dt(name, val)
            elif key == 'STATUS' and clean.upper() == 'CANCELLED':
                ev['_cancelled'] = True
        if not ev.get('summary') or not ev.get('start') or ev.get('_cancelled'):
            continue
        if rrule and not recur_id:
            dur = None
            if not ev.get('all_day'):
                s0, e0 = _dt(ev.get('start')), _dt(ev.get('end'))
                if s0 and e0 and e0 > s0:
                    dur = e0 - s0
            for s in _expand(ev['start'], ev.get('all_day', False), rrule, exdates):
                if (uid, s) in overrides:
                    continue          # a moved instance; its own VEVENT carries it
                e = dict(ev); e['start'] = s; e.pop('end', None)
                if dur is not None:
                    st = _dt(s)
                    if st is not None:
                        e['end'] = (st + dur).strftime('%Y-%m-%d %H:%M')
                events.append(e)
        else:
            events.append(ev)
    return events

def _known_codes():
    """Course codes from Classes/ — e.g. {'CSE423': 'CSE 423'}."""
    out = {}
    for q in iter_notes():
        d = fm_dict(read_note(q)[0])
        if d.get('type') == 'class' and d.get('code'):
            out[d['code'].replace(' ', '').upper()] = d['code']
    return out

def _clean_canvas(summary, codes):
    """Canvas appends a course tag: 'HW 01 [2026FallC-T-CSE485-CSE423-60367-...]'.
    Turn that into 'HW 01 · CSE 423', or drop it when it's an ASU org calendar."""
    m = re.search(r'\s*\[([^\]]+)\]\s*$', summary)
    if not m:
        return summary.strip()
    tag, title = m.group(1).upper(), summary[:m.start()].strip()
    hits = [codes[c] for c in codes if c in tag.replace('-', '')]
    if hits:
        return '%s · %s' % (title, '/'.join(sorted(set(hits))))
    return '%s · ASU' % title if tag.startswith('ORG-') else title

def _ics_text(src):
    if src.startswith('http'):
        with urllib.request.urlopen(src.replace('webcal://', 'https://'), timeout=30) as r:
            return r.read().decode('utf-8', 'replace')
    return open(os.path.expanduser(src), encoding='utf-8', errors='replace').read()

def cmd_ingest_ics(a):
    os.makedirs(INBOX, exist_ok=True)
    src = a.source
    text = getattr(a, 'text', None)     # cmd_calendars pre-fetches in parallel
    if text is None:
        try:
            text = _ics_text(src)
        except Exception as e:
            print('fetch failed: %s' % e); return 1
    events = parse_ics(text)
    if a.label == 'canvas':
        codes = _known_codes()
        for e in events:
            e['summary'] = _clean_canvas(e.get('summary', ''), codes)
    total = len(events)
    match = getattr(a, 'match', None)
    if match:
        rx = re.compile(match, re.I)
        events = [e for e in events if rx.search(e.get('summary', ''))]
    out = os.path.join(INBOX, 'calendar-%s.json' % a.label)
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump({'label': a.label, 'fetched': TODAY.isoformat(),
                   'match': match, 'kept': len(events), 'seen': total,
                   'events': events}, fh, indent=1)
    # A shared calendar changes constantly, so remember the URL and refetch on catchup.
    if a.source.startswith('http'):
        cfg = {}
        if os.path.exists(CAL_CFG):
            cfg = json.load(open(CAL_CFG, encoding='utf-8'))
        cfg[a.label] = {'url': a.source, 'match': match}
        with open(CAL_CFG, 'w', encoding='utf-8') as fh:
            json.dump(cfg, fh, indent=1)
        print('  remembered in %s — catchup will refetch it' % rel(CAL_CFG))
    if match:
        print('  filter %r kept %d of %d event(s)' % (match, len(events), total))
    upcoming = [e for e in events if e['start'][:10] >= TODAY.isoformat()]
    print('%s: %d event(s), %d upcoming -> %s' %
          (a.label, len(events), len(upcoming), rel(out)))
    return 0

def cmd_calendars(a):
    """Refetch every calendar remembered by `ingest-ics <url>`. Shared calendars change
    constantly, so a one-time import is stale the day after it's taken."""
    if not os.path.exists(CAL_CFG):
        print('no remembered calendars — run: brain.py ingest-ics <url> --label X')
        return 0
    cfg = json.load(open(CAL_CFG, encoding='utf-8'))
    # The fetches are pure network wait and dominate the refresh, so overlap them.
    # Parsing and writing stay serial: cmd_ingest_ics rewrites calendars.json, and
    # concurrent writers there would race over the file holding the secret URLs.
    import concurrent.futures as _cf
    items = sorted(cfg.items())
    texts = {}
    with _cf.ThreadPoolExecutor(max_workers=min(8, max(1, len(items)))) as ex:
        futs = {ex.submit(_ics_text, c['url']): label for label, c in items}
        for f in _cf.as_completed(futs):
            label = futs[f]
            try:
                texts[label] = f.result()
            except Exception as e:
                print('%s: fetch failed: %s' % (label, e))
    for label, c in items:
        if label not in texts:
            continue
        class A:
            source = c['url']; label = None; match = c.get('match'); text = None
        A.label = label
        A.text = texts[label]
        try:
            cmd_ingest_ics(A())
        except Exception as e:
            print('%s: refetch failed: %s' % (label, e))
    return 0

def _hhmm(m):
    return '%02d:%02d' % (m // 60, m % 60)

def _dur(mins):
    h, m = divmod(int(mins), 60)
    return ('%dh%02dm' % (h, m)) if h and m else ('%dh' % h) if h else ('%dm' % m)

def _snip(text, n):
    """Truncate on a word boundary. A hard slice cuts mid-word and reads as a bug."""
    text = ' '.join((text or '').split())
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(' ', 1)[0]
    return (cut or text[:n]).rstrip(' ,;:—-') + '…'

def _place(loc):
    """A location worth putting in the grid. Canvas puts whole URLs in here."""
    loc = ' '.join((loc or '').split())
    if not loc or 'http' in loc:
        return ''
    return _snip(loc, 34)

def _prep(uid, start, enotes):
    """The 📝 tail for a row that has an event note behind it."""
    r = enotes.get((uid, _fmt_dt(start)))
    if not r:
        return ''
    tail = ' · 📝 [[%s]]' % r['title']
    if r['state'] == 'due':
        tail += ' ← **debrief**'
    return tail

def _day_grid(rows, enotes, slot=30, now_min=None):
    """One day as a time sheet: blocks whose height is their real length.

    A list of start times says when things begin and hides how much of the day
    they eat. The grid draws each event across the rows it actually occupies,
    and gives overlapping events their own lane — so a double-booking shows up
    as two bars on one row instead of two innocent lines in a list.
    """
    timed = [r for r in rows if not r['all_day'] and len(r['start']) > 10]
    allday = [r for r in rows if r not in timed]
    out = []
    for r in allday:
        out.append('- `all day` **%s** · %s%s'
                   % (r['summary'], r['label'], _prep(r['uid'], r['start'], enotes)))
    if allday and timed:
        out.append('')
    if not timed:
        if not allday:
            out.append('*Nothing scheduled.*')
        return out

    blocks = []
    for r in timed:
        st, en = _dt(r['start']), _dt(r.get('end') or '')
        if st is None:
            continue
        s0 = st.hour * 60 + st.minute
        if en is None or en <= st:
            e0 = s0 + slot                      # no DTEND in the feed
        elif en.date() != st.date():
            e0 = 24 * 60                        # runs past midnight; clamp to the day
        else:
            e0 = en.hour * 60 + en.minute
        blocks.append({'s': s0, 'e': max(e0, s0 + 5), 'r': r})
    blocks.sort(key=lambda b: (b['s'], b['e']))

    lanes = []                                   # lane -> minute it frees up
    for b in blocks:
        for i, free in enumerate(lanes):
            if b['s'] >= free:
                lanes[i] = b['e']; b['lane'] = i; break
        else:
            b['lane'] = len(lanes); lanes.append(b['e'])
    width = max(1, len(lanes)) * 2 - 1

    lo = min(b['s'] for b in blocks) // slot * slot
    hi = max(-(-b['e'] // slot) * slot for b in blocks)
    out.append('```text')
    for t in range(lo, hi, slot):
        cells = [' '] * len(lanes)
        labels = []
        for b in blocks:
            if b['s'] < t + slot and b['e'] > t:
                cells[b['lane']] = '█'
            if b['s'] - (b['s'] % slot) == t:
                r = b['r']
                bits = ['%s–%s, %s' % (_hhmm(b['s']), _hhmm(b['e']), _dur(b['e'] - b['s']))]
                if _place(r['loc']):
                    bits.append(_place(r['loc']))
                # a wiki link inside a code block is dead text, so the grid
                # only marks that a note exists; the linked reasons go below it
                er = enotes.get((r['uid'], _fmt_dt(r['start'])))
                tag = ' 📝' if er else ''
                if er and er['state'] == 'due':
                    tag = ' 📝 debrief'
                labels.append('%s%s  (%s)' % (r['summary'], tag,
                                              ' · '.join(b for b in bits if b)))
        mark = ' ←now' if now_min is not None and t <= now_min < t + slot else ''
        bar = ' '.join(cells).ljust(width)
        out.append((' %s │ %s %s%s'
                    % (_hhmm(t), bar, '  '.join(labels), mark)).rstrip())
    out.append('```')

    # the reason each noted event is on the calendar, under the grid
    whys = []
    for b in blocks:
        r = enotes.get((b['r']['uid'], _fmt_dt(b['r']['start'])))
        if r and r.get('why'):
            whys.append('- 📝 [[%s|%s]] — %s'
                        % (r['title'], r['summary'] or r['title'], _snip(r['why'], 150)))
    if whys:
        out += [''] + whys
    return out

def cmd_agenda(a):
    os.makedirs(METADIR, exist_ok=True)
    horizon = (TODAY + datetime.timedelta(days=a.days)).isoformat()
    rows = []
    for f in sorted(glob.glob(os.path.join(INBOX, 'calendar-*.json'))):
        blob = json.load(open(f, encoding='utf-8'))
        for e in blob['events']:
            if TODAY.isoformat() <= e['start'][:10] <= horizon:
                rows.append({'start': e['start'], 'end': e.get('end', ''),
                             'label': blob['label'], 'summary': e['summary'],
                             'loc': e.get('location', ''), 'uid': e.get('uid', ''),
                             'all_day': bool(e.get('all_day')) or len(e['start']) <= 10})
    rows.sort(key=lambda r: (r['start'], r['summary']))
    cstat = canvas_status_map()
    enotes = event_note_map()
    lines = ['---', 'tags: [meta, view]', 'type: view', 'view_kind: generated',
             'status: living', 'source: brain.py agenda',
             'generated: ' + TODAY.isoformat(), '---', '',
             '# Agenda', '',
             '*Generated by `brain.py agenda`. Do not hand-edit — rerun instead.*', '',
             '📝 marks an event with a note in `Events/` — read it before you go.', '',
             'Next %d days, from %d ingested calendar(s).%s' %
             (a.days, len(glob.glob(os.path.join(INBOX, 'calendar-*.json'))),
              '  Canvas items struck through are submitted.' if cstat else
              '  Canvas submission status not loaded - run `brain.py canvas`.'), '']
    if not rows:
        lines.append('Nothing ingested yet. Run `brain.py ingest-ics <url> --label canvas`.')

    # Today, as a time sheet. The heading stays literally "Today" so that
    # ![[Agenda#Today]] keeps resolving from Now and Dashboard tomorrow.
    now = datetime.datetime.now()
    lines += ['## Today', '',
              '**%s**' % TODAY.strftime('%A %-d %B %Y'), '']
    lines += _day_grid([r for r in rows if r['start'][:10] == TODAY.isoformat()],
                       enotes, now_min=now.hour * 60 + now.minute)
    lines += ['', '## Next %d days' % a.days]

    day = None
    for r in rows:
        start, label, summary = r['start'], r['label'], r['summary']
        loc, uid = r['loc'], r['uid']
        d = start[:10]
        if d != day:
            day = d
            dt = datetime.date(*[int(x) for x in d.split('-')])
            lines.append('')
            lines.append('### %s (%s)' % (d, dt.strftime('%a')))
        t = start[11:] or 'all day'
        mark = ''
        if label == 'canvas':
            done = cstat.get((d, _norm_title(summary)))
            mark = {True: '~~', False: ''}.get(done, '')
        body = '%s**%s**%s' % (mark, summary, mark)
        # An event with a note is one he scheduled *for* a reason. Show the
        # link here so the reason is in front of him before he walks in, not
        # only afterwards when the queue asks how it went.
        er = enotes.get((uid, _fmt_dt(start)))
        prep = ''
        if er:
            prep = ' · 📝 [[%s]]' % er['title']
            if er['state'] == 'due':
                prep += ' ← **debrief**'
        lines.append('- `%s` %s · %s%s%s%s' % (t, body, label,
                     ' · ' + loc if loc else '', '  ✓ submitted' if mark else '',
                     prep))
    lines += ['', 'Related: [[Status]] · [[Home]]', '']
    open(os.path.join(METADIR, 'Agenda.md'), 'w', encoding='utf-8').write('\n'.join(lines))
    print('agenda: %d event(s) in the next %d days -> Meta/Agenda.md' % (len(rows), a.days))
    return 0

# ---------------------------------------------------------------- budget ingest

def cmd_ingest_budget(a):
    path = os.path.expanduser(a.csvfile)
    rows = list(csv.DictReader(open(path, encoding='utf-8', errors='replace')))
    if not rows:
        print('empty csv'); return 1
    cols = rows[0].keys()
    def pick(cands):
        for c in cols:
            if c and c.strip().lower() in cands:
                return c
        for c in cols:
            for cand in cands:
                if c and cand in c.strip().lower():
                    return c
        return None
    dcol = pick({'date', 'transaction date', 'posted date'})
    acol = pick({'amount', 'debit', 'value'})
    if not dcol or not acol:
        print('could not find date/amount columns in: %s' % list(cols)); return 1
    months = {}
    for r in rows:
        try:
            amt = float(str(r[acol]).replace('$', '').replace(',', '').strip() or 0)
        except ValueError:
            continue
        raw = str(r[dcol]).strip()
        m = re.search(r'(\d{4})-(\d{2})', raw) or re.search(r'(\d{2})/(\d{2})/(\d{4})', raw)
        if not m:
            continue
        key = '%s-%s' % (m.group(1), m.group(2)) if '-' in raw else '%s-%s' % (m.group(3), m.group(1))
        b = months.setdefault(key, [0.0, 0.0])
        if amt < 0:
            b[0] += -amt
        else:
            b[1] += amt
    for month in sorted(months):
        out, inn = months[month]
        add_metric('spend_month', round(out, 2), month + '-01', '', 'budget-csv')
        add_metric('income_month', round(inn, 2), month + '-01', '', 'budget-csv')
        print('%s  out %.2f  in %.2f' % (month, out, inn))
    print('wrote %d month(s) to Metrics/metrics.csv' % len(months))
    return 0

# ---------------------------------------------------------------- status

def _days_since(iso):
    try:
        d = datetime.date(*[int(x) for x in iso[:10].split('-')])
        return (TODAY - d).days
    except Exception:
        return None

def cmd_status(a):
    os.makedirs(METADIR, exist_ok=True)
    notes = []
    for p in iter_notes():
        pairs, _ = read_note(p)
        notes.append((p, fm_dict(pairs)))

    L = ['---', 'tags: [meta, view]', 'type: view', 'view_kind: generated',
         'status: living', 'source: brain.py status',
         'generated: ' + TODAY.isoformat(), '---', '',
         '# Status', '',
         '*Generated by `brain.py status` on %s. Do not hand-edit — rerun instead.*'
         % TODAY.isoformat(), '']

    # revenue scoreboard
    rev = [(p, d) for p, d in notes if d.get('revenue_intent') == 'true']
    rev.sort(key=lambda x: -float(x[1].get('revenue_to_date', 0) or 0))
    L += ['## The scoreboard', '',
          '| Venture | Stage | $ to date | Customers | Next action |',
          '|---|---|---|---|---|']
    total = 0.0
    for p, d in rev:
        total += float(d.get('revenue_to_date', 0) or 0)
        cust = d.get('paying_users', d.get('customers', '—'))
        L.append('| [[%s]] | %s | %s | %s | %s |' %
                 (title_of(p), d.get('stage', '—'), d.get('revenue_to_date', '—'),
                  cust, d.get('next_action', '**none**')))
    L += ['', '**%d ventures, $%g total.**' % (len(rev), total), '']

    # drifting
    drift = [(p, d) for p, d in notes
             if d.get('status') in ('active', 'ongoing')
             and d.get('type') in ('project', 'area')
             and not d.get('next_action')]
    L += ['## Drifting — active, no next action', '']
    L += ['- [[%s]] (%s)' % (title_of(p), d.get('type', '?')) for p, d in drift] or ['- none']
    L += ['']

    # stale
    stale = []
    for p, d in notes:
        if d.get('status') == 'active' and d.get('note_updated'):
            n = _days_since(d['note_updated'])
            if n is not None and n > 30:
                stale.append((n, p, d))
    stale.sort(reverse=True)
    L += ['', '## Stale — says active, note untouched 30+ days', '']
    L += ['- [[%s]] — %d days (last_touched: %s)' %
          (title_of(p), n, d.get('last_touched', '—')) for n, p, d in stale] or ['- none']
    L += ['']

    # reviews
    # Decisions/ is not in SKIP_DIRS, so notes already covers it. A second
    # explicit pass over DECDIR listed every decision twice.
    due = [(d['review'], p, d) for p, d in notes if d.get('review')]
    due.sort()
    L += ['', '## Reviews', '']
    for when, p, d in due:
        flag = ' ⟵ **due**' if when <= TODAY.isoformat() else ''
        L.append('- `%s` [[%s]]%s' % (when, title_of(p), flag))
    if not due:
        L.append('- none scheduled')
    L += ['']

    # blocked / open loops
    blocked = [(p, d) for p, d in notes if d.get('blocked_by') or d.get('open_loop') == 'true']
    L += ['', '## Blocked and open loops', '']
    L += ['- [[%s]] — %s' % (title_of(p), d.get('blocked_by', 'open loop'))
          for p, d in blocked] or ['- none']
    L += ['']

    # log activity
    logs = sorted(glob.glob(os.path.join(LOGDIR, '*.md')))
    recent = [f for f in logs if _days_since(title_of(f)) is not None
              and _days_since(title_of(f)) <= 14]
    # Count the section, not just the file. LINK_RE stops at '#' because that is
    # what `sync` needs -- a mention of [[Others#Willis]] is evidence the Others
    # note was touched. But for attention it throws away the only thing worth
    # knowing: folding five people into one note turned "Willis x 4" into a share
    # of "Others x 5", and Willis carrying the thesis is exactly the signal this
    # section exists to show.
    mentioned = {}
    for f in recent:
        for link in ANCHOR_RE.findall(open(f, encoding='utf-8').read()):
            mentioned[link.strip()] = mentioned.get(link.strip(), 0) + 1
    L += ['', '## Attention, last 14 days', '',
          '%d log entr%s.' % (len(recent), 'y' if len(recent) == 1 else 'ies'), '']
    for k, v in sorted(mentioned.items(), key=lambda x: -x[1])[:15]:
        L.append('- [[%s]] × %d' % (k, v))
    if not mentioned:
        L.append('*Nothing logged. Every "what have I been working on" query is blind until this fills in.*')

    # untouched actives, cross-referenced with attention
    if recent:
        actives = set(title_of(p) for p, d in notes if d.get('status') == 'active')
        ignored = sorted(actives - set(mentioned))
        L += ['', '### Active but unmentioned in any recent log', '']
        L += ['- [[%s]]' % t for t in ignored] or ['- none']

    L += ['', 'Related: [[Dashboard]] · [[Agenda]] · [[Review]] · [[Home]]', '']
    open(os.path.join(METADIR, 'Status.md'), 'w', encoding='utf-8').write('\n'.join(L))
    print('status -> Meta/Status.md  (%d ventures, %d drifting, %d stale, %d blocked)'
          % (len(rev), len(drift), len(stale), len(blocked)))
    return 0


# ---------------------------------------------------------------- github

import subprocess, hashlib

GH_JSON = os.path.join(INBOX, 'github.json')
CAL_CFG = os.path.join(INBOX, 'calendars.json')
GH_USER = os.environ.get('BRAIN_GH_USER', '')
# Orgs whose repos also count as his work. Team repos live here (Luminosity Lab),
# so without this Orbitscape and Mini Charlotte look dead in the vault when they aren't.
GH_ORGS = [o for o in os.environ.get('BRAIN_GH_ORGS', '').split(',') if o.strip()]

def _utc_local(ts):
    """GitHub returns UTC ('...Z'); the vault dates everything in local time.
    Without this a 20:28 push in Phoenix reads as the next day."""
    if not ts:
        return ''
    try:
        dt = datetime.datetime.strptime(ts[:19], '%Y-%m-%dT%H:%M:%S')
        dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone()
        return dt.strftime('%Y-%m-%dT%H:%M:%S')
    except Exception:
        return ts[:19]

def _split_repo(key):
    """'acme/widget' -> ('acme','widget'); 'widget' -> (GH_USER,'widget')."""
    return tuple(key.split('/', 1)) if '/' in key else (GH_USER, key)

def _token():
    t = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if t:
        return t.strip()
    try:
        r = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None

def _api(path, token, params=''):
    url = 'https://api.github.com' + path + params
    req = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'brain.py'})
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def note_repo_map():
    """{repo_name: (note_path, is_primary)} from explicit `repos:` frontmatter."""
    out = {}
    for p in iter_notes():
        d = fm_dict(read_note(p)[0])
        if not d.get('repos'):
            continue
        for k, name in enumerate(as_list(d['repos'])):
            out[name] = (p, k == 0)
    return out

def cmd_github(a):
    os.makedirs(INBOX, exist_ok=True)
    token = _token()
    print('auth: %s' % ('token found (private repos included)' if token
                        else 'none (public repos only - set GITHUB_TOKEN or run gh auth login)'))
    try:
        repos = _api('/user/repos', token, '?per_page=100&sort=pushed&affiliation=owner') \
                if token else _api('/users/%s/repos' % GH_USER, token,
                                   '?per_page=100&sort=pushed')
    except Exception as e:
        print('github fetch failed: %s' % e)
        return 1
    # Org repos are under NDA. He can see all of them because he's an owner, but the
    # vault must never hold any repo he hasn't explicitly claimed. Only repos already
    # named in a note's `repos:` field survive this filter -- names, descriptions and
    # commits of everything else are dropped before they can be written anywhere.
    _allow = set(note_repo_map())
    for org in GH_ORGS:
        try:
            more = _api('/orgs/%s/repos' % org, token, '?per_page=100&sort=pushed')
            keep = [r for r in more
                    if '%s/%s' % (org, r['name']) in _allow or r['name'] in _allow]
            repos += keep
            print('  + %d of %d repo(s) from org %s (NDA: only mapped repos kept)'
                  % (len(keep), len(more), org))
        except Exception as e:
            print('  ! org %s fetch failed: %s' % (org, e))
    repos = [{'name': r['name'], 'url': r['html_url'],
              'description': r.get('description') or '',
              'language': (r.get('language') or ''),
              'private': r.get('private', False),
              'owner': (r.get('owner') or {}).get('login') or GH_USER,
              'pushed_at': _utc_local(r.get('pushed_at'))} for r in repos]
    for r in repos:
        # his own repos keep bare names (back-compat with existing `repos:` fields);
        # org repos are addressed as org/name so they can never collide with his
        r['key'] = r['name'] if r['owner'] == GH_USER else '%s/%s' % (r['owner'], r['name'])
        r['is_org'] = r['owner'] != GH_USER
    repos.sort(key=lambda r: r['pushed_at'], reverse=True)
    print('%d repo(s) (%d private)' % (len(repos), sum(1 for r in repos if r['private'])))

    prev = {}
    if os.path.exists(GH_JSON):
        prev = dict((r.get('key') or r['name'], r) for r in
                    json.load(open(GH_JSON, encoding='utf-8')).get('repos', []))

    mapping = note_repo_map()
    since = (datetime.datetime.now() -
             datetime.timedelta(days=a.since_days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    owner = GH_USER
    fetched = 0
    for r in repos:
        r['note'] = title_of(mapping[r['key']][0]) if r['key'] in mapping else None
        r['commits'] = []
        if r['key'] not in mapping:
            continue
        cached = prev.get(r['key'], {})
        if (cached.get('pushed_at') == r['pushed_at'] and cached.get('note')
                and not a.full):
            r['commits'] = cached.get('commits', [])
            continue
        try:
            cs = _api('/repos/%s/%s/commits' % (r['owner'], r['name']), token,
                      '?since=%s&per_page=100' % since)
            r['commits'] = [{'sha': c['sha'][:7],
                             'date': _utc_local(c['commit']['author']['date'])[:10],
                             'message': c['commit']['message'].split('\n')[0][:100],
                             'mine': ((c.get('author') or {}).get('login') or '').lower()
                                     == GH_USER.lower()}
                            for c in cs]
            fetched += 1
        except Exception as e:
            r['commit_error'] = str(e)
    with open(GH_JSON, 'w', encoding='utf-8') as fh:
        json.dump({'fetched': datetime.datetime.now().isoformat(timespec='seconds'),
                   'user': owner, 'since': since, 'repos': repos}, fh, indent=1)
    print('commits fetched for %d changed repo(s), window %d days' % (fetched, a.since_days))

    # write activity back onto the notes
    by_note = {}
    for r in repos:
        if r['note']:
            by_note.setdefault(r['note'], []).append(r)
    idx = dict((title_of(p), p) for p in iter_notes())
    bumped = 0
    for note, rs in by_note.items():
        p = idx[note]
        pairs, body = read_note(p)
        d = fm_dict(pairs)
        newest = max(r['pushed_at'] for r in rs)
        ncommits = sum(len(r['commits']) for r in rs)
        mine = [c for r in rs for c in r['commits'] if c.get('mine')]
        has_org = any(r.get('is_org') for r in rs)
        changed = False
        if newest[:10] != d.get('last_push'):
            set_field(pairs, 'last_push', newest[:10]); changed = True
        if str(ncommits) != d.get('commits_recent'):
            set_field(pairs, 'commits_recent', str(ncommits)); changed = True
        if has_org:
            # On a team repo a push is the team's, not necessarily his. `last_touched`
            # feeds the drift flags, which are about *his* attention — so only his own
            # commits may move it. `commits_mine` keeps the distinction visible.
            if str(len(mine)) != d.get('commits_mine'):
                set_field(pairs, 'commits_mine', str(len(mine))); changed = True
            when = max((c['date'] for c in mine), default='')
            if when and when[:7] > (d.get('last_touched') or ''):
                set_field(pairs, 'last_touched', when[:7]); changed = True
        elif newest[:7] > (d.get('last_touched') or ''):
            set_field(pairs, 'last_touched', newest[:7]); changed = True
        if changed:
            write_note(p, pairs, body); bumped += 1
            print('  %-30s last_push %s  %d commit(s) in window%s'
                  % (note, newest[:10], ncommits,
                     '  (%d his)' % len(mine) if has_org else ''))
    print('updated %d note(s)' % bumped)

    # generated index
    L = ['---', 'tags: [meta, view]', 'type: view', 'view_kind: generated',
         'status: living', 'source: brain.py github',
         'generated: ' + TODAY.isoformat(), '---', '', '# Repos', '',
         '*Generated by `brain.py github`. The source of truth is the `repos:` field on each',
         'note — edit that, not this file.*', '',
         '%d repos, %d mapped to a note, %d unassigned.' %
         (len(repos), sum(1 for r in repos if r['note']),
          sum(1 for r in repos if not r['note'])), '',
         '## Mapped', '', '| Repo | Note | Language | Last push | Commits |', '|---|---|---|---|---|']
    for r in repos:
        if r['note']:
            nmine = sum(1 for c in r['commits'] if c.get('mine'))
            L.append('| [%s](%s)%s | [[%s]] | %s | %s | %s |' %
                     (r['key'], r['url'], ' 🔒' if r['private'] else '', r['note'],
                      r['language'] or '—', r['pushed_at'][:10],
                      ('%d (%d his)' % (len(r['commits']), nmine)) if r.get('is_org')
                      else str(len(r['commits']))))
    L += ['', '## Unassigned', '',
          'Add the repo name to a note\'s `repos:` field to map it.', '',
          '| Repo | Language | Last push | Description |', '|---|---|---|---|']
    for r in repos:
        if not r['note']:
            L.append('| [%s](%s)%s | %s | %s | %s |' %
                     (r['key'], r['url'], ' 🔒' if r['private'] else '',
                      r['language'] or '—', r['pushed_at'][:10], r['description'][:60]))
    L += ['', 'Related: [[Queue]] · [[Status]] · [[Workflow]] · [[Home]]', '']
    open(os.path.join(METADIR, 'Repos.md'), 'w', encoding='utf-8').write('\n'.join(L))
    print('-> Meta/Repos.md')
    return 0

def cmd_inspect(a):
    """Dump README + commit history for repos so Claude can write notes for them."""
    if not os.path.exists(GH_JSON):
        print('run `brain.py github` first'); return 1
    blob = json.load(open(GH_JSON, encoding='utf-8'))
    repos = blob['repos']
    owner = blob.get('user', GH_USER)
    token = _token()
    if a.repos:
        want = [r for r in repos if r['name'] in a.repos]
        missing = set(a.repos) - set(r['name'] for r in want)
        if missing:
            print('not found: %s' % ', '.join(sorted(missing)))
    else:
        want = [r for r in repos if not r.get('note')]
        want.sort(key=lambda r: r['pushed_at'], reverse=True)
        want = want[:a.limit]
    # Never dump org repo contents. They are under NDA -- he can read them because he
    # owns the org, the vault may not hold them. Mapped-ness is not consent.
    blocked = [r['key'] for r in want if r.get('is_org')]
    if blocked:
        print('refusing %d org repo(s) (NDA, contents stay out of the vault): %s'
              % (len(blocked), ', '.join(sorted(blocked))))
        want = [r for r in want if not r.get('is_org')]
    print('inspecting %d repo(s)%s' % (len(want), '' if a.repos else
          ' (most recently pushed unmapped; --limit to change)'))
    out = []
    for r in want:
        rec = {'name': r['name'], 'private': r['private'], 'url': r['url'],
               'language': r['language'], 'description': r['description'],
               'pushed_at': r['pushed_at'], 'readme': None, 'commits': []}
        try:
            req = urllib.request.Request(
                'https://api.github.com/repos/%s/%s/readme' % (owner, r['name']),
                headers={'Accept': 'application/vnd.github.raw', 'User-Agent': 'brain.py'})
            if token:
                req.add_header('Authorization', 'Bearer ' + token)
            with urllib.request.urlopen(req, timeout=30) as fh:
                rec['readme'] = fh.read().decode('utf-8', 'replace')[:a.readme_chars]
        except Exception as e:
            rec['readme_error'] = str(e)
        try:
            cs = _api('/repos/%s/%s/commits' % (owner, r['name']), token, '?per_page=40')
            rec['commits'] = [{'date': c['commit']['author']['date'][:10],
                               'message': c['commit']['message'].split(chr(10))[0][:110]}
                              for c in cs]
        except Exception as e:
            rec['commits_error'] = str(e)
        out.append(rec)
        print('  %-32s readme:%-5s commits:%d' %
              (r['name'], 'yes' if rec['readme'] else 'no', len(rec['commits'])))
    dest = os.path.join(INBOX, 'repo-details.json')
    with open(dest, 'w', encoding='utf-8') as fh:
        json.dump({'fetched': datetime.datetime.now().isoformat(timespec='seconds'),
                   'repos': out}, fh, indent=1)
    print('-> %s' % rel(dest))
    print('Now message Claude: "read Inbox/repo-details.json and write notes for these."')
    return 0

# ---------------------------------------------------------------- queue

STATE = os.path.join(INBOX, 'state.json')
TASK_RE = re.compile(r'^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$')

def _all_md():
    out = []
    for dp, dn, fn in os.walk(VAULT):
        dn[:] = [d for d in dn if d not in ('.obsidian', '.git', 'Inbox')]
        for f in sorted(fn):
            if not f.endswith('.md') or f in SKIP_FILES:
                continue
            path = os.path.join(dp, f)
            try:
                head = open(path, encoding='utf-8').read(400)
            except Exception:
                continue
            if re.search(r'^view_kind:\s*generated\s*$', head, re.M):
                continue      # a cache, not a source - never counts as a change
            out.append(path)
    return out

def snapshot():
    snap = {'taken': datetime.datetime.now().isoformat(timespec='seconds'),
            'notes': {}, 'tasks': {}, 'metric_rows': 0, 'repos': {}}
    for p in _all_md():
        raw = open(p, encoding='utf-8').read()
        snap['notes'][rel(p)] = hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]
        for line in raw.split('\n'):
            m = TASK_RE.match(line)
            if m:
                text = re.sub(r'\[[a-z_]+::\s*(?:\[\[[^\]]+\]\]|[^\]]*)\]', '',
                              m.group(2)).strip()
                text = re.sub(r'#\w+', '', text).strip()
                if text:
                    snap['tasks'][text[:120]] = (m.group(1).lower() == 'x')
    if os.path.exists(METCSV):
        snap['metric_rows'] = sum(1 for _ in open(METCSV, encoding='utf-8')) - 1
    if os.path.exists(GH_JSON):
        for r in json.load(open(GH_JSON, encoding='utf-8'))['repos']:
            snap['repos'][r['name']] = (r.get('pushedAt') or '')[:19]
    return snap

def cmd_queue(a):
    os.makedirs(INBOX, exist_ok=True)
    # Reconcile event notes against the calendar *before* the snapshot. A
    # rescheduled meeting rewrites its note, and doing that afterwards would
    # report the note as changed on the following run instead of this one.
    evrecs = resolve_events(fix=True)
    new = snapshot()
    led = _ledger_update(new['tasks'])
    old = json.load(open(STATE, encoding='utf-8')) if os.path.exists(STATE) else None

    if old is None:
        q = {'first_run': True, 'since': None, 'until': new['taken']}
        print('queue: first run - baseline recorded, no diff to show')
    else:
        chg = [k for k, v in new['notes'].items()
               if old['notes'].get(k) != v and k in old['notes']]
        added = [k for k in new['notes'] if k not in old['notes']]
        removed = [k for k in old['notes'] if k not in new['notes']]
        done = [t for t, c in new['tasks'].items() if c and not old['tasks'].get(t, False)]
        newt = [t for t in new['tasks'] if t not in old['tasks'] and not new['tasks'][t]]
        pushed = [(n, ts) for n, ts in new['repos'].items() if old['repos'].get(n, '') != ts]
        q = {'first_run': False, 'since': old['taken'], 'until': new['taken'],
             'notes_changed': sorted(chg), 'notes_added': sorted(added),
             'notes_removed': sorted(removed), 'tasks_completed': done,
             'tasks_added': newt, 'repos_pushed': sorted(pushed, key=lambda x: -1 * len(x[1])),
             'metric_rows_added': new['metric_rows'] - old.get('metric_rows', 0)}

    # derived flags - the part worth a human reading
    flags = []
    notes = [(p, fm_dict(read_note(p)[0])) for p in iter_notes()]
    logged_recently = set()
    for f in glob.glob(os.path.join(LOGDIR, '*.md')):
        n = _days_since(title_of(f))
        if n is not None and n <= 7:
            logged_recently.update(x.strip() for x in LINK_RE.findall(
                open(f, encoding='utf-8').read()))
    for p, d in notes:
        t = title_of(p)
        if d.get('last_push') and _days_since(d['last_push']) is not None \
           and _days_since(d['last_push']) <= 7 and t not in logged_recently:
            flags.append('%s was pushed to on %s but never appears in a log entry' %
                         (t, d['last_push']))
        if d.get('type') == 'project' and d.get('status') == 'active':
            lt = d.get('last_touched')
            if not lt:
                flags.append('%s is active with no last_touched at all' % t)
            elif lt < (TODAY - datetime.timedelta(days=45)).isoformat()[:7]:
                # status_verified is the owner confirming the status despite the
                # evidence, so it suppresses this too -- but only for 45 days,
                # or a single confirmation would hide the drift permanently.
                sv = d.get('status_verified', '')
                if sv and _days_since(sv) is not None and _days_since(sv) <= 45:
                    pass
                else:
                    flags.append('%s is active but untouched since %s%s'
                                 % (t, lt, ' (last verified %s)' % sv if sv else ''))
        if d.get('repos') and d.get('last_push') and d.get('status') in (
                'dormant', 'idea', 'archived', 'retired'):
            n = _days_since(d['last_push'])
            verified = d.get('status_verified', '')
            if n is not None and n <= 120 and verified < d['last_push']:
                flags.append('%s is marked %s but was pushed to on %s — status may be stale'
                             % (t, d['status'], d['last_push']))
        if d.get('review') and d['review'] <= TODAY.isoformat():
            flags.append('%s is due for review (%s)' % (t, d['review']))
    # Event notes. A meeting he scheduled for a reason is not finished when it
    # ends -- it is finished when what came out of it is written down. This is
    # the only flag that expects an answer from him rather than an edit.
    for r in evrecs:
        when = (r['start'] or '?').replace('T', ' ')
        if r['state'] == 'due':
            flags.append('event needs a debrief: "%s" (%s) — ask how it went, then '
                         'write it into %s' % (r['summary'] or r['title'], when,
                                               rel(r['path'])))
        elif r['state'] == 'dangling':
            flags.append('event note matches nothing on the calendar: %s (%s) — '
                         'deleted, or outside the ingest window' % (rel(r['path']), when))
        elif r['moved_to']:
            flags.append('event moved: "%s" is now %s — its note followed it'
                         % (r['summary'] or r['title'], r['moved_to'].replace('T', ' ')))
    # Only this run's drops. A 30-day window re-reported the same drop every
    # run for a month, which made a single edit look like sustained abandonment.
    # The ledger needs its own high-water mark: state.json's `taken` is stamped
    # before _ledger_update runs, so a drop always looks newer than it and would
    # re-report on every run forever.
    seen = led.get('__meta__', {}).get('reported_at', '')
    recent_drop = sorted(t for t, e in led.items()
                         if t != '__meta__' and e.get('dropped_on', '') > seen)
    renamed = sorted(t for t, e in led.items()
                     if t != '__meta__' and e.get('renamed_on', '') > seen)
    led.setdefault('__meta__', {})['reported_at'] = datetime.datetime.now().isoformat()
    with open(LEDGER, 'w', encoding='utf-8') as fh:
        json.dump(led, fh, indent=1, sort_keys=True)
    q['tasks_dropped'] = recent_drop
    q['tasks_renamed'] = renamed
    for t in recent_drop:
        flags.append('task left the list unfinished: "%s"' % t[:70])
    q['flags'] = flags

    with open(os.path.join(INBOX, 'queue.json'), 'w', encoding='utf-8') as fh:
        json.dump(q, fh, indent=1)
    with open(STATE, 'w', encoding='utf-8') as fh:
        json.dump(new, fh, indent=1)

    # human-readable
    L = ['---', 'tags: [meta, view]', 'type: view', 'view_kind: generated',
         'status: living', 'source: brain.py queue',
         'generated: ' + TODAY.isoformat(), '---', '', '# Queue', '',
         '*Generated by `brain.py queue`. Everything that changed since the last run.*',
         '', '**Window:** %s → %s' % (q.get('since') or 'first run', q['until']), '']
    def sec(title, items, fmt=lambda x: '- %s' % x):
        L.append('## %s' % title)
        L.append('')
        L.extend([fmt(i) for i in items] or ['- nothing'])
        L.append('')
    if not q.get('first_run'):
        sec('Repos pushed', q['repos_pushed'], lambda x: '- `%s` — %s' % (x[0], x[1][:10]))
        sec('Tasks completed', q['tasks_completed'])
        sec('Tasks added', q['tasks_added'])
        sec('Tasks dropped unfinished', q.get('tasks_dropped', []))
        sec('Tasks reworded (not dropped)', q.get('tasks_renamed', []))
        sec('Events to debrief', [
            '**[[%s]]** — %s' % (r['title'], (r['start'] or '?').replace('T', ' '))
            for r in evrecs if r['state'] == 'due'])
        sec('Notes changed', q['notes_changed'], lambda x: '- `%s`' % x)
        sec('Notes added', q['notes_added'], lambda x: '- `%s`' % x)
        L += ['## Metrics', '', '- %d new row(s)' % q['metric_rows_added'], '']
    sec('Flags', flags)
    L += ['---', '',
          'Hand this to Claude with: *"read Meta/Queue.md and Inbox/queue.json, then update '
          'the vault."*', '', 'Related: [[Status]] · [[Now]] · [[Review]] · [[Home]]', '']
    open(os.path.join(METADIR, 'Queue.md'), 'w', encoding='utf-8').write('\n'.join(L))

    if not q.get('first_run'):
        print('queue: %d note(s) changed, %d task(s) done, %d repo push(es), %d flag(s)'
              % (len(q['notes_changed']), len(q['tasks_completed']),
                 len(q['repos_pushed']), len(flags)))
    else:
        print('queue: %d flag(s)' % len(flags))
    print('  -> Meta/Queue.md  +  Inbox/queue.json')
    return 0

def cmd_catchup(a):
    print('== github =='); 
    class A: since_days = 30; full = False
    try:
        cmd_github(A())
    except Exception as e:
        print('github step skipped: %s' % e)
    print('\n== calendars ==')
    try:
        cmd_calendars(a)
    except Exception as e:
        print('calendar step skipped: %s' % e)
    print('\n== sync ==');   cmd_sync(a)
    print('\n== agenda =='); a.days = 14; cmd_agenda(a)
    print('\n== status =='); cmd_status(a)
    print('\n== views ==');  cmd_views(a)
    print('\n== queue ==');  cmd_queue(a)
    print('\nDone. Message Claude: "catch me up" — it reads Meta/Queue.md.')
    return 0


# ---------------------------------------------------------------- main

# ---------------------------------------------------------------- canvas

CANVAS_JSON = os.path.join(INBOX, 'canvas.json')
CANVAS_HOST = os.environ.get('CANVAS_HOST', 'https://canvas.instructure.com')

def _canvas_courses():
    """{canvas_course_id: 'CSE 423'} from Classes/ frontmatter.

    Mapped by explicit id, never by matching course titles -- Canvas calls
    CSE 423 "Capstone Project I", which no name-matching would ever resolve.
    """
    out = {}
    for p in iter_notes():
        d = fm_dict(read_note(p)[0])
        if d.get('type') == 'class' and d.get('canvas_course_id') and d.get('code'):
            out[str(d['canvas_course_id']).strip()] = d['code'].strip()
    return out

def _canvas_fetch(days):
    """Pull planner items straight from the API. Needs CANVAS_TOKEN.

    Until ASU grants a token this raises, and --file is the way in. The parsing
    below is identical either way, so the token is a swap of transport only.
    """
    tok = os.environ.get('CANVAS_TOKEN')
    if not tok:
        raise RuntimeError('no CANVAS_TOKEN set')
    url = ('%s/api/v1/planner/items?start_date=%s&end_date=%s&per_page=100'
           % (CANVAS_HOST, (TODAY - datetime.timedelta(days=7)).isoformat(),
              (TODAY + datetime.timedelta(days=days)).isoformat()))
    out = []
    while url and len(out) < 1000:
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + tok})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode('utf-8').lstrip()
            link = r.headers.get('Link', '')
        out.extend(json.loads(body[9:] if body.startswith('while(1);') else body))
        # per_page caps at 100; without following rel="next" a busy month is
        # silently truncated and the dashboard under-reports what is due.
        m = re.search(r'<([^>]+)>\s*;\s*rel="next"', link)
        url = m.group(1) if m else None
    return out

def _canvas_parse(items):
    codes = _canvas_courses()
    out, skipped = [], 0
    for it in items:
        sub = it.get('submissions')
        if not isinstance(sub, dict):
            skipped += 1          # announcements, calendar events: nothing to submit
            continue
        pl = it.get('plannable') or {}
        due = pl.get('due_at') or it.get('plannable_date')
        code = codes.get(str(it.get('course_id')))
        if not code:
            skipped += 1          # a course the vault does not track
            continue
        out.append({
            'course': code,
            'title': (pl.get('title') or '').strip(),
            'due': _utc_local(due) if due else '',
            'type': it.get('plannable_type', ''),
            'points': pl.get('points_possible'),
            'submitted': bool(sub.get('submitted')),
            'graded': bool(sub.get('graded')),
            'late': bool(sub.get('late')),
            'missing': bool(sub.get('missing')),
            'url': (CANVAS_HOST + it['html_url']) if it.get('html_url', '').startswith('/') else it.get('html_url', ''),
        })
    out.sort(key=lambda x: (x['due'] or '9999', x['course'], x['title']))
    return out, skipped

def cmd_canvas(a):
    if a.file:
        raw = open(os.path.expanduser(a.file), encoding='utf-8').read().lstrip()
        if raw.startswith('while(1);'):
            raw = raw[len('while(1);'):]          # Canvas' anti-JSON-hijack prefix
        items = json.loads(raw)
        src = 'file:' + os.path.basename(a.file)
    else:
        try:
            items = _canvas_fetch(a.days)
            src = 'api'
        except Exception as e:
            print('canvas: %s' % e)
            print('  no token yet -- dump the planner JSON from a logged-in browser and pass --file')
            print('  %s/api/v1/planner/items?start_date=%s&end_date=%s&per_page=100'
                  % (CANVAS_HOST, TODAY.isoformat(),
                     (TODAY + datetime.timedelta(days=a.days)).isoformat()))
            return 1
    rows, skipped = _canvas_parse(items)
    with open(CANVAS_JSON, 'w', encoding='utf-8') as fh:
        json.dump({'fetched': datetime.datetime.now().isoformat(timespec='seconds'),
                   'source': src, 'items': rows}, fh, indent=1)
    done = sum(1 for r in rows if r['submitted'])
    late = [r for r in rows if r['missing'] or (r['late'] and not r['submitted'])]
    print('canvas: %d graded item(s), %d submitted, %d outstanding  (%d skipped) -> %s'
          % (len(rows), done, len(rows) - done, skipped, rel(CANVAS_JSON)))
    if late:
        print('  MISSING: ' + '; '.join('%s %s' % (r['course'], r['title'][:40]) for r in late))
    by_day = {}
    for r in rows:
        if not r['submitted'] and r['due']:
            by_day.setdefault(r['due'][:10], []).append(r)
    for d in sorted(by_day)[:6]:
        print('  %s  %d outstanding: %s' % (d, len(by_day[d]),
              ', '.join(sorted(set(x['course'] for x in by_day[d])))))
    return 0

def canvas_status_map():
    """{(YYYY-MM-DD, normalized title): submitted} for the agenda to annotate with."""
    if not os.path.exists(CANVAS_JSON):
        return {}
    blob = json.load(open(CANVAS_JSON, encoding='utf-8'))
    out = {}
    for r in blob.get('items', []):
        if not r['due']:
            continue
        out[(r['due'][:10], _norm_title(r['title']))] = r['submitted']
        # Canvas dates a 23:59 deadline on the day it falls; the ICS feed often
        # files the same item on the following date. Accept either.
        nxt = (datetime.date(*map(int, r['due'][:10].split('-')))
               + datetime.timedelta(days=1)).isoformat()
        out.setdefault((nxt, _norm_title(r['title'])), r['submitted'])
    return out

def _norm_title(t):
    """Reduce an ICS summary and a planner title to the same key.

    The ICS feed decorates titles the API does not: a ' · CSE 423' course tag
    added by _clean_canvas, and Canvas' own '(60362)' section numbers. Both have
    to go or nothing matches.
    """
    t = re.sub(r'\s*·.*$', '', t or '')       # ' · CSE 423'
    t = re.sub(r'\(\s*\d{3,}\s*\)', '', t)    # '(60362)' section numbers
    return re.sub(r'[^a-z0-9]+', '', t.lower())

# ---------------------------------------------------------------- score

LEDGER = os.path.join(INBOX, 'task-ledger.json')

def _ledger_update(tasks):
    """Track first_seen / done_on / dropped_on / renamed_on per task.

    Task text carries no id, so the ledger keys on the text itself and an edit
    looks exactly like a deletion plus an unrelated addition. Distinguishing them
    without fuzzy-matching text uses a structural signal instead: if the total
    number of tasks did not fall, nothing was actually abandoned, so the vanished
    key is marked `renamed_on` and kept out of the drop count. Only a key that
    disappears while the total shrinks is a real `dropped_on`.
    """
    led = json.load(open(LEDGER, encoding='utf-8')) if os.path.exists(LEDGER) else {}
    today = TODAY.isoformat()
    # dropped/renamed carry a full timestamp so cmd_queue can report only what
    # moved since the previous run; a bare date re-reported all day long.
    now = datetime.datetime.now().isoformat()      # microseconds: a queue run
    meta = led.setdefault('__meta__', {})           # finishes inside one second
    prev_count = meta.get('count')
    for t, done in tasks.items():
        fresh = t not in led
        e = led.setdefault(t, {'first_seen': today})
        if fresh and done:
            # already ticked the first time the ledger ever saw it: its age is
            # unknowable, so it must not count as a close in either direction
            e['preexisting'] = True
        if done and not e.get('done_on'):
            e['done_on'] = today
            e.pop('dropped_on', None)
        if not done and e.get('done_on'):
            e.pop('done_on', None)          # unticked again; it is open work
        e.pop('dropped_on', None)   # back on the list; no longer dropped
        e.pop('renamed_on', None)
    vanished = [t for t, e in led.items()
                if t != '__meta__' and t not in tasks
                and not e.get('done_on') and not e.get('dropped_on')
                and not e.get('renamed_on')]
    shrank = prev_count is not None and len(tasks) < prev_count
    for t in vanished:
        led[t]['renamed_on' if not shrank else 'dropped_on'] = now
    meta['count'] = len(tasks)
    with open(LEDGER, 'w', encoding='utf-8') as fh:
        json.dump(led, fh, indent=1, sort_keys=True)
    return led

def _months_ago(ym):
    """days since a YYYY-MM (or YYYY) field, counted from the start of it."""
    if not ym:
        return None
    parts = str(ym).strip()[:7].split('-')
    try:
        y = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 1
        return (TODAY - datetime.date(y, m, 1)).days
    except Exception:
        return None

def _median(xs):
    xs = sorted(xs)
    if not xs:
        return 0
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0

def compute_score(window=30):
    led = json.load(open(LEDGER, encoding='utf-8')) if os.path.exists(LEDGER) else {}
    cut = (TODAY - datetime.timedelta(days=window)).isoformat()
    out, detail = {}, {}

    # --- the ungameable denominator: active projects, which shrink only by an
    # --- explicit, dated status change
    stalls = []
    for p in iter_notes():
        d = fm_dict(read_note(p)[0])
        if d.get('type') != 'project' or d.get('status') != 'active':
            continue
        n = _months_ago(d.get('last_touched'))
        stalls.append((title_of(p), n if n is not None else 9999))
    out['projects_active'] = len(stalls)
    out['projects_drifting'] = sum(1 for _, n in stalls if n >= 30)
    out['stall_days_max'] = max([n for _, n in stalls] or [0])
    out['stall_days_median'] = int(_median([n for _, n in stalls]))
    detail['oldest'] = sorted(stalls, key=lambda x: -x[1])[:3]

    # --- anti-inflation: closes weighted by how long the thing sat
    hard = quick = 0
    for t, e in led.items():
        if t == '__meta__' or e.get('preexisting') or not e.get('done_on') or e['done_on'] < cut:
            continue
        age = (datetime.date(*map(int, e['done_on'].split('-'))) -
               datetime.date(*map(int, e['first_seen'].split('-')))).days
        if age >= 14:
            hard += 1
        elif age <= 1:
            quick += 1
    out['hard_closes'] = hard
    out['quick_closes'] = quick

    # --- anti-omission: work that left the list without being done
    out['tasks_dropped'] = sum(1 for t, e in led.items()
                               if t != '__meta__' and e.get('dropped_on', '') >= cut)

    # --- open commitments, and the ones already past a date he set himself
    open_tasks = overdue = 0
    for p in glob.glob(os.path.join(VAULT, 'Tasks', '*.md')):
        for line in open(p, encoding='utf-8'):
            m = TASK_RE.match(line)
            if not m or m.group(1).lower() == 'x':
                continue
            open_tasks += 1
            due = re.search(r'\[due::\s*(\d{4}-\d{2}-\d{2})\]', m.group(2))
            if due and due.group(1) < TODAY.isoformat():
                overdue += 1
    out['tasks_open'] = open_tasks
    out['tasks_overdue'] = overdue
    return out, detail

def cmd_score(a):
    out, detail = compute_score(a.window)
    _ensure_metrics()
    rows = list(csv.DictReader(open(METCSV, encoding='utf-8')))
    last = {}
    for r in rows:
        if r['key'] in out and r['date'] > last.get(r['key'], ''):
            last[r['key']] = r['date']
    gap = (TODAY - datetime.timedelta(days=7)).isoformat()
    wrote = 0
    for k in sorted(out):
        if not a.force and last.get(k, '') > gap:
            continue
        add_metric(k, out[k], None, '', 'score')
        wrote += 1
    for k in sorted(out):
        print('  %-20s %s' % (k, out[k]))
    if detail.get('oldest'):
        print('\n  oldest untouched active project(s):')
        for t, n in detail['oldest']:
            print('    %-28s %s' % (t, '%d days' % n if n < 9999 else 'never touched'))
    print('\n%s' % ('wrote %d row(s) to Metrics/metrics.csv' % wrote if wrote
                    else 'no rows written - scored within the last 7 days (--force to override)'))
    return 0

# ---------------------------------------------------------------- views
#
# What Dataview used to do, done once and written to disk.
#
# Dataview answered these questions live inside Obsidian, which meant they were
# invisible to the dashboard, to the terminal, and to anything running on a
# server with no Obsidian. The queries were also the only place several of these
# definitions existed -- "drifting" meant one thing in Dashboard.md and something
# subtly different in Now.md.
#
# So: one pass over the vault, one JSON file, one definition each. Every view is
# the same shape -- title, columns, rows -- so a renderer never has to know which
# view it is holding. A cell is a scalar, or {'link': 'Note'} when it should be
# clickable.

VIEWS_JSON = os.path.join(INBOX, 'views.json')

def _link(title):
    return {'link': title}

def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _months_since(ym):
    """`last_touched` is YYYY-MM. Compare at day resolution by taking the 1st."""
    if not ym:
        return None
    return _days_since(str(ym)[:7] + '-01')

def note_index():
    """Every note's frontmatter, read once. Folder is how the old FROM clauses
    scoped themselves, so it is carried alongside."""
    out = []
    for p in iter_notes():
        pairs, _ = read_note(p)
        d = fm_dict(pairs)
        d['_title'] = title_of(p)
        d['_folder'] = os.path.relpath(os.path.dirname(p), VAULT).split(os.sep)[0]
        d['_path'] = rel(p)
        out.append(d)
    return out

def vault_tasks():
    """Every checkbox under Tasks/, with its inline fields pulled out. Same
    normalisation as the queue, so a task keys identically everywhere."""
    out = []
    for p in sorted(glob.glob(os.path.join(VAULT, 'Tasks', '*.md'))):
        for line in open(p, encoding='utf-8'):
            m = TASK_RE.match(line)
            if not m:
                continue
            raw = m.group(2)
            due = re.search(r'\[due::\s*(\d{4}-\d{2}-\d{2})\]', raw)
            proj = re.search(r'\[project::\s*\[\[([^\]]+)\]\]', raw)
            text = re.sub(r'\[[a-z_]+::\s*(?:\[\[[^\]]+\]\]|[^\]]*)\]', '', raw)
            text = re.sub(r'#\w+', '', text).strip()
            out.append({'text': text,
                        'done': m.group(1).lower() == 'x',
                        'due': due.group(1) if due else '',
                        'project': proj.group(1) if proj else '',
                        'next': '#next' in raw,
                        'file': title_of(p)})
    return out

APP_RE = re.compile(r'^\s*-\s+(.+?)\s*(?=\[[a-z_]+::)')

def applications():
    """The internship list is prose with inline fields, not frontmatter, so it
    needs its own tiny parser. Anything without a [status::] is commentary."""
    p = os.path.join(VAULT, 'Areas', 'Internship Applications.md')
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding='utf-8'):
        if not re.match(r'^\s*-\s', line) or 'status::' not in line:
            continue
        name = APP_RE.match(line)
        if not name:
            continue
        f = dict(re.findall(r'\[([a-z_]+)::\s*([^\]]*)\]', line))
        out.append({'company': name.group(1).strip(),
                    'status': f.get('status', ''), 'tier': f.get('tier', ''),
                    'opens': f.get('opens', ''), 'applied': f.get('applied', '')})
    return out

def _view(title, columns, rows, note='', empty='Nothing.'):
    return {'title': title, 'columns': columns, 'rows': rows,
            'note': note, 'empty': empty, 'count': len(rows)}

def build_views():
    notes = note_index()
    tasks = vault_tasks()
    projects = [d for d in notes if d.get('type') == 'project']
    areas = [d for d in notes if d.get('type') == 'area']
    open_tasks = [t for t in tasks if not t['done']]
    horizon = (TODAY + datetime.timedelta(days=30)).isoformat()
    V = {}

    # -- now -----------------------------------------------------------------
    V['next_flagged'] = _view(
        'Flagged next', ['Task', 'Project', 'Due'],
        [[t['text'], _link(t['project']) if t['project'] else '—', t['due'] or '—']
         for t in open_tasks if t['next']],
        note='Keep this under five. If it is longer, you have not chosen.',
        empty='Nothing flagged.')

    by_proj = {}
    for t in open_tasks:
        by_proj.setdefault(t['project'] or '(unassigned)', []).append(t)
    V['tasks_by_project'] = _view(
        'Every open task, by project', ['Project', 'Open', 'Next up'],
        [[_link(k) if k != '(unassigned)' else k, len(v), v[0]['text']]
         for k, v in sorted(by_proj.items(), key=lambda kv: -len(kv[1]))],
        empty='No open tasks.')

    active = [d for d in projects if d.get('status') == 'active']
    V['next_actions'] = _view(
        'The next action on every active project',
        ['Project', 'Stage', 'Next action'],
        [[_link(d['_title']), d.get('stage', '—'), d.get('next_action', '')]
         for d in sorted(active, key=lambda d: d.get('stage', ''))],
        note='The most useful view in the vault. A blank row is a project drifting.',
        empty='No active projects.')

    V['blocked'] = _view(
        'Blocked', ['Project', 'Blocked by'],
        [[_link(d['_title']), d['blocked_by']]
         for d in projects if d.get('blocked_by')],
        empty='Nothing blocked.')

    V['reviews_due'] = _view(
        'Due for review in the next 30 days', ['Note', 'Review', 'Type'],
        sorted([[_link(d['_title']), d['review'], d.get('type', '')]
                for d in notes if d.get('review') and str(d['review'])[:10] <= horizon],
               key=lambda r: r[1]),
        empty='Nothing due.')

    V['open_decisions'] = _view(
        'Open decisions', ['Decision', 'Decided', 'Review', 'Confidence'],
        [[_link(d['_title']), d.get('decided', ''), d.get('review', ''),
          d.get('confidence', '')]
         for d in notes if d.get('type') == 'decision' and d.get('status') == 'open'],
        note='Open one and read *what would change my mind*. Has any of it happened?',
        empty='No open decisions.')

    # -- ventures ------------------------------------------------------------
    ventures = sorted([d for d in projects if d.get('revenue_intent') == 'true'],
                      key=lambda d: -_num(d.get('revenue_to_date')))
    V['scoreboard'] = _view(
        'The scoreboard', ['Venture', 'Stage', '$', 'Users', 'Next action'],
        [[_link(d['_title']), d.get('stage', '—'), d.get('revenue_to_date', '—'),
          d.get('paying_users', d.get('customers', '—')), d.get('next_action', '')]
         for d in ventures],
        note='%d ventures, $%g between them.'
             % (len(ventures), sum(_num(d.get('revenue_to_date')) for d in ventures)),
        empty='No ventures.')

    V['all_built'] = _view(
        'Everything ever built, earning or not',
        ['Project', 'Status', 'Stage', 'Meant to earn?', '$'],
        sorted([[_link(d['_title']), d.get('status', ''), d.get('stage', ''),
                 d.get('revenue_intent', 'false'), d.get('revenue_to_date', '—')]
                for d in projects],
               key=lambda r: (r[3] != 'true', -_num(r[4] if r[4] != '—' else 0))),
        empty='No projects.')

    dead = {'dormant', 'archived', 'retired'}
    V['graveyard'] = _view(
        'Dead and dormant', ['Project', 'Status', 'Ended', 'Why'],
        sorted([[_link(d['_title']), d.get('status', ''), d.get('ended', '—'),
                 d.get('blocked_by', '')] for d in projects
                if d.get('status') in dead],
               key=lambda r: str(r[2]), reverse=True),
        note='Read this list before starting the next thing.',
        empty='Nothing dead.')

    # -- school --------------------------------------------------------------
    V['classes'] = _view(
        'Classes this semester', ['Class', 'Code', 'Status'],
        sorted([[_link(d['_title']), d.get('code', ''), d.get('status', '')]
                for d in notes if d.get('type') == 'class'], key=lambda r: r[1]),
        empty='No classes.')

    school = ('CSE', 'FSE', 'Capstone', 'Networks')
    V['school_tasks'] = _view(
        'Open school tasks', ['Task', 'Project', 'Due'],
        sorted([[t['text'], _link(t['project']), t['due'] or '—'] for t in open_tasks
                if any(k.lower() in t['project'].lower() for k in school)],
               key=lambda r: r[2]),
        empty='Nothing outstanding.')

    apps = applications()
    for key, field, label in (('apps_by_status', 'status', 'Status'),
                              ('apps_by_tier', 'tier', 'Tier')):
        counts = {}
        for a in apps:
            if a[field]:
                counts[a[field]] = counts.get(a[field], 0) + 1
        V[key] = _view('Applications, by ' + field, [label, 'Count'],
                       sorted(counts.items(), key=lambda kv: -kv[1]),
                       empty='No applications tracked.')

    V['apps_open'] = _view(
        'Everything not yet applied to', ['Company', 'Tier', 'Opens'],
        sorted([[a['company'], a['tier'], a['opens']] for a in apps
                if a['status'] == 'none'], key=lambda r: (r[1], r[0])),
        empty='All applied.')

    # -- drift ---------------------------------------------------------------
    # The three that follow all mean "this is going stale", and they disagree on
    # purpose: one is about a missing plan, one about the note, one about the work.
    V['drifting'] = _view(
        'Drifting — active, with no next action', ['Note', 'Type', 'Status'],
        [[_link(d['_title']), d.get('type', ''), d.get('status', '')]
         for d in projects + areas
         if d.get('status') in ('active', 'ongoing') and not d.get('next_action')],
        note='Active is a claim. A next action is the evidence.',
        empty='Everything active has a next action.')

    stale = [(d, _days_since(str(d.get('note_updated', '')))) for d in active]
    V['stale_notes'] = _view(
        'Stale — claims active, note untouched 30+ days',
        ['Project', 'Note last written', 'Days'],
        sorted([[_link(d['_title']), d.get('note_updated', '—'), n]
                for d, n in stale if n and n > 30], key=lambda r: -r[2]),
        empty='Every active note is current.')

    absent = []
    for d in active:
        n = _months_since(d.get('last_touched'))
        if n is None or n > 21:
            absent.append([_link(d['_title']), d.get('last_touched', 'never'),
                           n if n is not None else '—', d.get('next_action', '')])
    V['absent_from_logs'] = _view(
        'Said active, but absent from the logs',
        ['Project', 'Last touched', 'Days', 'Next action'],
        sorted(absent, key=lambda r: -(r[2] if isinstance(r[2], int) else 10**6)),
        note='`sync` fills last_touched from [[links]] in Log/, so this only tells '
             'the truth if you log.',
        empty='Everything active has recent evidence.')

    V['open_loops'] = _view(
        'Blocked and open loops', ['Project', 'Blocked by', 'Next action', 'Review'],
        [[_link(d['_title']), d.get('blocked_by', ''), d.get('next_action', ''),
          d.get('review', '')] for d in projects
         if d.get('blocked_by') or d.get('open_loop') == 'true'],
        empty='No open loops.')

    V['decisions_due'] = _view(
        'Decisions coming due', ['Decision', 'Status', 'Decided', 'Review'],
        sorted([[_link(d['_title']), d.get('status', ''), d.get('decided', ''),
                 d.get('review', '')] for d in notes
                if d.get('type') == 'decision' and d.get('review')],
               key=lambda r: str(r[3])),
        empty='No decisions on the books.')

    V['cad'] = _view(
        'Where the CAD actually goes', ['Project', 'Status', 'Stage'],
        [[_link(d['_title']), d.get('status', ''), d.get('stage', '')]
         for d in projects
         if 'cad' in [x.lower() for x in as_list(d.get('domain') or '')]],
        note='The query is the argument.', empty='No CAD projects.')

    # People used to be one hardcoded query per person. Counting them all is the
    # same work and stops the view from being about whoever was interesting once.
    who = {}
    for d in projects + areas:
        for person in as_list(d.get('people') or ''):
            person = person.split('#')[0].strip('[] ')
            if person and person.lower() != 'team':
                who.setdefault(person, []).append(d['_title'])
    V['people'] = _view(
        'Who is on what', ['Person', 'Projects', 'Where'],
        sorted([[_link(k), len(v), ', '.join(sorted(v))] for k, v in who.items()],
               key=lambda r: -r[1]),
        empty='No people mapped to projects.')

    return V

# Which views belong on which page, in order. The old Meta/*.md files are the
# spec: this is the same grouping, minus the duplication between them.
VIEW_PAGES = [
    ('now',       'Now',      'The one page to open in the morning.',
     ['next_flagged', 'next_actions', 'reviews_due', 'open_decisions',
      'blocked', 'tasks_by_project']),
    ('ventures',  'Ventures', 'Building is measured here. Selling is not, yet.',
     ['scoreboard', 'all_built', 'graveyard']),
    ('school',    'School',   'Coursework and the internship pipeline.',
     ['classes', 'school_tasks', 'apps_by_status', 'apps_by_tier', 'apps_open']),
    ('drift',     'Drift',    'Three different ways of going stale.',
     ['drifting', 'absent_from_logs', 'stale_notes', 'open_loops',
      'decisions_due', 'cad', 'people']),
]

def cmd_views(a):
    os.makedirs(INBOX, exist_ok=True)
    views = build_views()
    blob = {'generated': datetime.datetime.now().isoformat(timespec='seconds'),
            'pages': [{'key': k, 'title': t, 'note': n, 'views': vs}
                      for k, t, n, vs in VIEW_PAGES],
            'views': views}
    tmp = VIEWS_JSON + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(blob, fh, indent=1)
    os.replace(tmp, VIEWS_JSON)
    print('views: %d view(s) across %d page(s) -> %s'
          % (len(views), len(VIEW_PAGES), rel(VIEWS_JSON)))
    for k, t, _n, vs in VIEW_PAGES:
        print('  %-9s %s' % (k, ', '.join('%s(%d)' % (v, views[v]['count'])
                                          for v in vs if v in views)))
    return 0


def main():
    ap = argparse.ArgumentParser(prog='brain.py', description='vault command line')
    sub = ap.add_subparsers(dest='cmd')

    s = sub.add_parser('today');   s.add_argument('--date'); s.set_defaults(fn=cmd_today)
    s = sub.add_parser('lint');    s.set_defaults(fn=cmd_lint)
    s = sub.add_parser('status');  s.set_defaults(fn=cmd_status)
    s = sub.add_parser('sync');    s.set_defaults(fn=cmd_sync)
    s = sub.add_parser('metrics'); s.set_defaults(fn=cmd_metrics)

    s = sub.add_parser('touch'); s.add_argument('note'); s.add_argument('--date')
    s.set_defaults(fn=cmd_touch)

    s = sub.add_parser('metric'); s.add_argument('key'); s.add_argument('value')
    s.add_argument('--date'); s.add_argument('--note'); s.set_defaults(fn=cmd_metric)

    s = sub.add_parser('decide'); s.add_argument('title'); s.add_argument('--date')
    s.add_argument('--review-days', type=int, default=90); s.set_defaults(fn=cmd_decide)

    s = sub.add_parser('ingest-ics'); s.add_argument('source')
    s.add_argument('--match', default=None,
                   help='regex; keep only events whose summary matches')
    s.add_argument('--label', default='calendar'); s.set_defaults(fn=cmd_ingest_ics)
    sub.add_parser('calendars').set_defaults(fn=cmd_calendars)

    s = sub.add_parser('ingest-budget'); s.add_argument('csvfile')
    s.set_defaults(fn=cmd_ingest_budget)

    s = sub.add_parser('agenda'); s.add_argument('--days', type=int, default=14)
    s.set_defaults(fn=cmd_agenda)

    s = sub.add_parser('github'); s.add_argument('--since-days', type=int, default=30)
    s.add_argument('--full', action='store_true'); s.set_defaults(fn=cmd_github)

    s = sub.add_parser('queue');  s.set_defaults(fn=cmd_queue)
    s = sub.add_parser('views');  s.set_defaults(fn=cmd_views)
    s = sub.add_parser('canvas'); s.add_argument('--file'); s.add_argument('--days', type=int, default=21)
    s.set_defaults(fn=cmd_canvas)
    s = sub.add_parser('score'); s.add_argument('--window', type=int, default=30)
    s.add_argument('--force', action='store_true'); s.set_defaults(fn=cmd_score)

    s = sub.add_parser('event'); s.add_argument('match')
    s.add_argument('--date'); s.add_argument('--about'); s.add_argument('--why')
    s.set_defaults(fn=cmd_event)
    s = sub.add_parser('events'); s.add_argument('--pending', action='store_true')
    s.set_defaults(fn=cmd_events)

    s = sub.add_parser('inspect'); s.add_argument('repos', nargs='*')
    s.add_argument('--limit', type=int, default=12)
    s.add_argument('--readme-chars', type=int, default=6000)
    s.set_defaults(fn=cmd_inspect)

    s = sub.add_parser('catchup'); s.add_argument('--days', type=int, default=14)
    s.add_argument('--apply', action='store_true', default=True)
    s.set_defaults(fn=cmd_catchup)

    a = ap.parse_args()
    if not getattr(a, 'fn', None):
        ap.print_help(); return 0
    return a.fn(a)

if __name__ == '__main__':
    sys.exit(main())
