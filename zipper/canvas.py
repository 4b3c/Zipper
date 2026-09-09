"""zipper.canvas

Canvas planner items -- the only source that knows submitted vs due.

**Nothing in this file talks to Canvas.** It parses a reading the browser has
already taken and hands the result to the vault. That is a deliberate amputation
(2026-09-08), not a gap waiting to be filled:

ASU issues no API access tokens to students, so the fetcher ran on a
`canvas_session` cookie copied by hand into `.env`. A copied cookie starts dying
the moment it is taken -- ASU rotated it twice inside one day on 2026-09-06 --
while the identical session inside a browser never dies, because the browser
renews it through SSO unasked. Nothing was ever wrong with the request; only the
credential's lifetime failed. So the request moved to where the credential
already lives, and only the answer crosses the network. See `extension/`.

What follows from that, and is worth not re-litigating: there is no
`CANVAS_TOKEN` path, no `CANVAS_SESSION` path, and no auth error to handle,
because there is no credential here that could be wrong. `ingest()` is the
single write path, reached from `POST /api/canvas` or `zipper canvas --file`.
"""
import os, re, json, datetime, html

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core


CANVAS_JSON = os.path.join(INBOX, 'canvas.json')
CANVAS_HOST = os.environ.get('CANVAS_HOST', 'https://canvas.instructure.com')
DESC_CAP = 6000          # a rubric-heavy assignment body, not a whole page

# --------------------------------------------------------------------------
# Crossing something off by hand
#
# **Canvas is not always right about what is done.** CSE 434's homework lives on
# PrairieLearn, an optional extra-credit item is one he has decided not to do --
# in both cases Canvas says `submitted: false` forever and is not going to change
# its mind. Abram's word is the better evidence, and this is where it is kept.
#
# The hard requirement is that it **survives a re-read**. The extension rewrites
# `canvas.json` wholesale every time he opens Canvas, so an override that lived
# on the item itself would be erased by the next visit and the finished homework
# would come back as outstanding -- which is exactly what it did.
#
# So overrides live beside the data rather than in it, and an item is matched
# back to its override two ways:
#
#   * **by key** -- course plus normalized title, so a re-read finds it again
#   * **by `plannable_id`** -- so an item that gets *retitled* stays crossed off
#
# Either match is enough. Between them, a crossed-off assignment reconnects to
# its own cross-off however Canvas chooses to re-describe it.
#
# **This is a display override and never a submission.** Nothing here is sent to
# Canvas, and nothing here makes a deadline go away.
OVERRIDES = os.path.join(INBOX, 'overrides.json')


def _ov_key(course, title):
    """`canvas:<course>|<normalized title>` -- the stable name of an item.

    Normalized through the same `_norm_title` the agenda uses, so the planner's
    'HW 01' and the ICS feed's 'HW 01 · CSE 434' cannot be crossed off
    separately and then disagree with each other.
    """
    return 'canvas:%s|%s' % (course, core._norm_title(title))


def _ov_load():
    """The override store, canonical and migrated.

    Keys written before 2026-09-09 hold the raw title rather than the normalized
    one, and values were a bare timestamp string. Both are read and rewritten in
    place, once -- a store that silently stopped matching after a format change
    would drop a cross-off on the floor, which is the one thing it exists to
    prevent.
    """
    try:
        raw = json.load(open(OVERRIDES, encoding='utf-8'))
    except Exception:
        return {}
    out, dirty = {}, False
    for k, v in (raw or {}).items():
        entry = {'at': v} if isinstance(v, str) else dict(v or {})
        course, _, title = k.partition('canvas:')[2].partition('|')
        key = _ov_key(course, entry.get('title') or title)
        if key != k or not isinstance(v, dict):
            dirty = True
        entry.setdefault('title', title)
        entry.setdefault('course', course)
        out[key] = entry
    if dirty:
        try:
            _ov_save(out)
        except OSError:
            pass                      # a read must never fail on a bad write
    return out


def _ov_save(d):
    os.makedirs(os.path.dirname(OVERRIDES), exist_ok=True)
    with open(OVERRIDES, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=1, sort_keys=True)


