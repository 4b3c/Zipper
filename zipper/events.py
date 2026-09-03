"""zipper.events

Event notes, and reconciling them against the ingested calendars.
"""
import os, re, json, datetime, glob

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core


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
*Empty until it happens. `zipper queue` will ask.*

Related: [[Agenda]] · [[Home]]
"""

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
    floor = datetime.datetime.combine(core.TODAY, datetime.time.min)
    ahead = [e for e in cands if (_dt(e.get('start')) or floor) >= floor]
    pool = sorted(ahead or cands, key=lambda e: e.get('start', ''))
    if not pool:
        print('no ingested event matches "%s"%s'
              % (a.match, ' on ' + a.date if a.date else ''))
        print('run `zipper calendars` first, or widen the match')
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
        print('no event notes yet — make one with `zipper event "<summary>"`')
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
