"""zipper.runqueue

The queue, and the bookkeeping pass over it.

There is **one** queue: `Inbox/feed.json`, an append-only stream of typed events
that happened outside the vault -- a push, a Canvas submission, a calendar
change. Each row carries a `kind` and, where one can be resolved, a `target`
note, so working through it is a matter of reading rather than inference. Rows
are ticked off individually and never re-appear.

Note edits are *not* rows. They are the working tree, read straight out of git
at bookkeeping time and cleared by committing -- git is the baseline, and there
is no second one. (There was, once: HISTORY.md, 2026-09-06.)

Flags are neither. They are conditions derived fresh from current state on every
run, so ticking one off is meaningless: it re-fires next run, and worse, looks
handled while the project it names keeps stalling. They are reported, never
queued.

`cmd_brief` renders all three into `Meta/Queue.md` -- the brief for a
bookkeeping pass; `cmd_fetch` pulls every input and ends by calling it. The
reading in between is not a command and cannot be one. `cmd_commit` ends the
pass by ticking the rows and committing the notes.
"""
import os, re, json, datetime, glob, subprocess

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core
from .events import resolve_events
from .metrics import _ledger_update
from . import canvas, gh, ics, status, sync, views


QUEUE_JSON = os.path.join(INBOX, 'queue.json')
FEED_JSON = os.path.join(INBOX, 'feed.json')

# ------------------------------------------------------------------ git

