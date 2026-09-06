"""zipper.runqueue

The queue, and the bookkeeping pass over it.

There is **one** queue: `Inbox/feed.json`, an append-only stream of typed events
that happened outside the vault -- a push, a Canvas submission, a calendar
change. Each row carries a `kind` and, where one can be resolved, a `target`
note, so working through it is a matter of reading rather than inference. Rows
are ticked off individually and never re-appear.

Note edits are *not* rows. They are the working tree, read straight out of git
at bookkeeping time and cleared by committing. Until 2026-09-06 a second,
hand-rolled baseline in `state.json` hashed every note to answer the same
question git already answers, and its reset was a separate command -- which is
what made "clear the queue" mean two incompatible things. Git is the baseline
now.

Flags are neither. They are conditions derived fresh from current state on every
run, so ticking one off is meaningless: it re-fires next run, and worse, looks
handled while the project it names keeps stalling. They are reported, never
queued.

`cmd_bookkeep` renders all three into `Meta/Queue.md` -- the brief for a
bookkeeping pass -- and `--commit` ends that pass by ticking the rows and
committing the notes.
"""
import os, re, json, datetime, glob, subprocess

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core
from .events import resolve_events
from .metrics import _ledger_update
from . import canvas, gh, ics, status, sync, views


QUEUE_JSON = os.path.join(INBOX, 'queue.json')
FEED_JSON = os.path.join(INBOX, 'feed.json')
STATE = os.path.join(INBOX, 'state.json')      # legacy; removed on first run

# ------------------------------------------------------------------ git

def _git(*args):
    """Run git in the vault. Returns stdout, or '' if git itself failed --
    the vault being a repo is load-bearing here, but a missing git must
    degrade to "no note changes", never take the whole brief down."""
    try:
        r = subprocess.run(['git', '-C', VAULT] + list(args),
                           capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else ''
    except Exception:
        return ''

def _is_generated(path):
    """True for a `view_kind: generated` note.

    These are excluded from the note diff on purpose. `catchup` rewrites them
    every run, so they are never clean, and everything they say is an echo of
    an event already sitting in the queue as its own row -- Agenda restates the
    calendar rows, Repos restates the pushes. Reviewing them is reading the same
    change twice and mistaking it for two.
    """
    full = os.path.join(VAULT, path)
    if not os.path.exists(full):
        return False                    # deleted: report it, do not guess
    try:
        with open(full, encoding='utf-8') as fh:
            head = fh.read(400)
    except OSError:
        return False
    return bool(re.search(r'^view_kind:\s*generated\s*$', head, re.M))

def note_changes():
    """Uncommitted note edits, whoever made them.

    Authorship is deliberately not modelled. Abram, this session and another
    Discord session all edit the same files, and a change needs looking at
    because it is unreviewed, not because of who typed it.
    """
    out = []
    for line in _git('status', '--porcelain', '--untracked-files=all').split('\n'):
        if not line.strip():
            continue
        code, _, path = line[:2], line[2], line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1].encode().decode('unicode_escape')
        if not path.endswith('.md') or _is_generated(path):
            continue
        state = ('added' if 'A' in code or '?' in code else
                 'deleted' if 'D' in code else 'changed')
        out.append({'path': path, 'state': state})
    return sorted(out, key=lambda x: x['path'])

def note_diff(path, context=1):
    """The actual hunks for one note, so a bookkeeping pass can read what
    changed instead of re-reading the whole file to find out."""
    d = _git('diff', 'HEAD', '-U%d' % context, '--', path)
    if not d:
        d = _git('diff', '--no-index', '-U%d' % context, '--',
                 os.devnull, os.path.join(VAULT, path))
    body = [l for l in d.split('\n')
            if l[:1] in '+- ' and not l.startswith(('+++', '---'))]
    return '\n'.join(body)

# ------------------------------------------------------------------ the queue

def feed_rows():
    try:
        return json.load(open(FEED_JSON, encoding='utf-8')).get('rows', [])
    except Exception:
        return []

def open_rows():
    return [r for r in feed_rows() if not r.get('done')]