def stamp_overrides(rows):
    """Mark every row Abram has crossed off, in place.

    `done_by_hand` is when he crossed it off, or ''. Downstream, *done* means
    `submitted or done_by_hand` -- see `is_done`. The two stay separate fields
    on purpose: the dashboard says "you crossed this off", not "Canvas received
    it", and conflating them would be the system lying on his behalf.
    """
    ov = _ov_load()
    by_id = {str(e['id']): e for e in ov.values() if e.get('id')}
    dirty = False
    for r in rows or []:
        key = _ov_key(r.get('course', ''), r.get('title', ''))
        hit = ov.get(key) or by_id.get(str(r.get('plannable_id') or ''))
        r['done_by_hand'] = (hit or {}).get('at', '')
        if hit is not None and not hit.get('id') and r.get('plannable_id'):
            # Backfill: an entry crossed off before ids were stored, or before
            # the item had been seen. Learning the id here is what buys it the
            # retitle-resistance the key alone cannot give.
            hit['id'] = str(r['plannable_id'])
            dirty = True
    if dirty:
        try:
            _ov_save(ov)
        except OSError:
            pass
    return rows


def is_done(r):
    """Handed in, or crossed off by hand. The question every surface is asking."""
    return bool(r.get('submitted') or r.get('done_by_hand'))


def toggle_override(key):
    """Cross an item off, or put it back. Returns the new state.

    The full entry is recorded rather than just the key, so the store can still
    identify the item when Canvas renames it and so `zipper canvas` can name
    what has been crossed off without a lookup.
    """
    ov = _ov_load()
    if key in ov:
        ov.pop(key)
        state = False
    else:
        row = next((r for r in raw_items()
                    if _ov_key(r['course'], r['title']) == key), None)
        ov[key] = {'at': datetime.datetime.now().isoformat(timespec='seconds'),
                   'title': (row or {}).get('title', ''),
                   'course': (row or {}).get('course', ''),
                   'id': str((row or {}).get('plannable_id') or '')}
        state = True
    _ov_save(ov)
    return state


def raw_items():
    """What the browser last sent, exactly as it sent it."""
    try:
        return json.load(open(CANVAS_JSON, encoding='utf-8')).get('items', [])
    except Exception:
        return []


def items():
    """What the browser last sent, with the hand cross-offs applied.

    **Every surface reads Canvas through here.** The dashboard's two cards, the
    agenda's strike-through and the CLI report each used to load `canvas.json`
    for themselves, so crossing something off in one place left it outstanding
    in the others and the next re-read brought it back everywhere.
    """
    return stamp_overrides(raw_items())


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


def _index_descriptions(assignments):
    """{course_id: {assignment id or normalized title: body text}}.

    Keyed twice on purpose. A quiz's `plannable_id` is not an assignment id, so
    an id-only index misses exactly the items whose bodies say where the work
    really lives; the title fallback is scoped to one course so two courses
    with a "Homework 1" cannot borrow each other's instructions.
    """
    out = {}
    for cid, rows in (assignments or {}).items():
        idx = {}
        for a in rows or []:
            txt = _html_to_text((a or {}).get('description') or '')
            if not txt:
                continue
            idx[str(a.get('id'))] = txt
            idx.setdefault(_norm_title((a.get('name') or '').strip()), txt)
        out[str(cid)] = idx
    return out


def ingest(items, assignments=None, source='extension'):
    """Turn one browser reading into `Inbox/canvas.json`.

    **The only way Canvas data enters the vault.** Nothing here fetches: the
    engine has no Canvas credential and cannot get one. ASU issues no API
    tokens, and the session cookie that stood in for one had to be copied by
    hand into `.env`, where it began expiring immediately -- rotated twice
    inside a single day on 2026-09-06. The reading now happens in the browser,
    where the session is renewed through SSO without anyone being asked, and
    only the *answer* crosses the network. See `extension/`.

    Returns `(rows, skipped, described)`.
    """
    rows, skipped = _canvas_parse(items)
    idx = _index_descriptions(assignments)
    described = 0
    for r in rows:
        by_course = idx.get(r.get('course_id') or '', {})
        txt = by_course.get(r.get('plannable_id') or '') or by_course.get(_norm_title(r['title']))
        if txt:
            r['description'] = txt
            described += 1
        # Only meaningful while it is not submitted; once Canvas has a grade it
        # knows more than the description does.
        where = _elsewhere(r.get('description', ''))
        if where and not r['submitted']:
            r['elsewhere'] = where
    with open(CANVAS_JSON, 'w', encoding='utf-8') as fh:
        json.dump({'fetched': datetime.datetime.now().isoformat(timespec='seconds'),
                   'source': source, 'items': rows}, fh, indent=1)
    return rows, skipped, described


