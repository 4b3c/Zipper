"""zipper.views

The saved queries, computed once into Inbox/views.json.
"""
import os, re, json, datetime, glob

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core


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
    horizon = (core.TODAY + datetime.timedelta(days=30)).isoformat()
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
