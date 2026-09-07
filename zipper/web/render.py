"""HTML for the dashboard and the view pages.

Everything that turns data into markup. The stylesheet and the browser code
are literals in `css.py` and `js.py`.

Split out of `zipper/serve.py` on 2026-09-07. That file had grown to 2,788
lines, which meant no part of it could be read without loading all of it.
"""
from .base import *
from .base import core, conversations, metrics, usage
from .css import CSS
from .js import JS, TICKJS
from .conv import _queue_prompt, current_conversation
from .data import flags, ranked, today_split
from .feed import feed_rows, note_rows


# ---------------------------------------------------------------- render




def esc(s):
    return html.escape(s or '')


def _lanes(timed):
    """Blocks with start/end in minutes and a lane each, so overlaps sit side
    by side instead of stacking into one ambiguous column."""
    blocks = []
    for e in timed:
        st = core._dt(e['start'])
        en = core._dt(e.get('end') or '')
        if st is None:
            continue
        s0 = st.hour * 60 + st.minute
        if en is None or en <= st:
            e0 = s0 + 30                      # no DTEND in the feed
        elif en.date() != st.date():
            e0 = 24 * 60                      # runs past midnight
        else:
            e0 = en.hour * 60 + en.minute
        blocks.append({'s': s0, 'e': max(e0, s0 + 20), 'ev': e})
    blocks.sort(key=lambda b: (b['s'], b['e']))
    free = []
    for b in blocks:
        for i, until in enumerate(free):
            if b['s'] >= until:
                free[i] = b['e']; b['lane'] = i; break
        else:
            b['lane'] = len(free); free.append(b['e'])
    return blocks, max(1, len(free))


GCAL_IDS = {}

def _gcal_ids():
    """label -> Google calendar id, read out of the remembered feed URLs.

    A Google iCal URL is .../ical/<calendar id>/private-<token>/basic.ics, so the
    id is already on disk; Canvas URLs have no such segment and get skipped.
    """
    if GCAL_IDS:
        return GCAL_IDS
    path = os.path.join(core.INBOX, 'calendars.json')
    if os.path.exists(path):
        for label, cfg in json.load(open(path, encoding='utf-8')).items():
            m = re.search(r'/ical/([^/]+)/', cfg.get('url', '') or '')
            if m and 'google.com' in cfg.get('url', ''):
                GCAL_IDS[label] = urllib.parse.unquote(m.group(1))
    return GCAL_IDS


def _gcal_link(e, recurring):
    """The Google Calendar page for one event.

    Google's `eid` is base64url(event_id + ' ' + calendar_id). The ICS UID gives
    the *series* id, so a recurring occurrence needs the instance suffix
    `_<UTC stamp>` rebuilt from its local start. Verified against three real
    htmlLinks from the Calendar API, one of them a recurring instance.
    """
    cal = _gcal_ids().get(e['label'])
    uid = e.get('uid') or ''
    if not cal or '@google.com' not in uid:
        return ''
    eid_src = uid.split('@')[0]
    if recurring:
        st = core._dt(e['start'])
        if st is None:
            return ''
        utc = st.astimezone(datetime.timezone.utc)
        eid_src += '_' + utc.strftime('%Y%m%dT%H%M%SZ')
    eid = base64.urlsafe_b64encode(('%s %s' % (eid_src, cal)).encode()).decode().rstrip('=')
    return 'https://www.google.com/calendar/event?eid=' + eid


def _join_link(e):
    """A meeting URL hiding in the location field — Zoom, Meet, Teams."""
    for cand in (e.get('loc') or '', e.get('url') or ''):
        m = re.search(r'https?://\S+', cand)
        if m and re.search(r'zoom|meet\.google|teams\.microsoft|webex', m.group(0)):
            return m.group(0)
    return ''


PX_PER_MIN = 1.0