def _report(rows, skipped=None, described=None, stamp=None):
    stamp_overrides(rows)
    done = sum(1 for r in rows if r['submitted'])
    crossed = [r for r in rows if r.get('done_by_hand') and not r['submitted']]
    print('canvas: %d item(s), %d submitted, %d outstanding%s%s%s'
          % (len(rows), done, sum(1 for r in rows if not is_done(r)),
             ', %d crossed off' % len(crossed) if crossed else '',
             '  (%d skipped)' % skipped if skipped is not None else '',
             '  read %s' % stamp if stamp else ''))
    if described is not None:
        print('  descriptions: %d of %d item(s)' % (described, len(rows)))
    if crossed:
        # Named rather than merely counted: this is the one number in the report
        # that rests on his word instead of on Canvas, and it should be possible
        # to see what he took responsibility for without opening a JSON file.
        print('  crossed off by hand -- Canvas still calls these unsubmitted:')
        for r in crossed:
            print('    %s %s  (%s)' % (r['course'], r['title'][:40],
                                       r['done_by_hand'][:10]))
    ext = [r for r in rows if r.get('elsewhere') and not is_done(r)]
    if ext:
        print('  graded elsewhere -- Canvas cannot see these submitted:')
        for r in ext:
            print('    %s %s (%s)' % (r['course'], r['title'][:40], r['elsewhere']))
    late = [r for r in rows if not is_done(r) and (r['missing'] or r['late'])]
    if late:
        print('  MISSING: ' + '; '.join('%s %s' % (r['course'], r['title'][:40]) for r in late))
    by_day = {}
    for r in rows:
        if not is_done(r) and r['due']:
            by_day.setdefault(r['due'][:10], []).append(r)
    for d in sorted(by_day)[:6]:
        print('  %s  %d outstanding: %s' % (d, len(by_day[d]),
              ', '.join(sorted(set(x['course'] for x in by_day[d])))))


def cmd_canvas(a):
    """Show what the browser last sent, or ingest a saved dump.

    There is deliberately no fetch here any more. The command that used to pull
    from Canvas could only ever run on a credential this machine is not able to
    keep, so what it mostly did was fail once an hour and print how stale it
    had become. Freshness is now the extension's business, and this reports it.
    """
    if a.file:
        raw = open(os.path.expanduser(a.file), encoding='utf-8').read().lstrip()
        if raw.startswith('while(1);'):
            raw = raw[len('while(1);'):]          # Canvas' anti-JSON-hijack prefix
        body = json.loads(raw)
        if isinstance(body, dict):
            items, assignments = body.get('items') or [], body.get('assignments')
        else:
            items, assignments = body, None
        rows, skipped, described = ingest(items, assignments,
                                          source='file:' + os.path.basename(a.file))
        _report(rows, skipped, described)
        print('  -> %s' % rel(CANVAS_JSON))
        return 0

    if not os.path.exists(CANVAS_JSON):
        print('canvas: nothing read yet. The browser extension writes this -- '
              'see extension/README.md, then open Canvas.')
        return 1
    blob = json.load(open(CANVAS_JSON, encoding='utf-8'))
    rows = blob.get('items', [])
    stamp = blob.get('fetched', '?')
    age = ''
    try:
        secs = (datetime.datetime.now()
                - datetime.datetime.fromisoformat(stamp)).total_seconds()
        age = ' (%dh ago)' % (secs // 3600) if secs >= 3600 else ' (%dm ago)' % (secs // 60)
    except (TypeError, ValueError):
        pass
    _report(rows, stamp='%s%s via %s' % (stamp, age, blob.get('source', '?')))
    # Staleness here is a fact about his browsing, not a fault to fix. Say it
    # plainly and do not prescribe: the reading is as old as the last time he
    # had Canvas open, and no amount of nagging from a server changes that.
    return 0


def canvas_status_map():
    """{(YYYY-MM-DD, normalized title): done} for the agenda to annotate with.

    *Done*, not *submitted* -- a crossed-off item strikes through here too, or
    the dashboard would contradict itself between its own two cards.
    """
    out = {}
    for r in items():
        if not r['due']:
            continue
        out[(r['due'][:10], _norm_title(r['title']))] = is_done(r)
        # Canvas dates a 23:59 deadline on the day it falls; the ICS feed often
        # files the same item on the following date. Accept either.
        nxt = (datetime.date(*map(int, r['due'][:10].split('-')))
               + datetime.timedelta(days=1)).isoformat()
        out.setdefault((nxt, _norm_title(r['title'])), r['submitted'])
    return out