def _target_map():
    """repo name -> note title, and canvas course -> note title.

    Repos come from the hand-maintained `repos:` field, which is the source of
    truth for that mapping and stays so -- nothing here fuzzy-matches. Courses
    come from `feeds:` on a `Classes/` note: the project a course's work
    actually lands in, which no other field records.
    """
    def listy(v):
        # core.as_list only takes strings; parse_fm hands back a real list for
        # `[a, b]` and a bare string otherwise, and both spellings appear.
        if v is None:
            return []
        return v if isinstance(v, list) else as_list(v)

    repos, courses = {}, {}
    for p in iter_notes():
        d = fm_dict(read_note(p)[0])
        t = title_of(p)
        for r in listy(d.get('repos')):
            repos[str(r).split('/')[-1].strip()] = t
        if d.get('type') == 'class' and d.get('code'):
            feeds = [str(f).strip().strip('[]') for f in listy(d.get('feeds'))]
            if feeds:
                courses[str(d['code']).strip()] = feeds[0]
    return repos, courses

def classify(row):
    """Give a queue row a `kind` and, where possible, a `target` note.

    Rows were flat display strings until 2026-09-06, which meant "work out what
    this affected" had nothing to work from but the text. The text is still the
    thing rendered; kind and target are what make the row actionable.
    """
    text = row.get('text', '')
    repos, courses = _target_map()
    kind, target = 'other', None
    if text.startswith('pushed'):
        kind = 'push'
        m = re.match(r'pushed\s+(\S+)', text)
        if m:
            target = repos.get(m.group(1))
    elif text.startswith(('+ canvas', 'submitted')):
        kind = 'canvas'
        for code, note in courses.items():
            if code.replace(' ', '') in text.replace(' ', ''):
                target = note
                break
    elif text.startswith(('+ calendar', '- calendar')):
        kind = 'calendar'
    elif text.startswith('error'):
        kind = 'error'
    return dict(row, kind=kind, target=target)

# ------------------------------------------------------------------ flags

def _tasks():
    """Every checkbox in the vault, normalized. Feeds the task ledger, which
    keeps its own history and so needs no baseline of its own."""
    tasks = {}
    for p in _all_md():
        for line in open(p, encoding='utf-8').read().split('\n'):
            m = TASK_RE.match(line)
            if not m:
                continue
            text = re.sub(r'\[[a-z_]+::\s*(?:\[\[[^\]]+\]\]|[^\]]*)\]', '',
                          m.group(2)).strip()
            text = re.sub(r'#\w+', '', text).strip()
            if text:
                tasks[text[:120]] = (m.group(1).lower() == 'x')
    return tasks

def flags(evrecs=None):
    """Conditions, re-derived every run. Never queued, never ticked."""
    if evrecs is None:
        evrecs = resolve_events(fix=False)
    out = []
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
            out.append('%s was pushed to on %s but never appears in a log entry' %
                       (t, d['last_push']))
        if d.get('type') == 'project' and d.get('status') == 'active':
            lt = d.get('last_touched')
            if not lt:
                out.append('%s is active with no last_touched at all' % t)
            elif lt < (core.TODAY - datetime.timedelta(days=45)).isoformat()[:7]:
                # status_verified is the owner confirming the status despite the
                # evidence, so it suppresses this too -- but only for 45 days,
                # or a single confirmation would hide the drift permanently.
                sv = d.get('status_verified', '')
                if sv and _days_since(sv) is not None and _days_since(sv) <= 45:
                    pass
                else:
                    out.append('%s is active but untouched since %s%s'
                               % (t, lt, ' (last verified %s)' % sv if sv else ''))
        if d.get('repos') and d.get('last_push') and d.get('status') in (
                'dormant', 'idea', 'archived', 'retired'):
            n = _days_since(d['last_push'])
            verified = d.get('status_verified', '')
            if n is not None and n <= 120 and verified < d['last_push']:
                out.append('%s is marked %s but was pushed to on %s — status may be stale'
                           % (t, d['status'], d['last_push']))
        if d.get('review') and d['review'] <= core.TODAY.isoformat():
            out.append('%s is due for review (%s)' % (t, d['review']))
    # Event notes. A meeting he scheduled for a reason is not finished when it
    # ends -- it is finished when what came out of it is written down. This is
    # the only flag that expects an answer from him rather than an edit.
    for r in evrecs:
        when = (r['start'] or '?').replace('T', ' ')
        if r['state'] == 'due':
            out.append('event needs a debrief: "%s" (%s) — ask how it went, then '
                       'write it into %s' % (r['summary'] or r['title'], when,
                                             rel(r['path'])))
        elif r['state'] == 'dangling':
            out.append('event note matches nothing on the calendar: %s (%s) — '
                       'deleted, or outside the ingest window' % (rel(r['path']), when))
        elif r['moved_to']:
            out.append('event moved: "%s" is now %s — its note followed it'
                       % (r['summary'] or r['title'], r['moved_to'].replace('T', ' ')))
    return out

