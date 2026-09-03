"""zipper.canvas

Canvas planner items -- the only source that knows submitted vs due.
"""
import os, re, json, datetime
import urllib.request

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core


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

    Where the institution withholds tokens this raises, and --file is the way
    in. The parsing
    below is identical either way, so the token is a swap of transport only.
    """
    tok = os.environ.get('CANVAS_TOKEN')
    if not tok:
        raise RuntimeError('no CANVAS_TOKEN set')
    url = ('%s/api/v1/planner/items?start_date=%s&end_date=%s&per_page=100'
           % (CANVAS_HOST, (core.TODAY - datetime.timedelta(days=7)).isoformat(),
              (core.TODAY + datetime.timedelta(days=days)).isoformat()))
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
                  % (CANVAS_HOST, core.TODAY.isoformat(),
                     (core.TODAY + datetime.timedelta(days=a.days)).isoformat()))
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
