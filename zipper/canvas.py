"""zipper.canvas

Canvas planner items -- the only source that knows submitted vs due.
"""
import os, re, json, datetime, html
import urllib.request, urllib.error

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core


CANVAS_JSON = os.path.join(INBOX, 'canvas.json')
CANVAS_HOST = os.environ.get('CANVAS_HOST', 'https://canvas.instructure.com')
DESC_CAP = 6000          # a rubric-heavy assignment body, not a whole page


class CanvasAuthError(Exception):
    """The credential is missing, wrong, or expired.

    Kept distinct from every other failure on purpose: an expired session must
    never be mistaken for 'nothing due', and must never leave the stale
    canvas.json in place while reporting success.
    """


def _canvas_auth():
    """Headers proving who we are. A token if the institution grants them,
    otherwise the session cookie from a browser that already cleared MFA.

    ASU does not issue access tokens, so CANVAS_SESSION is the live path here.
    The cookie is not a way around Duo -- it is what Duo produced. It carries
    that session's lifetime with it, which is why _canvas_get treats a login
    redirect as an error rather than as data.
    """
    tok = os.environ.get('CANVAS_TOKEN', '').strip()
    if tok:
        return {'Authorization': 'Bearer ' + tok}, 'token'
    sess = os.environ.get('CANVAS_SESSION', '').strip()
    if sess:
        # Accept either a bare canvas_session value or a whole pasted
        # `Cookie:` header -- the browser offers both and neither is wrong.
        cookie = sess if '=' in sess else 'canvas_session=' + sess
        return {'Cookie': cookie}, 'cookie'
    raise CanvasAuthError('no CANVAS_TOKEN and no CANVAS_SESSION set')


def _canvas_get(url, headers):
    """One authenticated GET. Returns (parsed json, next-page url).

    The failure that matters: with cookie auth an expired session does not
    return 401. Canvas 302s to the SSO login page and serves it with a cheerful
    200, so trusting the status code alone would parse a login form as an empty
    planner and quietly report that nothing is due. Anything that is not JSON
    is therefore an auth error.
    """
    req = urllib.request.Request(url, headers=dict(headers, Accept='application/json'))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode('utf-8', 'replace').lstrip()
            ctype = (r.headers.get('Content-Type') or '').lower()
            link = r.headers.get('Link', '')
            final = r.geturl()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise CanvasAuthError('Canvas returned %d -- credential rejected' % e.code)
        raise
    if body.startswith('while(1);'):
        body = body[len('while(1);'):]          # Canvas' anti-JSON-hijack prefix
    if 'json' not in ctype:
        raise CanvasAuthError('Canvas served %s from %s -- session expired'
                              % (ctype.split(';')[0] or 'no content-type', final))
    try:
        data = json.loads(body)
    except ValueError:
        raise CanvasAuthError('Canvas returned non-JSON -- session expired')
    m = re.search(r'<([^>]+)>\s*;\s*rel="next"', link)
    return data, (m.group(1) if m else None)


def _canvas_paged(url, headers, cap=1000):
    out = []
    while url and len(out) < cap:
        data, url = _canvas_get(url, headers)
        if not isinstance(data, list):
            break
        out.extend(data)
    return out


def _html_to_text(h):
    """Assignment bodies are HTML. Keep the prose and the list structure."""
    if not h:
        return ''
    t = re.sub(r'(?is)<(script|style).*?</\1>', ' ', h)
    t = re.sub(r'(?i)<br\s*/?>', '\n', t)
    t = re.sub(r'(?i)</(p|div|tr|h[1-6])>', '\n\n', t)
    t = re.sub(r'(?i)<li[^>]*>', '\n- ', t)
    t = re.sub(r'(?s)<[^>]+>', ' ', t)
    t = html.unescape(t)
    t = re.sub(r'[ \t\r\f\v]+', ' ', t)
    t = re.sub(r'\n\s*\n\s*\n+', '\n\n', t)
    return t.strip()[:DESC_CAP]

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
    """Pull planner items straight from the API.

    The parsing below is identical whether a token or a cookie got us in, so
    the credential is a swap of transport only.
    """
    headers, kind = _canvas_auth()
    url = ('%s/api/v1/planner/items?start_date=%s&end_date=%s&per_page=100'
           % (CANVAS_HOST, (core.TODAY - datetime.timedelta(days=7)).isoformat(),
              (core.TODAY + datetime.timedelta(days=days)).isoformat()))
    # per_page caps at 100; without following rel="next" a busy month is
    # silently truncated and the dashboard under-reports what is due.
    return _canvas_paged(url, headers), kind