def _schedule_html(timed, is_today=True):
    """Today as a real calendar column: height is duration, not a bullet."""
    blocks, nlanes = _lanes(timed)
    if not blocks:
        return '<div><h2>Schedule</h2><p class="sub">Nothing scheduled.</p></div>'
    notes = events.event_note_map()
    seen = {}
    for f in sorted(glob.glob(os.path.join(core.INBOX, 'calendar-*.json'))):
        for ev in json.load(open(f, encoding='utf-8')).get('events', []):
            u = ev.get('uid') or ''
            seen[u] = seen.get(u, 0) + 1
    now = datetime.datetime.now()
    nowm = (now.hour * 60 + now.minute) if is_today else -10**6
    lo = min(b['s'] for b in blocks) // 60 * 60
    hi = -(-max(b['e'] for b in blocks) // 60) * 60
    if lo - 90 <= nowm <= hi + 90:            # frame the now-line in when it is near
        lo, hi = min(lo, nowm // 60 * 60), max(hi, -(-nowm // 60) * 60)
    height = (hi - lo) * PX_PER_MIN

    out = ['<div><h2>Schedule</h2><div class="grid" style="height:%dpx">' % height]
    for m in range(lo, hi + 1, 60):
        out.append('<div class="hr" style="top:%.1fpx"><span>%02d:00</span></div>'
                   % ((m - lo) * PX_PER_MIN, (m // 60) % 24))
    if lo <= nowm <= hi:
        out.append('<div class="nowline" style="top:%.1fpx"></div>'
                   % ((nowm - lo) * PX_PER_MIN))
    for b in blocks:
        e = b['ev']
        rec = notes.get((e.get('uid', ''), core._fmt_dt(e['start'])))
        h = (b['e'] - b['s']) * PX_PER_MIN - 2
        cls = 'blk' + (' past' if b['e'] <= nowm else '') + (' noted' if rec else '')
        style = ('--top:%.1fpx;--h:%.1fpx;--l:%.4f%%;--w:%.4f%%'
                 % ((b['s'] - lo) * PX_PER_MIN, h,
                    b['lane'] * 100.0 / nlanes, 100.0 / nlanes))
        span = '%02d:%02d\u2013%02d:%02d' % (b['s'] // 60, b['s'] % 60,
                                             b['e'] // 60, b['e'] % 60)
        meta = [span, core._dur(b['e'] - b['s'])]
        if e['loc'] and 'http' not in e['loc']:
            meta.append(e['loc'][:30])
        pin = ''
        if rec:
            pin = (' <span class="pin">debrief</span>' if rec['state'] == 'due'
                   else ' <span class="pin">note</span>')

        # The description belongs in the slot, clamped to the lines the block can
        # actually hold; a footer list meant reading the grid twice to answer one
        # question. Opening the block lifts the clamp -- the height becomes a
        # floor rather than a cap.
        why = ''
        if rec and rec.get('why'):
            why = '<div class="why">%s</div>' % ''.join(
                '<p>%s</p>' % esc(x) for x in (rec.get('why_all') or [rec['why']]))

        acts = []
        if rec:
            acts.append('<a class="act" href="obsidian://open?vault=space&amp;file=%s">%s</a>'
                        % (urllib.parse.quote('Events/' + rec['title']),
                           'write the debrief' if rec['state'] == 'due' else 'open note'))
        else:
            acts.append('<button class="act mknote" data-summary="%s" data-date="%s">'
                        '+ event note</button>' % (esc(e['summary']), e['date']))
        j = _join_link(e)
        if j:
            acts.append('<a class="act" href="%s" target="_blank" rel="noopener">join</a>'
                        % esc(j))
        g = _gcal_link(e, seen.get(e.get('uid') or '', 0) > 1)
        if g:
            acts.append('<a class="act" href="%s" target="_blank" rel="noopener">calendar</a>'
                        % g)
        if e['label'] == 'canvas' and e.get('url'):
            acts.append('<a class="act" href="%s" target="_blank" rel="noopener">Canvas</a>'
                        % esc(e['url']))
        acts.append('<span class="actdim">%s</span>' % esc(e['label']))

        out.append('<div class="%s" style="%s"><div class="bt">%s%s</div>'
                   '<div class="bm">%s</div>%s'
                   '<div class="bx"><div class="acts">%s</div></div></div>'
                   % (cls, style, esc(e['summary']), pin, esc(' \u00b7 '.join(meta)),
                      why, ''.join(acts)))
    out.append('</div></div>')
    return ''.join(out)


def _today_html(day=None):
    day = day or core.TODAY.isoformat()
    is_today = day == core.TODAY.isoformat()
    allday, timed = today_split(day)
    a = ['<div><h2>%s</h2><ul>' % ('Due today' if is_today else 'Due')]
    for e in allday:
        a.append('<li><span%s>%s</span> <span class="tag">%s</span></li>'
                 % (' class="done"' if e['done'] else '', esc(e['summary']), e['label']))
    a.append('</ul>%s</div>' % ('' if allday else '<p class="sub">Nothing due.</p>'))
    return ''.join(a) + _schedule_html(timed, is_today)


def _startbtns(live, ready):
    """First paint of the start box — the server-side twin of drawTerm().

    A page load is never the `mounted` state: the iframe is only ever attached by
    a click, so the choices here are the live pair or the cold pair.

    Four modes: blank/queue open a new conversation, resume/catchup return to
    the current one, and within each pair the only difference is whether the run
    queue is handed over. See `start_session`.
    """
    qd = '' if ready else ' disabled title="nothing in this run&#39;s queue to consume"'
    if live:
        return ('<button class="startbtn" data-mode="resume">resume conversation</button>'
                '<button class="startbtn" data-mode="catchup"%s>resume and clear queue</button>' % qd)
    return ('<button class="startbtn" data-mode="blank">start blank session</button>'
            '<button class="startbtn" data-mode="queue"%s>start session to clear queue</button>' % qd)


def _qrow_html(r):
    """A queue row. Read-only: nothing on this card is crossed off by hand.

    Ticking from the dashboard let a row be cleared without the reasoning that
    clearing it is supposed to stand for -- the same disagreement as "bookkeeping
    is a pass, not a command". A row means *something happened that the vault has
    not accounted for*, and the only thing that makes it accounted for is working
    out what it affected. A tick box offers to shorten the list without that, and
    a short list then reads as a reconciled one.

    So a row clears exactly two ways, both of which mean the work happened:
    `zipper commit` closing a pass, or `zipper.serve --mark` from the session
    that just did the reasoning. The ghost keeps the text aligned with the note
    rows, which have never had a box for a closely related reason.
    """
    return ('<div class="qrow%s"><span class="tick ghost"></span>'
            '<span class="qt">%s</span><span class="qx">%s</span></div>'
            % (' crossed' if r['done'] else '', esc(r['at']), esc(r['text'])))


def _qnotes_html(rows):
    """Changed notes, as ordinary queue rows.

    **Not a section of its own.** A note edit is an event like any other -- the
    queue is one list, and splitting it under a heading made the vault rows read
    as a different kind of thing that had to be dealt with separately. What is
    genuinely different is only how they clear, and the row already says that by
    carrying no tick box: these go when the pass commits.
    """
    if not rows:
        return ''
    body = ''.join(
        '<div class="qrow nrow"><span class="tick ghost"></span>'
        '<span class="qt">%s</span><span class="qx">%s</span></div>'
        % (esc((r.get('when') or '')[11:]),
           esc(r.get('text') or '%-11s %s' % (r['action'], r['path'])))
        for r in rows)
    return (body + '<p class="sub">Nothing here is crossed off by hand. '
            'The pass clears it &mdash; '
            '<code>python3 -m zipper commit "msg"</code></p>')


def _item_li(it, show_score=True):
    """One task row. Title leads; everything else drops to a dim second line.

    Priority leads that second line. It was a tooltip for a while, on the theory
    that a number beside the title competed with it — but the `#next` tag that
    was supposed to carry urgency is on half the open tasks, so it stopped
    discriminating. The score is the only thing that actually orders the list,
    so it says so out loud."""
    title = esc(it['title'])
    if it['url']:
        title = '<a class="plain" href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
    meta = []
    if show_score:
        meta.append('<span class="pri">%d</span>' % it['score'])
    if it['due']:
        meta.append('<span class="%s">%s</span>' % ('od' if it['overdue'] else '', it['due'][5:]))
    if it['tag']:
        meta.append(esc(it['tag']))
    if it.get('elsewhere'):
        # Canvas keeps only a grade column for these, so its "not submitted" is
        # silence, not a fact. Say which platform actually holds the work rather
        # than nagging about something already handed in.
        meta.append('<span class="elsewhere">on %s &mdash; Canvas can\'t tell</span>'
                    % esc(it['elsewhere']))
    return ('<li class="row %s" title="priority %d">'
            '<button class="tick" data-key="%s" aria-label="cross off">%s</button>'
            '<span class="rowbody"><span class="rowtitle">%s</span>'
            '<span class="rowmeta">%s</span></span></li>'
            % ('crossed' if it.get('done') else '', it['score'], esc(it['key']),
               '&#10003;' if it.get('done') else '', title, ' &middot; '.join(meta)))


def _side(items, empty):
    if not items:
        return '<p class="sub">%s</p>' % empty
    return '<ul>' + ''.join(_item_li(i) for i in items) + '</ul>'


def panels_html(day=None):
    _, allitems = ranked()
    cv = [i for i in allitems if i['source'] == 'canvas']
    tk = [i for i in allitems if i['source'] == 'task']
    return {'p-today': _today_html(day),
            'p-work-canvas': _side(cv[:8], 'Nothing outstanding in Canvas.'),
            'p-work-tasks': _side(tk[:8], 'No open tasks.')}


# ---------------------------------------------------------------- views
#
# `zipper views` computed these; this only draws them. One renderer for every
# view, because every view is the same shape -- which is the whole reason the
# Dataview queries were replaced with a JSON file rather than twenty panels.

VIEWS_JSON = os.path.join(core.INBOX, 'views.json')

def views_blob():
    try:
        return json.load(open(VIEWS_JSON, encoding='utf-8'))
    except Exception:
        return {'views': {}, 'pages': [], 'generated': ''}

def _cell(c):
    """A cell is a scalar, or {'link': 'Note'} when it should open in Obsidian."""
    if isinstance(c, dict) and 'link' in c:
        return ('<a class="vlink" href="obsidian://open?vault=%s&amp;file=%s">%s</a>'
                % (urllib.parse.quote(os.path.basename(core.VAULT)),
                   urllib.parse.quote(c['link']), esc(c['link'])))
    if c is None or c == '':
        return '<span class="vnone">—</span>'
    return esc(str(c))

def _view_html(v, compact=False):
    if not v:
        return ''
    head = ''.join('<th>%s</th>' % esc(c) for c in v['columns'])
    rows = v['rows'][:6] if compact else v['rows']
    body = ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % _cell(c) for c in r)
                   for r in rows)
    more = ''
    if compact and len(v['rows']) > 6:
        more = '<p class="sub vmore">+%d more</p>' % (len(v['rows']) - 6)
    if not v['rows']:
        return ('<div class="vwrap"><p class="sub">%s</p></div>' % esc(v['empty']))
    note = ('<p class="sub vnote">%s</p>' % esc(v['note'])) if v.get('note') and not compact else ''
    return ('<div class="vwrap">%s<table class="vtable"><thead><tr>%s</tr></thead>'
            '<tbody>%s</tbody></table>%s</div>' % (note, head, body, more))

def _views_page(page_key):
    blob = views_blob()
    pages = blob.get('pages', [])
    page = next((p for p in pages if p['key'] == page_key), None)
    if not page:
        return None
    nav = ' '.join('<a class="vnav%s" href="/views/%s">%s</a>'
                   % (' on' if p['key'] == page_key else '', p['key'], esc(p['title']))
                   for p in pages)
    cards = []
    for key in page['views']:
        v = blob['views'].get(key)
        if not v:
            continue
        cards.append('<div class="card"><h2>%s <span class="sub">&middot; %d</span></h2>%s</div>'
                     % (esc(v['title']), v['count'], _view_html(v)))
    stamp = blob.get('generated', '')[:16].replace('T', ' ')
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>
<style>%s</style></head><body><div class="wrap">
<h1>%s</h1><p class="sub">%s</p>
<p class="sub vnavbar"><a class="plain" href="/">&larr; today</a> &middot; %s</p>
%s
<footer><div class="fresh">computed %s &middot; <code>zipper views</code></div></footer>
</div></body></html>""" % (esc(page['title']), CSS, esc(page['title']),
                           esc(page['note']), nav, ''.join(cards), esc(stamp))


def _list_page(kind):
    _, allitems = ranked()
    items = [i for i in allitems if i['source'] == kind]
    label = 'Canvas' if kind == 'canvas' else 'Tasks'
    body = _side(items, 'Nothing here.')
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>
<style>%s</style></head><body><div class="wrap">
<h1>%s <span class="sub">&middot; %d</span></h1>
<p class="sub"><a class="plain" href="/">&larr; back to today</a></p>
<div class="card">%s</div>
</div><script>%s</script></body></html>""" % (label, CSS, label, len(items), body, TICKJS)




def render():
    f = freshness()
    _vb = views_blob()
    p = panels_html()
    fl = flags()
    try:
        sc, det = metrics.compute_score()
        met = ('<div class="metrics">' + ''.join(
            '<div class="metric"><span class="big">%s</span><small>%s</small></div>'
            % (sc[k], k.replace('_', ' ')) for k in
            ('stall_days_max', 'projects_drifting', 'tasks_open', 'tasks_overdue'))
            + '</div><p class="sub" style="margin:10px 0 0">oldest: %s</p>'
            % esc(', '.join('%s (%dd)' % (t, n) for t, n in det.get('oldest', [])[:2])))
    except Exception as e:
        met = '<p class="sub">%s</p>' % esc(str(e))

    epochs = {}
    for k, v in f.items():
        try:
            epochs[k] = datetime.datetime.fromisoformat(v).timestamp() if v else None
        except Exception:
            epochs[k] = None

    rows = feed_rows()
    nrows = note_rows()
    open_rows = [r for r in rows if not r['done']]
    done_rows = [r for r in rows if r['done']]
    feed = ''.join(_qrow_html(r) for r in open_rows) + _qnotes_html(nrows)
    if rows and not open_rows and not nrows:
        feed = '<p class="sub">All clear &mdash; everything this run turned up is dealt with.</p>'
    if done_rows:
        feed += ('<button class="qfold" id="qfold">&#9656; %d crossed off</button>'
                 % len(done_rows))
    # The count is what is outstanding, and an uncommitted note is outstanding.
    outstanding = len(open_rows) + len(nrows)
    live = bool(current_conversation())
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zipper</title><style>%s</style></head><body><div class="wrap">
<h1><span id="pagedate">%s</span> <button id="dorefresh" class="btn">refresh</button></h1><div class="sub" id="status"></div>

<div class="card"><h2 class="hdr"><span id="daylabel">Today</span>
<span class="daynav"><button id="daytoday" hidden>today</button><button id="dayprev" aria-label="previous day">&lsaquo;</button><button id="daynext" aria-label="next day">&rsaquo;</button></span></h2>
<div class="today" id="p-today">%s</div></div>

<div class="card"><h2>What to work on</h2>
<div class="today">
  <div><h2>Canvas <a class="more" href="/canvas" target="_blank" rel="noopener">see all</a></h2><div id="p-work-canvas">%s</div></div>
  <div><h2>Tasks <a class="more" href="/tasks" target="_blank" rel="noopener">see all</a></h2><div id="p-work-tasks">%s</div></div>
</div></div>

<div class="card" id="termcard"><h2>Claude <span id="termstate" class="sub">%s</span>
<button id="termnew" class="btn"%s>new conversation</button>
<button id="termfull" class="btn" hidden>fullscreen</button>
<a id="termpop" class="btn" href="#" target="_blank" rel="noopener" hidden>pop out</a></h2>
<div id="termstart">%s</div>
<div id="termbody"><aside id="chatside" hidden><div id="chatlist"></div><div id="usemeters"></div></aside><div id="termwrap"></div></div></div>

<div class="card"><h2>Next actions <a class="more" href="/views/now">see all</a></h2>%s</div>

<div class="card"><h2>Ventures <a class="more" href="/views/ventures">see all</a></h2>%s</div>

<div class="card"><h2>Signals</h2>
<div class="today">
  <div><h2>Flags (%d)</h2>%s</div>
  <div><h2>Execution</h2>%s</div>
</div></div>

<div class="card"><h2>Queue &middot; <span id="qcount">%d</span>
<span class="sub qfresh" id="qfresh"></span>
<button id="qrefetch" class="btn">refetch</button></h2>
<div id="queue"%s>%s</div></div>

<footer><div class="fresh" id="fresh"></div>
<div class="sub vnavbar">%s</div></footer>
</div><script>window.__epochs=%s;window.__feed=%s;window.__session=%s;window.__mounted=false;window.__showdone=false;window.__queueready=%s;
window.__today=%s;window.__day=window.__today;window.__canvashost=%s;
window.__notes=%s;
%s%s</script></body></html>""" % (
        CSS, core.TODAY.strftime('%A %d %B %Y'),
        p['p-today'], p['p-work-canvas'], p['p-work-tasks'],
        'running \u2014 not attached here' if live else 'not started',
        '' if live else ' hidden',
        _startbtns(live, bool(_queue_prompt())),
        _view_html(_vb['views'].get('next_actions'), compact=True),
        _view_html(_vb['views'].get('scoreboard'), compact=True),
        len(fl), ''.join('<div class="flag">%s</div>' % esc(x) for x in fl) or '<p class="sub">Clean.</p>',
        met, outstanding, '' if (rows or nrows) else ' data-empty="1"',
        feed or '<p class="sub">Waiting for this run’s fetch…</p>',
        ' &middot; '.join('<a class="vnav" href="/views/%s">%s</a>'
                         % (pg['key'], esc(pg['title'])) for pg in _vb.get('pages', [])),
        json.dumps(epochs), json.dumps(rows), json.dumps(bool(current_conversation())),
        json.dumps(bool(_queue_prompt())), json.dumps(core.TODAY.isoformat()),
        json.dumps(canvas.CANVAS_HOST), json.dumps(nrows), JS, TICKJS)