# ------------------------------------------------------------------ bookkeep

def cmd_bookkeep(a):
    os.makedirs(INBOX, exist_ok=True)
    # Reconcile event notes against the calendar *before* reading the working
    # tree. A rescheduled meeting rewrites its note, and doing that afterwards
    # would leave the rewrite sitting uncommitted with nothing explaining it.
    evrecs = resolve_events(fix=True)
    led = _ledger_update(_tasks())

    # Only this pass's drops. A 30-day window re-reported the same drop every
    # run for a month, which made a single edit look like sustained abandonment.
    seen = led.get('__meta__', {}).get('reported_at', '')
    dropped = sorted(t for t, e in led.items()
                     if t != '__meta__' and e.get('dropped_on', '') > seen)
    renamed = sorted(t for t, e in led.items()
                     if t != '__meta__' and e.get('renamed_on', '') > seen)
    led.setdefault('__meta__', {})['reported_at'] = datetime.datetime.now().isoformat()
    with open(LEDGER, 'w', encoding='utf-8') as fh:
        json.dump(led, fh, indent=1, sort_keys=True)

    fl = flags(evrecs)
    for t in dropped:
        fl.append('task left the list unfinished: "%s"' % t[:70])

    rows = [classify(r) for r in open_rows()]
    changes = note_changes()
    q = {'generated': datetime.datetime.now().isoformat(timespec='seconds'),
         'events': rows, 'notes_uncommitted': changes,
         'tasks_dropped': dropped, 'tasks_renamed': renamed, 'flags': fl}
    with open(QUEUE_JSON, 'w', encoding='utf-8') as fh:
        json.dump(q, fh, indent=1)
    # The old note/task/repo baseline. Git answers all three now, and leaving
    # the file behind invites someone to trust it.
    if os.path.exists(STATE):
        os.remove(STATE)

    if getattr(a, 'commit', None) is not None:
        return _finish(a, rows, changes)
    _write_brief(q)
    print('bookkeep: %d open event(s), %d uncommitted note(s), %d flag(s)'
          % (len(rows), len(changes), len(fl)))
    print('  -> Meta/Queue.md  +  Inbox/queue.json')
    return 0

KIND_LABEL = {'push': 'Pushes', 'canvas': 'Canvas', 'calendar': 'Calendar',
              'error': 'Errors', 'other': 'Other'}

def _write_brief(q):
    L = ['---', 'tags: [meta, view]', 'type: view', 'view_kind: generated',
         'status: living', 'source: zipper bookkeep',
         'generated: ' + core.TODAY.isoformat(), '---', '', '# Queue', '',
         '*Generated by `zipper bookkeep`. The brief for a bookkeeping pass: '
         'what happened, what is unreviewed, and what is wrong.*', '',
         '**As of:** %s' % q['generated'], '']

    L += ['## Events to handle', '',
          '*One queue. Each row is something that happened outside the vault. '
          'Work out what it affected, update the note, then tick it off.*', '']
    if not q['events']:
        L.append('- nothing open')
    for kind in ('push', 'canvas', 'calendar', 'error', 'other'):
        rows = [r for r in q['events'] if r['kind'] == kind]
        if not rows:
            continue
        L += ['', '### %s' % KIND_LABEL[kind], '']
        for r in rows:
            tgt = ' → **[[%s]]**' % r['target'] if r.get('target') else ''
            L.append('- `%s`  %s%s' % (r['key'], r['text'], tgt))
    L.append('')

    L += ['## Notes changed since the last pass', '',
          '*Every pass ends in a commit, so this is exactly what has changed since '
          'the last one — whoever wrote it, him or any session. Read it, make sure '
          'it is right, then commit. Cleared by committing, never by ticking.*', '']
    if not q['notes_uncommitted']:
        L.append('- nothing; the tree is clean')
    for c in q['notes_uncommitted']:
        L.append('- %s `%s`' % (c['state'], c['path']))
    L.append('')

    if q['tasks_dropped'] or q['tasks_renamed']:
        L += ['## Tasks', '']
        for t in q['tasks_dropped']:
            L.append('- dropped unfinished: %s' % t)
        for t in q['tasks_renamed']:
            L.append('- reworded (not dropped): %s' % t)
        L.append('')

    L += ['## Flags', '',
          '*Conditions, not events. These are re-derived every run and cannot be '
          'ticked off — a flag stops firing when the underlying data changes, and '
          'not before.*', '']
    L.extend(['- %s' % f for f in q['flags']] or ['- none firing'])
    L += ['', '---', '',
          'Hand this to Claude with: *"bookkeep"* — it works the events, reviews '
          'the diff, updates what they affect, and commits.', '',
          'Related: [[Status]] · [[Now]] · [[Review]] · [[Home]]', '']
    open(os.path.join(METADIR, 'Queue.md'), 'w', encoding='utf-8').write('\n'.join(L))