def _git(*args):
    """Run git in the vault. Returns stdout, or '' if git itself failed --
    the vault being a repo is load-bearing here, but a missing git must
    degrade to "no note changes", never take the whole brief down."""
    # core.quotePath=false: git otherwise renders any non-ASCII byte in a path
    # as an octal escape inside double quotes -- `Zipper \342\200\224 the
    # system.md`. Decoding that back is fiddly and was being got wrong (see
    # note_changes), so the fix is to never be handed it: with quotePath off,
    # git emits the real UTF-8 path and there is nothing to decode.
    try:
        r = subprocess.run(['git', '-C', VAULT, '-c', 'core.quotePath=false']
                           + list(args),
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
            # Only reachable for genuinely odd names now that quotePath is off
            # (a quote or a control character in the filename). The round trip
            # through latin-1 matters: `unicode_escape` maps each octal escape
            # to one *codepoint*, so the three bytes of an em dash come back as
            # three mojibake characters unless they are re-packed as bytes and
            # decoded as UTF-8. Getting this wrong silently produced a path that
            # matched no file, and `git add` aborts the whole batch on one bad
            # pathspec -- so a single em dash in a filename stopped four
            # unrelated notes from being committed at all.
            try:
                path = (path[1:-1].encode().decode('unicode_escape')
                        .encode('latin-1').decode('utf-8'))
            except (UnicodeDecodeError, UnicodeEncodeError):
                path = path[1:-1]
        if not path.endswith('.md') or _is_generated(path):
            continue
        action = ('add' if 'A' in code or '?' in code else
                  'delete' if 'D' in code else 'edit')
        # `when` is the file's mtime -- the one thing git genuinely knows about
        # an uncommitted change. `who` is absent and stays absent: git records
        # an author at commit time, and nothing anywhere records which session
        # wrote a byte to the working tree. Optional means absent, not guessed.
        when = None
        try:
            when = datetime.datetime.fromtimestamp(
                os.path.getmtime(os.path.join(VAULT, path))).isoformat(timespec='minutes')
        except OSError:
            pass
        out.append({'system': 'vault', 'action': action, 'path': path,
                    'when': when, 'clears': 'commit',
                    'text': '%-11s %s' % (action, path)})
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
    """Fill in system/action/target for a row, migrating pre-2026-09-06 ones.

    Rows written before the typed schema are flat display strings, so their
    system has to be read back out of the text. New rows arrive already typed
    and only need their target resolved -- the mapping is hand-maintained and
    can change after a row was written.
    """
    text = row.get('text', '')
    repos, courses = _target_map()
    out = dict(row)
    out.setdefault('clears', 'tick')
    if not out.get('system'):
        if text.startswith('pushed'):
            out['system'], out['action'] = 'github', 'push'
        elif text.startswith('submitted'):
            out['system'], out['action'] = 'canvas', 'submit'
        elif text.startswith('+ canvas'):
            out['system'], out['action'] = 'canvas', 'add'
        elif text.startswith(('+ calendar', '- calendar')):
            out['system'] = 'calendar'
            out['action'] = 'add' if text.startswith('+') else 'remove'
        elif text.startswith('error'):
            out['system'] = 'error'
        else:
            out['system'] = 'other'
    if not out.get('target'):
        if out['system'] == 'github':
            m = re.match(r'pushed\s+(\S+)', text)
            if m:
                out['target'] = repos.get(m.group(1))
        elif out['system'] == 'canvas':
            for code, note in courses.items():
                if code.replace(' ', '') in text.replace(' ', ''):
                    out['target'] = note
                    break
    return out

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

def fetch_all(a, emit=True):
    """Pull every input, regenerate everything derived from them, and turn what
    changed into queue rows.

    The emit half matters as much as the fetch: queue rows *are* the diff
    between two fetches, so whatever fetches has to be what publishes rows, or
    a push lands in the data with nothing in the queue pointing at it. Rows are
    written to Inbox/feed.json, and a running dashboard's feed watcher picks the
    file up within a second -- so this works the same from a timer, from the
    terminal, or from inside the server.

    A bookkeeping pass always starts here. The brief's whole claim is that it
    shows what is true *now* -- open events, an unreviewed diff, live flags --
    and rendering it over a stale fetch quietly breaks that: a push from an hour
    ago is missing, a submitted assignment still reads as due, and a flag fires
    or fails to fire on yesterday's data. Fetching separately made freshness a
    thing you had to remember; making it the first step of the pass makes it a
    thing you cannot skip.

    Every source is wrapped: a dead Canvas cookie or a GitHub outage must
    degrade the brief, never prevent it.
    """
    from . import serve
    serve.feed_load()
    before = serve.snapshot_data() if emit else None

    print('== github ==')
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
    # No canvas step. Canvas is no longer something this machine can pull: the
    # reading is taken by the browser extension and POSTed to /api/canvas
    # whenever he has Canvas open, so `Inbox/canvas.json` is already as current
    # as it is ever going to be by the time a pass starts. The old step existed
    # to beat `agenda` to the strike-throughs on a cookie that expired hourly;
    # with no fetch there is no ordering left to enforce. `zipper canvas` still
    # reports what was last read, and how long ago.
    print('\n== sync ==');   sync.cmd_sync(a)
    print('\n== agenda =='); a.days = getattr(a, 'days', 14) or 14; ics.cmd_agenda(a)
    print('\n== status =='); status.cmd_status(a)
    print('\n== views ==');  views.cmd_views(a)
    if emit:
        n = serve.emit_diff(before, serve.snapshot_data())
        print('\n== queue =='); print('queue: %d new row(s)' % n)


def cmd_fetch(a):
    """Pull the inputs, publish what changed, and write the brief.

    **This is the first step of a bookkeeping pass, not the pass.** Bookkeeping
    is fetch -> reasoning -> commit, and only the two ends are commands. The
    middle needs an agent: deciding that a push to `pantry` means the Pantry
    note's `next_action` is now wrong is a judgement about the vault's contents,
    and nothing here can make it. So this stops at handing over a brief.

    Fetching happens exactly twice: on the hour, and at the start of a
    bookkeeping pass. It used to also happen when the dashboard launched, which
    tied how fresh the data was to when a browser happened to open -- so the
    morning page was current and an all-day tab was a day stale.

    This also carries the Canvas cookie keepalive that `zipper canvas` used to.
    ASU issues no API tokens, the cookie is idle-timed, and exercising it hourly
    is what holds it open.
    """
    fetch_all(a, emit=True)
    print()
    return cmd_brief(a)


def cmd_brief(a):
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
    _write_brief(q)
    print('brief: %d open event(s), %d uncommitted note(s), %d flag(s)'
          % (len(rows), len(changes), len(fl)))
    print('  -> Meta/Queue.md  +  Inbox/queue.json')
    return 0

SYSTEM_LABEL = {'github': 'GitHub', 'calendar': 'Calendar', 'canvas': 'Canvas',
                'vault': 'Vault', 'error': 'Errors', 'other': 'Other'}
SYSTEM_ORDER = ('github', 'canvas', 'calendar', 'vault', 'error', 'other')


def _ev_line(e):
    """One event, one line: what it was, when, who, where it lands, how it clears.

    who and when are optional and simply absent when the source does not know
    them -- a push knows both, a calendar entry knows when it is but not who
    added it, a note edit knows when it was written and never who wrote it.
    """
    bits = []
    if e.get('when'):
        bits.append(str(e['when']).replace('T', ' '))
    if e.get('who'):
        bits.append(str(e['who']))
    meta = '  ·  %s' % ' · '.join(bits) if bits else ''
    tgt = '  → **[[%s]]**' % e['target'] if e.get('target') else ''
    key = '`%s` ' % e['key'] if e.get('key') else ''
    return '- %s%s%s%s' % (key, e.get('text', ''), meta, tgt)


def _write_brief(q):
    L = ['---', 'tags: [meta, view]', 'type: view', 'view_kind: generated',
         'status: living', 'source: zipper fetch',
         'generated: ' + core.TODAY.isoformat(), '---', '', '# Queue', '',
         '*Generated by `zipper fetch`. The brief for a bookkeeping pass: what '
         'happened, what is unreviewed, and what is wrong.*', '',
         '**As of:** %s' % q['generated'], '']

    events = list(q['events']) + list(q['notes_uncommitted'])
    L += ['## Events', '',
          '*One queue, every system. Each row is something that happened: work out '
          'what it affected, update the note, then clear it. **How a row clears is '
          'stated on the row** — `tick` means `zipper.serve --mark <key>`, `commit` '
          'means it goes away when the change is committed.*', '']
    if not events:
        L.append('- nothing open; the tree is clean')
    for sysname in SYSTEM_ORDER:
        rows = [e for e in events if e.get('system') == sysname]
        if not rows:
            continue
        clears = 'commit' if sysname == 'vault' else 'tick'
        L += ['', '### %s  <sub>clears by %s</sub>' % (SYSTEM_LABEL[sysname], clears), '']
        L += [_ev_line(e) for e in rows]
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
          '**This is a brief, not a report.** `zipper fetch` wrote it and stopped; '
          'the middle step of a bookkeeping pass is reasoning, and it needs an agent. '
          'Work each row to the note it affected, read the diff, then close the pass '
          'with `python3 -m zipper commit "<message>"`.', '',
          'Related: [[Status]] · [[Now]] · [[Review]] · [[Home]]', '']
    open(os.path.join(METADIR, 'Queue.md'), 'w', encoding='utf-8').write('\n'.join(L))

