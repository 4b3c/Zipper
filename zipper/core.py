#!/usr/bin/env python3
"""
zipper - the command line for the space/ vault.

Stdlib only. Run it from anywhere:

    ZIPPER_VAULT=~/path/to/vault python3 python3 -m zipper <command>

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
import os, sys, re, csv, json, argparse, datetime, urllib.request, glob, uuid
import subprocess, hashlib

HERE    = os.path.dirname(os.path.abspath(__file__))
# The code no longer has to live inside the data. ZIPPER_VAULT points at the
# notes; without it we fall back to the parent dir, which is the layout you get
# when this is dropped in as `<vault>/Scripts/`.
VAULT   = os.environ.get('ZIPPER_VAULT') or os.path.dirname(HERE)
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


# ------------------------------------------------- frontmatter
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

# ------------------------------------------------- shared helpers
#
# Small things used by more than one module, or by serve.py. They live
# here so no module has to import a sibling just to format a date.

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

def _days_since(iso):
    try:
        d = datetime.date(*[int(x) for x in iso[:10].split('-')])
        return (TODAY - d).days
    except Exception:
        return None

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

def _norm_title(t):
    """Reduce an ICS summary and a planner title to the same key.

    The ICS feed decorates titles the API does not: a ' · CSE 423' course tag
    added by _clean_canvas, and Canvas' own '(60362)' section numbers. Both have
    to go or nothing matches.
    """
    t = re.sub(r'\s*·.*$', '', t or '')       # ' · CSE 423'
    t = re.sub(r'\(\s*\d{3,}\s*\)', '', t)    # '(60362)' section numbers
    return re.sub(r'[^a-z0-9]+', '', t.lower())

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


# ------------------------------------------------- shared paths and patterns
#
# These were scattered through the file that became this package. They are
# genuinely shared -- the calendar config is written by the ICS ingest and read
# by the fetcher, the ledger by the score and the queue -- so they belong with
# the other vault paths rather than in whichever module happened to define them.

GH_JSON   = os.path.join(INBOX, 'github.json')
CAL_CFG   = os.path.join(INBOX, 'calendars.json')
LEDGER    = os.path.join(INBOX, 'task-ledger.json')
VIEWS_JSON = os.path.join(INBOX, 'views.json')

LINK_RE   = re.compile(r'\[\[([^\]|#]+)')
ANCHOR_RE = re.compile(r'\[\[([^\]|]+)')   # keeps the #section; see status

# `import *` skips underscore names, and several of the shared helpers above
# are underscore-prefixed by convention rather than by privacy -- they are the
# vocabulary every module speaks. Name them explicitly so the split stays a
# split, not a rewrite.
__all__ = [
    'HERE', 'VAULT', 'LOGDIR', 'METDIR', 'DECDIR', 'EVTDIR', 'INBOX',
    'METADIR', 'METCSV', 'TODAY', 'SKIP_DIRS', 'SKIP_FILES', 'ENUMS',
    'DATE_RE', 'EVT_DT_RE', 'MONTH_RE', 'DATE_FIELDS', 'MONTH_FIELDS',
    'NUM_FIELDS', 'BOOL_FIELDS', 'parse_fm', 'fm_dict', 'as_list', 'dump_fm',
    'iter_notes', 'read_note', 'write_note', 'title_of', 'rel', 'set_field',
    '_dt', '_fmt_dt', '_days_since', '_utc_local', '_split_repo',
    '_norm_title', '_months_ago', '_median', '_hhmm', '_dur', '_snip',
    '_place', 'TASK_RE', '_all_md', 'GH_JSON', 'CAL_CFG', 'LEDGER',
    'VIEWS_JSON', 'LINK_RE', 'ANCHOR_RE'
]
