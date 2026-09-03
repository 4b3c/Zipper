"""zipper.runqueue

The vault diff between runs, and the flags derived from it.
"""
import os, re, json, datetime, glob, hashlib

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core
from .events import resolve_events
from .metrics import _ledger_update
from . import gh, ics, status, sync, views


STATE = os.path.join(INBOX, 'state.json')
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
            elif lt < (core.TODAY - datetime.timedelta(days=45)).isoformat()[:7]:
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
        if d.get('review') and d['review'] <= core.TODAY.isoformat():
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
         'status: living', 'source: zipper queue',
         'generated: ' + core.TODAY.isoformat(), '---', '', '# Queue', '',
         '*Generated by `zipper queue`. Everything that changed since the last run.*',
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
        gh.cmd_github(A())
    except Exception as e:
        print('github step skipped: %s' % e)
    print('\n== calendars ==')
    try:
        ics.cmd_calendars(a)
    except Exception as e:
        print('calendar step skipped: %s' % e)
    print('\n== sync ==');   sync.cmd_sync(a)
    print('\n== agenda =='); a.days = 14; ics.cmd_agenda(a)
    print('\n== status =='); status.cmd_status(a)
    print('\n== views ==');  views.cmd_views(a)
    print('\n== queue ==');  cmd_queue(a)
    print('\nDone. Message Claude: "catch me up" — it reads Meta/Queue.md.')
    return 0