def cmd_commit(a):
    """Close a bookkeeping pass: tick every event and commit the notes.

    The last of the pass's three steps -- fetch, reason, commit -- and the only
    other one that is a command. What happens in between is an agent reading
    the brief against the vault, which is why there is no `bookkeep` command:
    naming one would suggest the machine does the part it cannot do.

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
    changes = note_changes()
    from . import serve, conversations, chat
    try:
        # Anyone else with a live terminal may be mid-edit. Nothing locks the
        # vault -- that was a deliberate call -- so the check is a warning, not
        # a mutex, and --force is the way past it.
        #
        # `chat.default_thread()` rather than a second os.environ read: the
        # variable is ZIPPER_DISCORD_THREAD, and a local guess at the name got
        # it wrong (ZIPPER_THREAD, never set by anything), so every commit from
        # inside a Discord thread counted *itself* as the other conversation
        # and demanded --force. One reader, one name.
        mine = chat.default_thread()
        live = [c for c in conversations.listing()
                if c.get('alive') and c.get('thread_id') != mine]
    except Exception:
        live = []
    if live and not getattr(a, 'force', False):
        print('commit: %d other conversation(s) live — committing now would '
              'sweep up their half-finished edits.' % len(live))
        print('  check with `python3 -m zipper conversations`, then re-run with --force')
        return 1
    serve.feed_load()
    marked = serve.feed_mark_all()['marked']
    paths = [c['path'] for c in changes]
    if paths:
        # Not check=False-and-forget. `git add` aborts the entire batch if one
        # pathspec matches nothing, so a single unreadable path silently took
        # every other note down with it and the only symptom was the "still
        # uncommitted" warning at the end, which reads like nothing was staged
        # rather than like staging failed.
        r = subprocess.run(['git', '-C', VAULT, 'add', '--'] + paths,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print('commit: git add failed, nothing staged --')
            for ln in (r.stderr or '').strip().split('\n')[:3]:
                print('  ' + ln)
    # The generated views used to be staged explicitly here, because a pass
    # rewrites them and leaving them dirty made `zipper commit`'s own
    # tree-is-clean check fail. They are gitignored as of 2026-09-08 -- the
    # brief cannot describe a clean tree without dirtying it, so tracking them
    # guaranteed one stray file after every pass -- and staging an ignored path
    # only earns a git warning. They still reach Obsidian: that goes over
    # CouchDB (see the vault's `Meta/Systems/CouchDB Sync`), not over git.
    r = subprocess.run(['git', '-C', VAULT, 'commit', '-m', a.message],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    print('commit: %d event(s) ticked, %s'
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
            print('    %s %s' % (c['action'], c['path']))
        return 1
    # Prune here rather than on every write. The end of a pass is the one
    # moment the queue is known to be reconciled, so it is the only safe place
    # to throw history away -- and only ticked rows are ever dropped.
    with open(serve.FEED_JSON, encoding='utf-8') as fh:
        blob = json.load(fh)
    kept = serve.feed_prune(blob.get('rows', []), serve.FEED_MAX)
    dropped = len(blob.get('rows', [])) - len(kept)
    blob['rows'] = kept
    with open(serve.FEED_JSON + '.tmp', 'w', encoding='utf-8') as fh:
        json.dump(blob, fh, indent=1)
    os.replace(serve.FEED_JSON + '.tmp', serve.FEED_JSON)
    if dropped:
        print('  pruned %d ticked row(s); %d kept' % (dropped, len(kept)))

    _write_brief({'generated': datetime.datetime.now().isoformat(timespec='seconds'),
                  'events': [], 'notes_uncommitted': [], 'tasks_dropped': [],
                  'tasks_renamed': [], 'flags': flags()})
    return 0

def cmd_queue(a):
    """Deprecated spelling. A pass is `fetch` -> reasoning -> `commit` now."""
    print('note: `queue` is now `fetch` — it pulls the inputs and writes the '
          'brief. Close the pass with `zipper commit "msg"`.\n')
    return cmd_fetch(a)