def _finish(a, rows, changes):
    """End a bookkeeping pass: tick every event and commit the notes.

    **A pass always ends in a commit.** That is not tidiness -- it is what makes
    the next pass's diff mean anything. The note diff is defined as "changed
    since the last bookkeeping pass", and the only thing making that true is
    that the last pass left the tree clean. Skip the commit once and the diff
    silently becomes general backlog, which is how the tree came to hold five
    sessions' conclusions with nothing marking where one ended.

    Ticking and committing are therefore one step: they are two halves of the
    same claim, that everything in this pass has been looked at and its
    consequences written down.
    """
    from . import serve, conversations
    try:
        # Anyone else with a live terminal may be mid-edit. Nothing locks the
        # vault -- that was a deliberate call -- so the check is a warning, not
        # a mutex, and --force is the way past it.
        mine = os.environ.get('ZIPPER_THREAD')
        live = [c for c in conversations.listing()
                if c.get('alive') and c.get('thread_id') != mine]
    except Exception:
        live = []
    if live and not getattr(a, 'force', False):
        print('bookkeep: %d other conversation(s) live — committing now would '
              'sweep up their half-finished edits.' % len(live))
        print('  check with `python3 -m zipper conversations`, then re-run with --force')
        return 1
    serve.feed_load()
    marked = serve.feed_mark_all()['marked']
    paths = [c['path'] for c in changes]
    if paths:
        subprocess.run(['git', '-C', VAULT, 'add', '--'] + paths, check=False)
    subprocess.run(['git', '-C', VAULT, 'add', '--'] +
                   [os.path.join('Meta', f) for f in
                    ('Status.md', 'Agenda.md', 'Queue.md', 'Repos.md')], check=False)
    r = subprocess.run(['git', '-C', VAULT, 'commit', '-m', a.commit],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    print('bookkeep: %d event(s) ticked, %s'
          % (marked, 'committed %d note(s)' % len(paths) if ok
             else 'nothing to commit'))
    if not ok and r.stdout.strip():
        print('  ' + r.stdout.strip().split('\n')[0])

    # The invariant, checked rather than assumed. Anything still dirty here
    # would silently widen the next pass's diff, and the whole point of the
    # commit is that it does not.
    left = note_changes()
    if left:
        print('  WARNING: %d note(s) still uncommitted — the next pass will show '
              'them as new:' % len(left))
        for c in left[:10]:
            print('    %s %s' % (c['state'], c['path']))
        return 1
    _write_brief({'generated': datetime.datetime.now().isoformat(timespec='seconds'),
                  'events': [], 'notes_uncommitted': [], 'tasks_dropped': [],
                  'tasks_renamed': [], 'flags': flags()})
    return 0

def cmd_queue(a):
    """Deprecated spelling. It used to reset a baseline that no longer exists."""
    print('note: `queue` is now `bookkeep` — there is only one queue, and this '
          'no longer resets anything.\n')
    a.commit = getattr(a, 'commit', None)
    return cmd_bookkeep(a)

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
    print('\n== canvas ==')
    try:
        # Submitted-vs-due comes from here and nowhere else, and the cookie it
        # runs on is short-lived, so this must happen before `agenda` strikes
        # items through. cmd_canvas already fails loudly and writes nothing on
        # an expired credential -- a bad fetch must not take the rest down.
        class C: file = None; days = 21; no_descriptions = False
        canvas.cmd_canvas(C())
    except Exception as e:
        print('canvas step skipped: %s' % e)
    print('\n== sync ==');   sync.cmd_sync(a)
    print('\n== agenda =='); a.days = 14; ics.cmd_agenda(a)
    print('\n== status =='); status.cmd_status(a)
    print('\n== views ==');  views.cmd_views(a)
    print('\n== bookkeep ==')
    a.commit = None
    cmd_bookkeep(a)
    print('\nDone. Message Claude: "bookkeep" — it reads Meta/Queue.md.')
    return 0