def _canvas_descriptions(course_ids):
    """{course_id: {assignment_id: text, normalized title: text}}.

    What an assignment actually asks for -- the thing the ICS feed has never
    carried. These stay in Inbox/ and are never written into a note: an
    assignment body is a copy, and a copy goes stale in silence. Read them,
    conclude something, write the conclusion.
    """
    headers, _ = _canvas_auth()
    out = {}
    for cid in course_ids:
        url = ('%s/api/v1/courses/%s/assignments?per_page=100' % (CANVAS_HOST, cid))
        try:
            rows = _canvas_paged(url, headers)
        except CanvasAuthError:
            raise
        except Exception as e:
            print('  descriptions: course %s skipped (%s)' % (cid, e))
            continue
        idx = {}
        for a in rows:
            txt = _html_to_text(a.get('description') or '')
            if not txt:
                continue
            idx[str(a.get('id'))] = txt
            idx.setdefault(_norm_title((a.get('name') or '').strip()), txt)
        out[str(cid)] = idx
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
            'course_id': str(it.get('course_id')),
            'plannable_id': str(pl.get('id') or ''),
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

# Platforms that host the actual work while Canvas keeps only a grade column.
# Canvas cannot see a submission made on one of these, so `submitted` stays
# false on a finished assignment until the instructor enters a score -- and a
# false there is not evidence of anything. Verified on CSE 434 HW 01,
# 2026-09-06: description "HW1 is available on PrairieLearn", submissions/self
# unsubmitted with a null submitted_at, hours after the work was handed in.
EXTERNAL_PLATFORMS = ('PrairieLearn', 'Gradescope', 'zyBooks', 'zyLabs', 'Codio',
                      'WebAssign', 'MyLab', 'Mastering', 'Pearson', 'HackerRank',
                      'Cengage', 'MindTap', 'Top Hat', 'Perusall')


def _elsewhere(text):
    """The platform a description points at, if the assignment lives off Canvas.

    Deliberately dumb: a name in the body is the signal, because that one line
    is all these shells ever contain. It only ever adds a caveat to a `submitted:
    false` -- it never marks anything done -- so a false positive costs a note,
    not a missed deadline.
    """
    if not text:
        return ''
    low = text.lower()
    for name in EXTERNAL_PLATFORMS:
        if name.lower() in low:
            return name
    return ''


def cmd_canvas(a):
    kind = None
    if a.file:
        raw = open(os.path.expanduser(a.file), encoding='utf-8').read().lstrip()
        if raw.startswith('while(1);'):
            raw = raw[len('while(1);'):]          # Canvas' anti-JSON-hijack prefix
        items = json.loads(raw)
        src = 'file:' + os.path.basename(a.file)
    else:
        try:
            items, kind = _canvas_fetch(a.days)
            src = 'api:' + kind
        except CanvasAuthError as e:
            # Loud, and nothing is written. The old canvas.json stays exactly as
            # it was and keeps its old `fetched` stamp, so a stale submitted-flag
            # can still be spotted -- what must never happen is this failing
            # quietly and the vault claiming the data is current.
            print('canvas: NOT FETCHED -- %s' % e)
            print('  repaste the cookie: canvas.asu.edu -> devtools -> Application ->')
            print('  Cookies -> canvas_session, then set CANVAS_SESSION in /opt/zipper/.env')
            print('  (and `systemctl restart zipper-web` so running services see it)')
            if os.path.exists(CANVAS_JSON):
                blob = json.load(open(CANVAS_JSON, encoding='utf-8'))
                print('  %s is unchanged, fetched %s -- treat submitted/ flags as stale'
                      % (rel(CANVAS_JSON), blob.get('fetched', '?')))
            return 1
        except Exception as e:
            print('canvas: fetch failed -- %s' % e)
            return 1

    rows, skipped = _canvas_parse(items)

    descs = {}
    if not a.no_descriptions and not a.file:
        try:
            descs = _canvas_descriptions(sorted({r['course_id'] for r in rows if r.get('course_id')}))
        except CanvasAuthError as e:
            print('canvas: descriptions skipped -- %s' % e)
    got = 0
    for r in rows:
        idx = descs.get(r.get('course_id') or '', {})
        # by assignment id first; a quiz's plannable id is not an assignment id,
        # so fall back to the normalized title within the same course.
        txt = idx.get(r.get('plannable_id') or '') or idx.get(_norm_title(r['title']))
        if txt:
            r['description'] = txt
            got += 1
        # Only meaningful while it is not submitted; once Canvas has a grade it
        # knows more than the description does.
        where = _elsewhere(r.get('description', ''))
        if where and not r['submitted']:
            r['elsewhere'] = where

    with open(CANVAS_JSON, 'w', encoding='utf-8') as fh:
        json.dump({'fetched': datetime.datetime.now().isoformat(timespec='seconds'),
                   'source': src, 'items': rows}, fh, indent=1)
    done = sum(1 for r in rows if r['submitted'])
    ext = [r for r in rows if r.get('elsewhere')]
    late = [r for r in rows if r['missing'] or (r['late'] and not r['submitted'])]
    print('canvas: %d item(s), %d submitted, %d outstanding  (%d skipped) -> %s'
          % (len(rows), done, len(rows) - done, skipped, rel(CANVAS_JSON)))
    if descs:
        print('  descriptions: %d of %d item(s)' % (got, len(rows)))
    if ext:
        print('  graded elsewhere -- Canvas cannot see these submitted:')
        for r in ext:
            print('    %s %s (%s)' % (r['course'], r['title'][:40], r['elsewhere']))
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
