"""Five candidate dashboards, at /look/1 .. /look/5.

Contenders, not replacements: the live page at `/` is untouched, and each of
these is a whole-page render of the same data so they can be compared by
looking rather than by argument. They share `data.py` with the real dashboard
and own nothing.

Round two. What carried over from round one, because he asked for it:

- **The day column never sets the page's height.** A fixed viewport scrolled to
  now, with a fixed 06:00-23:00 span inside. Block height still means duration.
- **Canvas work has its own section**, separate from `Tasks/`. Merging them
  into one ranked list threw away the distinction that matters: one has a
  deadline somebody else set, the other is his own note to self.
- **Clicking a day shows what is due that day.** The week is navigation, and
  selecting a day moves the schedule *and* the due list together.
- **Mono carries the furniture.** Labels, times, counts and metadata are
  monospaced; only titles and prose are not.

Every positioned element writes **one** `style` attribute. Round one emitted
two -- a `--hue` and then the geometry -- and a duplicate attribute is not an
error, it is silently dropped: browsers keep the first and discard the rest. So
every block lost its `top`, `height`, `left` and `width` and stacked at the
container origin, which read as "everything is at 6am". The arithmetic was
right the whole time. `_style()` exists so that cannot happen again.
"""
from .base import *
from .base import core, canvas, events, metrics
from .js import TICKJS
from .data import (canvas_items, class_notes, flags, monday_of, open_tasks,
                   priority, ranked, today_split, week_canvas)
from .render import _gcal_link, _join_link, _lanes, esc


LOOKS = {}          # n -> (title, blurb, renderer)

DOW = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')

# The day column always spans the same hours, whatever is booked. 06:00-23:00
# covers every event these calendars have carried; anything outside it renders
# clamped to the edge, because a clipped block beats a column that resizes the
# page.
DAY_LO, DAY_HI = 6 * 60, 23 * 60
SPAN = DAY_HI - DAY_LO


def _style(*parts):
    """One style attribute, always.

    Two `style=` attributes on one tag is not a parse error -- the browser keeps
    the first and drops the rest. That is how every block in round one lost its
    geometry and piled up at 06:00 while the numbers behind it were correct.
    Everything positioned here goes through this.
    """
    return 'style="%s"' % ';'.join(p for p in parts if p)


def hue(name):
    """A stable hue per course/label. Not random per load -- the point of a
    colour is that CSE 434 is the same colour tomorrow."""
    if not name:
        return 210
    h = 0
    for ch in str(name):
        h = (h * 31 + ord(ch)) % 360
    return (h * 47) % 360


def _blocks(day, is_today):
    """Positioned blocks for one day, as fractions of the fixed span.

    Percentages rather than pixels: each look gives the day a different fixed
    height, and one builder serves a 430px rail, a 150px horizontal band and a
    seven-column week grid without a second set of numbers.
    """
    _, timed = today_split(day)
    laned, nlanes = _lanes(timed)
    notes = events.event_note_map()
    now = datetime.datetime.now()
    nowm = now.hour * 60 + now.minute if is_today else None
    out = []
    for b in laned:
        e = b['ev']
        s, en = max(b['s'], DAY_LO), min(b['e'], DAY_HI)
        if en <= DAY_LO or s >= DAY_HI:
            continue
        out.append({
            'ev': e, 'rec': notes.get((e.get('uid', ''), core._fmt_dt(e['start']))),
            'top': (s - DAY_LO) * 100.0 / SPAN,
            'h': max(en - s, 16) * 100.0 / SPAN,
            'lane': b['lane'], 'nlanes': nlanes, 'hue': hue(e['label']),
            'past': bool(nowm is not None and b['e'] <= nowm),
            'live': bool(nowm is not None and b['s'] <= nowm < b['e']),
            'span': '%02d:%02d–%02d:%02d' % (b['s'] // 60, b['s'] % 60,
                                                  b['e'] // 60, b['e'] % 60),
            'from': '%02d:%02d' % (b['s'] // 60, b['s'] % 60),
            'mins': b['e'] - b['s'], 's': b['s'], 'e': b['e'],
        })
    nowpct = ((nowm - DAY_LO) * 100.0 / SPAN
              if nowm is not None and DAY_LO <= nowm <= DAY_HI else None)
    return out, nowpct


def _hours(step=1):
    return [(m, (m - DAY_LO) * 100.0 / SPAN)
            for m in range(DAY_LO, DAY_HI + 1, 60 * step)]


def _blk_acts(e, rec, cls='act'):
    """The same actions the live grid offers, in shape if not in markup."""
    acts = []
    if rec:
        acts.append('<a class="%s" href="obsidian://open?vault=%s&amp;file=%s">%s</a>'
                    % (cls, urllib.parse.quote(os.path.basename(core.VAULT)),
                       urllib.parse.quote('Events/' + rec['title']),
                       'write the debrief' if rec['state'] == 'due' else 'open note'))
    j = _join_link(e)
    if j:
        acts.append('<a class="%s" href="%s" target="_blank" rel="noopener">join</a>'
                    % (cls, esc(j)))
    g = _gcal_link(e, False)
    if g:
        acts.append('<a class="%s" href="%s" target="_blank" rel="noopener">calendar</a>'
                    % (cls, g))
    return ''.join(acts)


def _why(rec):
    if not (rec and rec.get('why')):
        return ''
    return '<div class="why">%s</div>' % ''.join(
        '<p>%s</p>' % esc(x) for x in (rec.get('why_all') or [rec['why']]))


def plain(title):
    """A task title without its wiki-link brackets. Display only -- the ledger
    keys on the raw string, so nothing downstream sees this."""
    return esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', title))


def canvas_on(day):
    """Canvas work due on one date, newest ranking applied.

    Its own function because every look now has a *Due <day>* section, and the
    whole point of the day chips is that this list and the schedule move
    together.
    """
    cls, _ = class_notes()
    out = []
    for r in canvas_items():
        if r['due'][:10] != day:
            continue
        it = {'source': 'canvas', 'title': r['title'], 'due': r['due'][:10],
              'at': r['due'][11:16], 'tag': r['course'], 'url': r['url'],
              'points': r.get('points'), 'next': False,
              'elsewhere': r.get('elsewhere', ''), 'kind': r.get('type', ''),
              'links': [cls[r['course']]] if r['course'] in cls else [],
              'submitted': bool(r['submitted']), 'done': bool(canvas.is_done(r))}
        it['score'] = priority(it)
        it['overdue'] = bool(it['due'] < core.TODAY.isoformat() and not it['done'])
        it['key'] = canvas._ov_key(it['tag'], it['title'])
        out.append(it)
    out.sort(key=lambda i: (i['done'], i['at'] or '99:99', i['title']))
    return out


def task_rows():
    """`Tasks/` lines only. Never mixed into a Canvas list again."""
    _, allitems = ranked(limit=1)
    return [i for i in allitems if i['source'] == 'task']


def done_task_rows():
    """Ticked `Tasks/` lines. `data.open_tasks` drops these by design, so the
    Done tab needs its own pass over the same files.

    Keyed exactly like an open task -- `data.override_key`'s task branch -- so
    the tick box un-ticks the real markdown line rather than orphaning it.
    """
    from .data import task_text
    out = []
    for p in sorted(glob.glob(os.path.join(core.VAULT, 'Tasks', '*.md'))):
        for line in open(p, encoding='utf-8'):
            m = core.TASK_RE.match(line)
            if not m or m.group(1).lower() != 'x':
                continue
            raw = m.group(2)
            proj = re.search(r'\[project::\s*\[\[([^\]]+)\]\]', raw)
            due = re.search(r'\[due::\s*(\d{4}-\d{2}-\d{2})\]', raw)
            text = task_text(raw)
            tag = proj.group(1) if proj else ''
            out.append({'source': 'task', 'title': text, 'tag': tag,
                        'due': due.group(1) if due else '', 'done': True,
                        'overdue': False, 'points': 0, 'url': '', 'at': '',
                        # Same alias trap as `data.override_key` -- the tag must
                        # not contain the delimiter or the title cannot be read
                        # back out of the key.
                        'key': 'task:%s|%s' % (tag.split('|')[0], text),
                        'score': 0})
    return out


def _page(title, css, body, extra_js=''):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s%s</style></head><body>%s'
            '<script>%s%s</script></body></html>'
            % (esc(title), BASE_CSS, css, body, TICKJS, SCROLLJS + extra_js))


SCROLLJS = """
// Open on now, not on 06:00. Every look has a day column and none of them
// should make him scroll to find the afternoon.
document.querySelectorAll('[data-scrollnow]').forEach(el=>{
  const n=el.querySelector('.nowline')||el.querySelector('.blk');
  if(n) el.scrollTop=Math.max(0,n.offsetTop-el.clientHeight*0.35);
});
document.querySelectorAll('[data-scrollnow-x]').forEach(el=>{
  const n=el.querySelector('.nowline')||el.querySelector('.blk');
  if(n) el.scrollLeft=Math.max(0,n.offsetLeft-el.clientWidth*0.3);
});
document.addEventListener('click',e=>{
  const b=e.target.closest('.blk'); if(!b||e.target.closest('a')) return;
  b.classList.toggle('open');
});
document.querySelectorAll('[data-tabs]').forEach(w=>{
  w.addEventListener('click',e=>{
    const t=e.target.closest('[data-tab]'); if(!t) return;
    w.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('on',x===t));
    w.querySelectorAll('[data-pane]').forEach(p=>p.hidden=p.dataset.pane!==t.dataset.tab);
  });
});
"""

# `--mono` is the one thing every look inherits: he liked look 3's typography,
# so the furniture -- labels, clocks, counts, metadata -- is monospaced
# everywhere now, and only titles and prose are not.
BASE_CSS = """
:root{--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
a{color:inherit}
ul{list-style:none;margin:0;padding:0}
.tick{flex:none;width:17px;height:17px;margin-top:2px;border:1.5px solid currentColor;opacity:.5;
  border-radius:5px;background:none;color:inherit;cursor:pointer;font:11px/1 var(--mono);
  padding:0;display:flex;align-items:center;justify-content:center}
.tick:hover{opacity:1}
.tick[disabled]{opacity:.25;cursor:default}
li.crossed{opacity:.42}
li.crossed .rowtitle{text-decoration:line-through}
.rowbody{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1}
.rowtitle{line-height:1.3;overflow-wrap:anywhere}
.rowmeta{font:11px/1.45 var(--mono);opacity:.7}
.looknav{display:flex;gap:6px;flex-wrap:wrap;align-items:center;
  font:11px/1 var(--mono);padding:10px 0 0;letter-spacing:.04em}
.looknav a{text-decoration:none;opacity:.55;padding:4px 9px;border-radius:99px;
  border:1px solid currentColor}
.looknav a.on,.looknav a:hover{opacity:1}
[hidden]{display:none!important}
"""


def looknav(n):
    links = ''.join('<a class="%s" href="/look/%d">%d</a>'
                    % ('on' if i == n else '', i, i) for i in sorted(LOOKS))
    return ('<nav class="looknav"><a href="/">&larr; live</a>'
            '<span style="opacity:.35">contenders</span>%s'
            '<span style="opacity:.45;margin-left:auto">%s</span></nav>'
            % (links, esc(LOOKS[n][0])))


def daystrip_days(day):
    mon = monday_of(day)
    return [(mon + datetime.timedelta(days=i)).isoformat() for i in range(7)]


# ================================================================ 1. Rail

# One row, four panels, one height. The height is a variable rather than a
# number in four places -- "make them all the same height" is a rule, and a
# rule stated once cannot drift.
RAIL_CSS = """
:root{--bg:#fbfaf7;--fg:#1a1916;--dim:#726c62;--line:#e6e1d8;--card:#fff;
  --accent:#1f5f4f;--warn:#a3521c;--paper:#f3f0e9;--tl:35%;--ph:620px}
@media(prefers-color-scheme:dark){:root{--bg:#121311;--fg:#eceae4;--dim:#8f8a80;
  --line:#272825;--card:#191a18;--accent:#6fcfae;--warn:#dd9455;--paper:#1f201d;--tl:68%}}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:1380px;margin:0 auto;padding:16px 22px 46px}
.head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:14px 0 14px}
h1{font:600 21px/1.2 var(--sans);margin:0;letter-spacing:-.015em}
.head .meta{font:10.5px/1 var(--mono);color:var(--dim);letter-spacing:.11em;text-transform:uppercase}
.head .back{margin-left:auto;font:11px/1 var(--mono);color:var(--accent);text-decoration:none}

.cols{display:grid;grid-template-columns:132px 216px minmax(0,1fr) minmax(0,1fr);gap:12px;
  align-items:start}
@media(max-width:1180px){.cols{grid-template-columns:132px 216px minmax(0,1fr)}
  .panel.tasks{grid-column:1/-1}}
@media(max-width:820px){.cols{grid-template-columns:1fr}.panel{height:auto!important;max-height:var(--ph)}
  .panel.day{height:var(--ph)!important}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:13px;
  height:var(--ph);display:flex;flex-direction:column;overflow:hidden}
.ph{padding:12px 14px 10px;display:flex;align-items:center;gap:8px;flex:none;
  font:600 10.5px/1 var(--mono);text-transform:uppercase;letter-spacing:.12em;color:var(--dim);
  border-bottom:1px solid var(--line)}
.ph .n{margin-left:auto;color:var(--accent)}
/* The lists scroll inside their panel; the day never does. */
.pb{flex:1;min-height:0;overflow-y:auto;padding:4px 14px 12px}
.pb.fit{overflow:hidden;padding:8px 10px 10px}

/* --- 1. the week, down the left --------------------------------------- */
.wk{display:flex;flex-direction:column;height:100%;padding:6px}
.wd{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;
  text-decoration:none;border-radius:9px;padding:5px 9px;border:1px solid transparent}
.wd:hover{background:var(--paper)}
.wd.on{border-color:var(--accent);background:var(--paper)}
.wd b{font:600 10px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase;color:var(--dim)}
.wd.on b,.wd.today b{color:var(--accent)}
.wd .num{font:600 18px/1.25 var(--mono)}
.wd .sub{font:9.5px/1.4 var(--mono);color:var(--dim);display:flex;gap:5px;align-items:center}
.wd .bars{display:flex;gap:2px;height:3px;margin-top:4px}
.wd .bars i{width:11px;border-radius:2px}

/* --- 2. the day, vertical and whole ------------------------------------ */
/* 06:00 to 23:00 compressed to fit the panel exactly -- no scrolling, and no
   height that depends on what is booked. Block height is still duration, just
   at a tighter scale than a scrolling column could afford. */
.day{position:relative;height:100%}
.hr{position:absolute;left:0;right:0;border-top:1px solid var(--line)}
.hr.q{border-top-style:dotted;opacity:.5}
.hr span{position:absolute;top:-5px;left:0;font:9px/1 var(--mono);color:var(--dim)}
.nowline{position:absolute;left:26px;right:0;border-top:2px solid var(--warn);z-index:6}
.nowline:after{content:'';position:absolute;left:-4px;top:-4px;width:6px;height:6px;
  border-radius:50%;background:var(--warn)}
.blk{position:absolute;border-radius:5px;padding:1px 5px;overflow:hidden;cursor:pointer;z-index:2;
  background:hsl(var(--hue) 55% 50% / .17);border-left:2px solid hsl(var(--hue) 50% 42%);
  display:flex;flex-direction:column;justify-content:center;min-height:11px}
@media(prefers-color-scheme:dark){.blk{background:hsl(var(--hue) 45% 55% / .22);
  border-left-color:hsl(var(--hue) 55% 60%)}}
.blk.past{opacity:.42}
.blk.live{box-shadow:0 0 0 1px var(--warn)}
.blk.open{height:auto!important;min-height:var(--h);z-index:20;overflow:visible;
  left:8px!important;right:4px!important;width:auto!important;justify-content:flex-start;
  background:var(--card);border:1px solid var(--line);border-left:2px solid hsl(var(--hue) 50% 42%);
  box-shadow:0 12px 30px rgba(0,0,0,.26);padding:7px 9px}
.bt{font:600 10.5px/1.2 var(--sans);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.blk.open .bt{white-space:normal;font-size:12.5px}
.bm{font:9px/1.35 var(--mono);color:var(--dim);white-space:nowrap;overflow:hidden}
.why{display:none;font:11.5px/1.45 var(--sans);color:var(--dim);margin-top:6px;
  border-top:1px solid var(--line);padding-top:6px}
.blk.open .why{display:block}
.why p{margin:0 0 5px}
.why p:last-child{margin:0}
.acts{display:none;gap:5px;flex-wrap:wrap;margin-top:7px}
.blk.open .acts{display:flex}
.act{font:10px/1.5 var(--mono);border:1px solid var(--line);border-radius:5px;padding:2px 7px;
  color:var(--accent);text-decoration:none;white-space:nowrap}
.act:hover{border-color:var(--accent)}
.dayempty{position:absolute;left:30px;top:46%;font:11px/1 var(--mono);color:var(--dim);opacity:.6}

/* --- 3 & 4. the lists --------------------------------------------------- */
.tabs{display:flex;gap:4px;margin-left:auto}
.tabb{font:9.5px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;background:none;
  border:1px solid var(--line);color:var(--dim);border-radius:99px;padding:4px 8px;cursor:pointer;
  text-decoration:none}
.tabb:hover{color:var(--fg)}
.tabb.on{color:var(--accent);border-color:var(--accent);background:var(--paper)}
.tabb .c{opacity:.7;margin-left:3px}
.grp{border-bottom:1px solid var(--line);padding:8px 0}
.grp:last-child{border-bottom:0}
.grp.sel{background:var(--paper);border-radius:8px;padding:8px 9px;margin:0 -9px}
.grph{display:flex;align-items:baseline;gap:8px;margin-bottom:2px}
.grph .nm{font:600 10.5px/1 var(--mono);letter-spacing:.09em;text-transform:uppercase;
  color:hsl(var(--hue) 45% var(--tl))}
.grph .nm.dayhd{color:var(--dim)}
.grph .nm.dayhd.on{color:var(--accent)}
.grph .w{margin-left:auto;font:9.5px/1 var(--mono);color:var(--dim)}
li.row{display:flex;gap:8px;padding:5px 0;align-items:flex-start}
.rowtitle{font-size:13px;line-height:1.35}
.rowmeta{font-size:10px}
.pill{font:9.5px/1.6 var(--mono);letter-spacing:.04em;border-radius:99px;padding:0 6px;flex:none;
  background:hsl(var(--hue) 55% 50% / .15);color:hsl(var(--hue) 45% var(--tl));
  border:1px solid hsl(var(--hue) 55% 50% / .3);white-space:nowrap;margin-top:2px}
.at{color:var(--dim)}
.od{color:var(--warn);font-weight:600}
.empty{font:11.5px/1.7 var(--mono);color:var(--dim);opacity:.65;padding:5px 0}
.carry{border-left:2px solid var(--warn);padding-left:10px;margin:2px 0 10px}
.carry .ch{font:600 10px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;
  color:var(--warn);margin-bottom:3px}
li.hid{display:none}
.grp.expand li.hid{display:flex}
.moretog{font:9.5px/1.6 var(--mono);letter-spacing:.05em;color:var(--accent);background:none;
  border:0;padding:2px 0 0;cursor:pointer;text-align:left}
.moretog:hover{text-decoration:underline}
.grp.expand .moretog .lbl:after{content:'show less'}
.moretog .lbl:after{content:'show all'}
.flag{color:var(--warn);font-size:12.5px;padding:3px 0;line-height:1.45}
.flagbar{margin-top:12px;background:var(--card);border:1px solid var(--line);
  border-left:3px solid var(--warn);border-radius:11px;padding:11px 14px}
.flagbar h2{font:600 10.5px/1 var(--mono);text-transform:uppercase;letter-spacing:.12em;
  color:var(--dim);margin:0 0 7px}
"""

RAIL_JS = """
document.addEventListener('click',e=>{
  const b=e.target.closest('.moretog'); if(!b) return;
  b.closest('.grp').classList.toggle('expand');
});
"""


def _rail_row(it, showat=True, showdue=False, pill=False):
    """One assignment or task. Shared by both list panels so the two read as
    one family: same tick, same title weight, same monospaced metadata."""
    title = plain(it['title'])
    if it.get('url'):
        title = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
    meta = []
    if showdue and it.get('due'):
        meta.append('<span class="%s">%s</span>'
                    % ('od' if it.get('overdue') else '', it['due'][5:]))
    if showat and it.get('at') and it['at'] not in ('', '23:59'):
        meta.append('<span class="at">%s</span>' % esc(it['at']))
    if it.get('points'):
        meta.append('%s pts' % esc(str(it['points'])))
    if it.get('elsewhere'):
        meta.append('on %s' % esc(it['elsewhere']))
    if it.get('next'):
        meta.append('next')
    if it.get('overdue') and not showdue:
        meta.append('<span class="od">late</span>')
    # The day is the section heading in the Canvas panel, so without this the
    # course is nowhere on the row -- and which class a thing belongs to is most
    # of what makes a week legible at a glance.
    tag = ('<span class="pill" %s>%s</span>'
           % (_style('--hue:%d' % hue(it.get('tag'))), esc(it['tag']))
           if pill and it.get('tag') else '')
    return ('<li class="row%s"><button class="tick" data-key="%s"%s>%s</button>'
            '<span class="rowbody"><span class="rowtitle">%s</span>'
            '<span class="rowmeta">%s</span></span>%s</li>'
            % (' crossed' if it.get('done') else '', esc(it['key']),
               ' disabled title="submitted in Canvas"' if it.get('submitted') else '',
               '&#10003;' if it.get('done') else '', title,
               ' &middot; '.join(meta), tag))


def look_rail(day=None):
    """1 -- Rail. Four panels in a row, one height, nothing stacked.

    Read left to right: which day, then that day's hours, then what is due,
    then what he has taken on himself. The week is a column of seven rows down
    the left rather than a strip across the top, so selecting a day is a
    vertical gesture next to the thing it changes.

    The day is vertical again -- he asked for it back -- but it does **not**
    scroll. 06:00 to 23:00 is compressed to fit the panel exactly, so height
    still means duration and the panel is the same size on a nine-meeting
    Tuesday as on an empty Sunday. That is the trade: a tighter scale than a
    scrolling column, in exchange for the whole day being visible at once.

    Everything else that used to sit above and below is gone. Four panels, one
    row, one height.
    """
    day = day or core.TODAY.isoformat()
    today_iso = core.TODAY.isoformat()
    is_today = day == today_iso
    d = datetime.date(*map(int, day.split('-')))
    blocks, nowpct = _blocks(day, is_today)
    wk = week_canvas(day)
    days = daystrip_days(day)

    # --- 1. the week ----------------------------------------------------
    wdays = []
    for i, dd in enumerate(days):
        items = [it for it in wk['days'][dd] if not it['done']]
        nmeet = len(today_split(dd)[1])
        dd_d = datetime.date(*map(int, dd.split('-')))
        bars = ''.join('<i %s></i>' % _style('background:hsl(%d 55%% 50%%)' % hue(it['tag']))
                       for it in items[:4])
        sub = []
        if items:
            sub.append('%d due' % len(items))
        if nmeet:
            sub.append('%d mtg' % nmeet)
        wdays.append('<a class="wd%s%s" href="/look/1?day=%s"><b>%s</b>'
                     '<span class="num">%s</span>'
                     '<span class="sub">%s</span>%s</a>'
                     % (' on' if dd == day else '', ' today' if dd == today_iso else '',
                        dd, DOW[i], dd_d.strftime('%d'),
                        esc(' &middot; '.join(sub)) if sub else '&mdash;',
                        '<span class="bars">%s</span>' % bars if bars else ''))

    # --- 2. the day -----------------------------------------------------
    grid = []
    for m, p in _hours():
        # Every hour ruled, but only every second hour labelled: at this scale
        # seventeen labels is a stack of numbers, not an axis.
        grid.append('<div class="hr%s" %s>%s</div>'
                    % ('' if (m // 60) % 2 == 0 else ' q', _style('top:%.3f%%' % p),
                       '<span>%02d</span>' % (m // 60) if (m // 60) % 2 == 0 else ''))
    if nowpct is not None:
        grid.append('<div class="nowline" %s></div>' % _style('top:%.3f%%' % nowpct))
    for b in blocks:
        e = b['ev']
        # A 30-minute block is about 16px tall here. The clock line does not fit
        # under the title and would only push the title out, so short blocks
        # carry the title alone and say the rest when opened.
        meta = ('<div class="bm">%s</div>' % b['span']) if b['mins'] >= 45 else ''
        grid.append('<div class="blk%s%s" %s><div class="bt">%s</div>%s%s'
                    '<div class="acts">%s</div></div>'
                    % (' past' if b['past'] else '', ' live' if b['live'] else '',
                       _style('--hue:%d' % b['hue'], 'top:%.3f%%' % b['top'],
                              '--h:%.3f%%' % b['h'], 'height:%.3f%%' % b['h'],
                              # Lanes tile the area *after* the hour gutter. Taking
                              # the gutter out of each lane's width instead left a
                              # 30px hole between two overlapping meetings.
                              'left:calc(28px + (100%% - 32px) * %.5f)'
                              % (float(b['lane']) / b['nlanes']),
                              'width:calc((100%% - 32px) * %.5f - 3px)'
                              % (1.0 / b['nlanes'])),
                       esc(e['summary']), meta, _why(b['rec']),
                       _blk_acts(e, b['rec'])))
    if not blocks:
        grid.append('<p class="dayempty">nothing scheduled</p>')

    # --- 3. Canvas ------------------------------------------------------
    def day_sections(pred):
        """Only days that have something. An empty day was carrying a heading
        and a dash purely to keep the week's shape visible -- but the week panel
        on the left already draws that shape, with counts, and drawing it twice
        was most of what made this column feel busy."""
        out = []
        for i, dd in enumerate(days):
            items = [it for it in wk['days'][dd] if pred(it)]
            if not items:
                continue
            dd_d = datetime.date(*map(int, dd.split('-')))
            out.append('<div class="grp%s"><div class="grph"><span class="nm%s">%s %s</span>'
                       '<span class="w">%d</span></div><ul>%s</ul></div>'
                       % (' sel' if dd == day else '',
                          ' dayhd' + (' on' if dd == today_iso else ''),
                          DOW[i], dd_d.strftime('%d'), len(items),
                          ''.join(_rail_row(it, pill=True) for it in items)))
        return ''.join(out)

    allit = [it for v in wk['days'].values() for it in v]
    nopen = sum(1 for it in allit if not it['done'])
    ndone = len(allit) - nopen
    carry = ''
    if wk['carried']:
        carry = ('<div class="carry"><div class="ch">Carried in &middot; %d</div><ul>%s</ul></div>'
                 % (len(wk['carried']),
                    ''.join(_rail_row(it, showdue=True, pill=True) for it in wk['carried'])))

    # --- 4. projects ----------------------------------------------------
    def project_groups(rows, by_score):
        """Projects, three todos each, ordered by the project's *best* item --
        the project holding the most pressing thing belongs at the top even if
        it holds only that one, and volume should not outrank urgency. Done work
        has no priority, so that pane falls back to count."""
        byproj = {}
        for t in rows:
            byproj.setdefault(t['tag'] or 'unfiled', []).append(t)
        if by_score:
            order = sorted(byproj, key=lambda k: (-max(t['score'] for t in byproj[k]), k))
        else:
            order = sorted(byproj, key=lambda k: (-len(byproj[k]), k))
        out = []
        for tag in order:
            items = sorted(byproj[tag], key=lambda t: (-t['score'], t['due'] or '9999',
                                                       t['title']))
            shown = ''.join(
                _rail_row(t, showat=False, showdue=True).replace(
                    '<li class="row', '<li class="row hid', 1) if n >= 3
                else _rail_row(t, showat=False, showdue=True)
                for n, t in enumerate(items))
            extra = len(items) - 3
            out.append('<div class="grp"><div class="grph"><span class="nm" %s>%s</span>'
                       '<span class="w">%s%d</span></div><ul>%s</ul>%s</div>'
                       % (_style('--hue:%d' % hue(tag)), esc(tag),
                          'top 3 of ' if extra > 0 else '', len(items), shown,
                          '<button class="moretog"><span class="lbl"></span> '
                          '&middot; %d more</button>' % extra if extra > 0 else ''))
        return ''.join(out)

    tasks = task_rows()
    donetasks = done_task_rows()
    fl = flags()
    label = ('Today' if is_today else
             'Tomorrow' if d == core.TODAY + datetime.timedelta(days=1) else
             d.strftime('%A'))

    body = ('<div class="wrap">%s'
            '<div class="head"><h1>%s</h1><span class="meta">%s</span>%s</div>'
            '<div class="cols">'

            '<div class="panel"><div class="ph">week</div>'
            '<div class="wk">%s</div></div>'

            '<div class="panel day"><div class="ph">%s<span class="n">%d</span></div>'
            '<div class="pb fit"><div class="day">%s</div></div></div>'

            '<div class="panel" data-tabs><div class="ph">canvas'
            '<span class="tabs"><button class="tabb on" data-tab="open">open'
            '<span class="c">%d</span></button>'
            '<button class="tabb" data-tab="done">done<span class="c">%d</span></button>'
            '</span></div>'
            '<div class="pb" data-pane="open">%s%s</div>'
            '<div class="pb" data-pane="done" hidden>%s</div></div>'

            '<div class="panel tasks" data-tabs><div class="ph">projects'
            '<span class="tabs"><button class="tabb on" data-tab="open">open'
            '<span class="c">%d</span></button>'
            '<button class="tabb" data-tab="done">done<span class="c">%d</span></button>'
            '<a class="tabb" href="/tasks" target="_blank" rel="noopener">all</a>'
            '</span></div>'
            '<div class="pb" data-pane="open">%s</div>'
            '<div class="pb" data-pane="done" hidden>%s</div></div>'

            '</div>%s</div>'
            % (looknav(1), esc(d.strftime('%A %d %B')), esc(label),
               '' if is_today else '<a class="back" href="/look/1">back to today &rarr;</a>',
               ''.join(wdays), esc(label), len(blocks), ''.join(grid),
               nopen, ndone,
               carry, day_sections(lambda it: not it['done'])
               or '<p class="empty">Nothing due this week.</p>',
               day_sections(lambda it: it['done'])
               or '<p class="empty">Nothing handed in this week yet.</p>',
               len(tasks), len(donetasks),
               project_groups(tasks, True) or '<p class="empty">No open tasks.</p>',
               project_groups(donetasks, False) or '<p class="empty">Nothing ticked off yet.</p>',
               ('<div class="flagbar"><h2>Flags &middot; %d</h2>%s</div>'
                % (len(fl), ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)))
               if fl else ''))
    return _page('Look 1 - Rail', RAIL_CSS, body, RAIL_JS)


LOOKS[1] = ('Rail', 'four panels, one row, one height', look_rail)


# ================================================================ 2. Horizon

HORIZON_CSS = """
:root{--bg:#0b0d10;--fg:#e8ecf2;--dim:#7c8798;--line:#1d2027;--card:#14171c;
  --accent:#22d3ee;--accent2:#a78bfa;--warn:#fbbf24;--hot:#fb7185}
body{background:var(--bg);color:var(--fg);
  background-image:linear-gradient(180deg,rgba(34,211,238,.06),transparent 320px)}
.wrap{max-width:1400px;margin:0 auto;padding:16px 22px 48px}
.head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin:14px 0 12px}
h1{font:600 19px/1.2 var(--mono);margin:0;letter-spacing:-.01em}
.head .meta{font:11px/1 var(--mono);color:var(--dim);letter-spacing:.08em;text-transform:uppercase}
.head .clock{margin-left:auto;font:600 15px/1 var(--mono);color:var(--accent)}
/* The day runs left to right. A calendar is a picture of time, and time is a
   line -- put it along the axis a screen actually has room on, and the column
   height problem stops existing because there is no column. */
.band{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:0;overflow:hidden;margin-bottom:14px}
.bandhead{display:flex;align-items:center;gap:10px;padding:10px 14px;
  border-bottom:1px solid var(--line);font:10.5px/1 var(--mono);letter-spacing:.11em;
  text-transform:uppercase;color:var(--dim)}
.bandhead .n{margin-left:auto;color:var(--accent)}
.track{position:relative;height:158px;overflow-x:auto;overflow-y:hidden}
.rail{position:relative;height:100%;min-width:1180px}
.vr{position:absolute;top:0;bottom:0;border-left:1px solid var(--line)}
.vr span{position:absolute;top:4px;left:4px;font:10px/1 var(--mono);color:var(--dim)}
.nowline{position:absolute;top:0;bottom:0;border-left:2px solid var(--hot);z-index:6;
  box-shadow:0 0 14px var(--hot)}
.nowline:after{content:'';position:absolute;top:-1px;left:-4px;width:7px;height:7px;
  border-radius:50%;background:var(--hot)}
.blk{position:absolute;border-radius:8px;padding:5px 8px;overflow:hidden;cursor:pointer;z-index:2;
  background:hsl(var(--hue) 60% 52% / .2);border:1px solid hsl(var(--hue) 65% 62% / .45);
  border-top:3px solid hsl(var(--hue) 70% 62%);display:flex;flex-direction:column;min-width:0}
.blk.live{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 0 20px rgba(34,211,238,.2)}
.blk.past{opacity:.34}
.blk.open{z-index:9;height:auto!important;min-height:var(--h);overflow:visible;
  background:var(--card);box-shadow:0 14px 34px rgba(0,0,0,.6)}
.bt{font:600 12px/1.2 var(--sans);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:none}
.bm{font:10px/1.4 var(--mono);color:var(--dim);flex:none}
.why{font:11px/1.35 var(--sans);color:var(--dim);margin-top:3px;flex:1;min-height:0;overflow:hidden;
  -webkit-mask-image:linear-gradient(#000 calc(100% - 9px),transparent);
  mask-image:linear-gradient(#000 calc(100% - 9px),transparent)}
.blk.open .why{flex:none;overflow:visible;-webkit-mask-image:none;mask-image:none}
.why p{margin:0 0 4px}
.acts{display:none;gap:5px;flex-wrap:wrap;margin-top:5px}
.blk.open .acts{display:flex}
.act{font:10px/1.5 var(--mono);border:1px solid var(--line);border-radius:5px;padding:2px 7px;
  color:var(--accent);text-decoration:none;white-space:nowrap}
.cols{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px}
@media(max-width:1060px){.cols{grid-template-columns:repeat(2,1fr)}}
/* Seven fixed-height boards. Clicking the header selects the day above. */
.dcol{background:var(--card);border:1px solid var(--line);border-radius:12px;
  display:flex;flex-direction:column;height:300px;overflow:hidden}
.dcol.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.dcol.past{opacity:.48}
.dh{padding:9px 11px 8px;border-bottom:1px solid var(--line);display:flex;align-items:baseline;
  gap:6px;text-decoration:none;font:10.5px/1 var(--mono);letter-spacing:.09em;
  text-transform:uppercase;color:var(--dim)}
.dcol.on .dh,.dcol.today .dh{color:var(--accent)}
.dh .num{margin-left:auto;font-size:16px;font-weight:600;letter-spacing:0}
.db{flex:1;overflow-y:auto;padding:5px 11px 9px}
li.row{display:flex;gap:8px;padding:7px 0;border-bottom:1px solid var(--line);align-items:flex-start}
li.row:last-child{border-bottom:0}
.rowtitle{font-size:12.5px}
.tt{font:10px/1.6 var(--mono);padding:0 6px;border-radius:99px;white-space:nowrap;
  background:hsl(var(--hue) 55% 55% / .18);color:hsl(var(--hue) 70% 74%)}
.tray{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:14px}
@media(max-width:900px){.tray{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.panel h2{font:600 10.5px/1 var(--mono);text-transform:uppercase;letter-spacing:.11em;
  color:var(--dim);margin:0 0 9px;display:flex;gap:8px}
.panel h2 .n{margin-left:auto;color:var(--accent)}
.empty{font:12px/1.6 var(--mono);color:var(--dim);opacity:.55;padding:6px 0}
.flag{color:var(--warn);font:12.5px/1.6 var(--mono);padding:3px 0}
.od{color:var(--hot)}
"""


def look_horizon(day=None):
    """2 -- Horizon. The day is a horizontal band; the week is a board.

    A calendar draws time as a line, and a browser window has far more room
    along its horizontal axis than its vertical one. Turning the day sideways
    makes the height question disappear rather than answering it -- the band is
    158px whatever is booked, and a long day scrolls sideways instead of
    pushing the page down. Duration is still literal, as width.

    Below it, seven fixed-height day boards carry the Canvas work, each header
    a link that re-aims the band above. Canvas and `Tasks/` stay in separate
    panels in the tray.
    """
    day = day or core.TODAY.isoformat()
    today_iso = core.TODAY.isoformat()
    is_today = day == today_iso
    d = datetime.date(*map(int, day.split('-')))
    blocks, nowpct = _blocks(day, is_today)
    wk = week_canvas(day)

    rail = ''.join('<div class="vr" %s><span>%02d</span></div>'
                   % (_style('left:%.3f%%' % p), m // 60) for m, p in _hours())
    if nowpct is not None:
        rail += '<div class="nowline" %s></div>' % _style('left:%.3f%%' % nowpct)
    for b in blocks:
        e = b['ev']
        rail += ('<div class="blk%s%s" %s>'
                 '<div class="bt">%s</div><div class="bm">%s</div>%s'
                 '<div class="acts">%s</div></div>'
                 % (' past' if b['past'] else '', ' live' if b['live'] else '',
                    _style('--hue:%d' % b['hue'], 'left:%.3f%%' % b['top'],
                           'width:%.3f%%' % b['h'],
                           'top:calc(22px + %.3f%%)' % (b['lane'] * 74.0 / b['nlanes']),
                           '--h:%.1fpx' % (128.0 / b['nlanes']),
                           'height:%.1fpx' % (128.0 / b['nlanes'])),
                    esc(e['summary']), b['span'], _why(b['rec']),
                    _blk_acts(e, b['rec'])))
    if not blocks:
        rail += ('<p class="empty" %s>Nothing scheduled.</p>'
                 % _style('position:absolute', 'left:16px', 'top:62px'))

    def crow(it):
        title = plain(it['title'])
        if it['url']:
            title = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
        meta = []
        if it.get('at') and it['at'] not in ('', '23:59'):
            meta.append(esc(it['at']))
        if it.get('points'):
            meta.append('%spts' % esc(str(it['points'])))
        if it.get('elsewhere'):
            meta.append('on %s' % esc(it['elsewhere']))
        return ('<li class="row%s"><button class="tick" data-key="%s"%s>%s</button>'
                '<span class="rowbody"><span class="rowtitle">%s</span>'
                '<span class="rowmeta">%s</span></span>'
                '<span class="tt" %s>%s</span></li>'
                % (' crossed' if it['done'] else '', esc(it['key']),
                   ' disabled' if it.get('submitted') else '',
                   '&#10003;' if it['done'] else '', title, ' &middot; '.join(meta),
                   _style('--hue:%d' % hue(it['tag'])), esc(it['tag'])))

    cols = []
    for i, dd in enumerate(daystrip_days(day)):
        items = wk['days'][dd]
        openn = sum(1 for it in items if not it['done'])
        cols.append('<div class="dcol%s%s%s">'
                    '<a class="dh" href="/look/2?day=%s">%s<span class="num">%s</span>'
                    '%s</a><div class="db"><ul>%s</ul></div></div>'
                    % (' on' if dd == day else '', ' today' if dd == today_iso else '',
                       ' past' if dd < today_iso else '', dd, DOW[i], dd[8:10],
                       '', ''.join(crow(it) for it in items)
                       or '<li class="empty">&mdash;</li>'))

    tasks = task_rows()
    trows = ''.join(
        '<li class="row%s"><button class="tick" data-key="%s">%s</button>'
        '<span class="rowbody"><span class="rowtitle">%s</span>'
        '<span class="rowmeta">%s</span></span></li>'
        % (' crossed' if t['done'] else '', esc(t['key']),
           '&#10003;' if t['done'] else '', plain(t['title']),
           ' &middot; '.join(x for x in [
               esc(t['tag']),
               ('<span class="od">%s</span>' % t['due'][5:]) if t['overdue']
               else (t['due'][5:] if t['due'] else '')] if x))
        for t in tasks[:9])

    fl = flags()
    carry = ''.join(crow(it) for it in wk['carried'])
    body = ('<div class="wrap">%s<div class="head"><h1>%s</h1>'
            '<span class="meta">%s</span><span class="clock">%s</span></div>'
            '<div class="band"><div class="bandhead">schedule &middot; %s'
            '<span class="n">%d</span></div>'
            '<div class="track" data-scrollnow-x><div class="rail">%s</div></div></div>'
            '<div class="cols">%s</div>'
            '<div class="tray">'
            '<div class="panel"><h2>Tasks <span class="n">%d</span></h2><ul>%s</ul></div>'
            '<div class="panel"><h2>Carried in <span class="n">%d</span></h2><ul>%s</ul></div>'
            '<div class="panel"><h2>Flags <span class="n">%d</span></h2>%s</div>'
            '</div></div>'
            % (looknav(2), esc(d.strftime('%A %d %B')),
               'today' if is_today else 'selected',
               esc(datetime.datetime.now().strftime('%H:%M')),
               esc(d.strftime('%a %d').lower()), len(blocks), rail, ''.join(cols),
               len(tasks), trows or '<li class="empty">No open tasks.</li>',
               len(wk['carried']), carry or '<li class="empty">Nothing late.</li>',
               len(fl), ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)
               or '<p class="empty">Clean.</p>'))
    return _page('Look 2 - Horizon', HORIZON_CSS, body)


LOOKS[2] = ('Horizon', 'the day turned sideways; seven boards under it', look_horizon)


# ================================================================ 4. Ledger

LEDGER_CSS = """
:root{--bg:#fbfaf7;--fg:#1a1916;--dim:#726c62;--line:#e6e1d8;--card:#fff;
  --accent:#1f5f4f;--warn:#a3521c;--paper:#f3f0e9}
@media(prefers-color-scheme:dark){:root{--bg:#121311;--fg:#eceae4;--dim:#8f8a80;
  --line:#272825;--card:#191a18;--accent:#6fcfae;--warn:#dd9455;--paper:#1f201d}}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:1180px;margin:0 auto;padding:16px 24px 52px}
.head{margin:16px 0 20px;display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap}
h1{font:400 34px/1.05 Iowan Old Style,"Palatino Linotype",Palatino,Georgia,serif;
  margin:0;letter-spacing:-.01em}
.head .meta{font:10.5px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;
  color:var(--dim);padding-bottom:5px}
.head .meta a{color:var(--accent);text-decoration:none;margin-left:10px}
.rule{border-top:2px solid var(--fg);margin:0 0 2px}
.rule2{border-top:1px solid var(--line);margin:0 0 18px;padding-top:2px}
.cols{display:grid;grid-template-columns:132px minmax(0,1fr) minmax(0,300px);gap:22px;
  align-items:start}
@media(max-width:1000px){.cols{grid-template-columns:1fr}}
/* A slim gutter, not a card: the hours are a margin note running beside the
   day, the way a ledger rules its left edge. Fixed height, scrolls on its own. */
.gutter{position:sticky;top:14px}
.gh{font:600 10px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dim);
  margin-bottom:8px}
.daycol{height:520px;overflow-y:auto;position:relative;border-left:1px solid var(--line);
  padding-left:2px}
.daybody{position:relative;height:1120px}
.hr{position:absolute;left:0;right:0;border-top:1px dotted var(--line)}
.hr span{position:absolute;top:-7px;left:2px;font:9.5px/1 var(--mono);color:var(--dim);
  background:var(--bg);padding-right:4px}
.nowline{position:absolute;left:0;right:0;border-top:1px solid var(--warn);z-index:5}
.nowline:after{content:'';position:absolute;left:-3px;top:-3px;width:5px;height:5px;
  border-radius:50%;background:var(--warn)}
.blk{position:absolute;left:30px;right:0;border-radius:3px;padding:2px 6px;overflow:hidden;
  cursor:pointer;z-index:2;background:hsl(var(--hue) 45% 55% / .16);
  border-left:2px solid hsl(var(--hue) 48% 45%);font:11px/1.25 var(--sans)}
.blk.past{opacity:.4}
.blk.live{box-shadow:0 0 0 1px var(--warn)}
.blk.open{height:auto!important;z-index:9;background:var(--card);right:-180px;
  box-shadow:0 10px 30px rgba(0,0,0,.2);border:1px solid var(--line);
  border-left:2px solid hsl(var(--hue) 48% 45%);padding:6px 9px}
.bt{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.blk.open .bt{white-space:normal}
.bm{font:9.5px/1.4 var(--mono);color:var(--dim)}
.why{display:none;font:11.5px/1.45 var(--sans);color:var(--dim);margin-top:5px}
.blk.open .why{display:block}
.why p{margin:0 0 4px}
.acts{display:none;gap:5px;flex-wrap:wrap;margin-top:6px}
.blk.open .acts{display:flex}
.act{font:10px/1.5 var(--mono);border:1px solid var(--line);border-radius:4px;padding:2px 6px;
  color:var(--accent);text-decoration:none}
/* The centre column is the ledger: work grouped by the course it belongs to,
   not by the day it is due. Which subject is eating the week is a question
   neither a ranked list nor a seven-column grid can answer. */
.sec{margin:0 0 26px}
.sech{display:flex;align-items:baseline;gap:9px;border-bottom:1px solid var(--fg);
  padding-bottom:5px;margin-bottom:2px}
.sech h2{font:400 19px/1.2 Iowan Old Style,Palatino,Georgia,serif;margin:0}
.sech .c{margin-left:auto;font:10.5px/1 var(--mono);letter-spacing:.1em;color:var(--dim)}
.grp{border-bottom:1px solid var(--line);padding:9px 0}
.grp:last-child{border-bottom:0}
.grph{display:flex;align-items:baseline;gap:8px;margin-bottom:3px}
.grph .nm{font:600 11px/1 var(--mono);letter-spacing:.09em;text-transform:uppercase;
  color:hsl(var(--hue) 45% 35%)}
@media(prefers-color-scheme:dark){.grph .nm{color:hsl(var(--hue) 60% 68%)}}
.grph .w{margin-left:auto;font:10px/1 var(--mono);color:var(--dim)}
li.row{display:flex;gap:9px;padding:5px 0;align-items:flex-start}
.rowtitle{font-size:13.5px;line-height:1.35}
.rowmeta{font-size:10.5px}
.day7{display:flex;gap:4px;margin:0 0 16px}
.d7{flex:1;text-decoration:none;border:1px solid var(--line);border-radius:6px;padding:6px 4px;
  text-align:center;background:var(--card)}
.d7 .w{font:9.5px/1 var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--dim)}
.d7 .n{font:600 16px/1.3 var(--mono);display:block}
.d7 .c{font:9.5px/1 var(--mono);color:var(--dim)}
.d7.on{border-color:var(--accent);background:var(--paper)}
.d7.on .n,.d7.on .w{color:var(--accent)}
.d7.today .n{text-decoration:underline;text-underline-offset:3px}
aside .box{background:var(--paper);border-radius:8px;padding:12px 14px;margin-bottom:12px}
aside h3{font:600 10px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;
  color:var(--dim);margin:0 0 9px}
.empty{font:11.5px/1.7 var(--mono);color:var(--dim);opacity:.65;padding:4px 0}
.flag{color:var(--warn);font-size:12.5px;padding:3px 0;line-height:1.45}
.od{color:var(--warn);font-weight:600}
.tot{display:flex;gap:16px;font:10.5px/1 var(--mono);color:var(--dim);letter-spacing:.06em;
  text-transform:uppercase;margin-top:6px}
.tot b{display:block;font:600 19px/1.3 var(--mono);color:var(--fg);letter-spacing:0}
"""


def look_ledger(day=None):
    """4 -- Ledger. Work grouped by course, not by day.

    Every other view here sorts by time, which answers *when* and never *what is
    eating the week*. This one groups outstanding Canvas work under the course
    it belongs to, so five CSE 434 items read as one problem rather than five
    rows scattered across a grid. `Tasks/` is its own section below, grouped by
    project on the same principle.

    The schedule becomes a margin rule -- a slim 520px gutter down the left,
    hours dotted, blocks opening sideways into the text column when clicked.
    Serif headings and a paper ground, because this one is meant to be read
    rather than scanned.
    """
    day = day or core.TODAY.isoformat()
    today_iso = core.TODAY.isoformat()
    is_today = day == today_iso
    d = datetime.date(*map(int, day.split('-')))
    blocks, nowpct = _blocks(day, is_today)
    wk = week_canvas(day)

    grid = ''.join('<div class="hr" %s><span>%02d</span></div>'
                   % (_style('top:%.3f%%' % p), m // 60) for m, p in _hours())
    if nowpct is not None:
        grid += '<div class="nowline" %s></div>' % _style('top:%.3f%%' % nowpct)
    for b in blocks:
        e = b['ev']
        grid += ('<div class="blk%s%s" %s><div class="bt">%s</div>'
                 '<div class="bm">%s</div>%s<div class="acts">%s</div></div>'
                 % (' past' if b['past'] else '', ' live' if b['live'] else '',
                    _style('--hue:%d' % b['hue'], 'top:%.3f%%' % b['top'],
                           'height:%.3f%%' % b['h']),
                    esc(e['summary']), b['from'], _why(b['rec']),
                    _blk_acts(e, b['rec'])))
    if not blocks:
        grid += '<p class="empty" %s>clear</p>' % _style('position:absolute', 'top:44%',
                                                         'left:34px')

    d7 = ''
    for i, dd in enumerate(daystrip_days(day)):
        n = sum(1 for it in wk['days'][dd] if not it['done'])
        d7 += ('<a class="d7%s%s" href="/look/4?day=%s"><span class="w">%s</span>'
               '<b class="n">%s</b><span class="c">%s</span></a>'
               % (' on' if dd == day else '', ' today' if dd == today_iso else '',
                  dd, DOW[i], dd[8:10], ('%d due' % n) if n else '&mdash;'))

    def row(it, showday=False):
        title = plain(it['title'])
        if it.get('url'):
            title = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
        meta = []
        if showday and it.get('due'):
            meta.append('<span class="%s">%s</span>'
                        % ('od' if it.get('overdue') else '', it['due'][5:]))
        if it.get('at') and it['at'] not in ('', '23:59'):
            meta.append(esc(it['at']))
        if it.get('points'):
            meta.append('%s pts' % esc(str(it['points'])))
        if it.get('elsewhere'):
            meta.append('on %s' % esc(it['elsewhere']))
        return ('<li class="row%s"><button class="tick" data-key="%s"%s>%s</button>'
                '<span class="rowbody"><span class="rowtitle">%s</span>'
                '<span class="rowmeta">%s</span></span></li>'
                % (' crossed' if it['done'] else '', esc(it['key']),
                   ' disabled' if it.get('submitted') else '',
                   '&#10003;' if it['done'] else '', title, ' &middot; '.join(meta)))

    # Canvas, grouped by course. Only what is still open -- the ledger is about
    # load, and a submitted item is not load.
    bycourse = {}
    for dd in sorted(wk['days']):
        for it in wk['days'][dd]:
            if not it['done']:
                bycourse.setdefault(it['tag'], []).append(it)
    for it in wk['carried']:
        bycourse.setdefault(it['tag'], []).insert(0, it)
    groups = ''
    for tag in sorted(bycourse, key=lambda t: (-len(bycourse[t]), t)):
        items = bycourse[tag]
        pts = sum(i['points'] or 0 for i in items)
        groups += ('<div class="grp"><div class="grph"><span class="nm" %s>%s</span>'
                   '<span class="w">%d item%s%s</span></div><ul>%s</ul></div>'
                   % (_style('--hue:%d' % hue(tag)), esc(tag or 'unfiled'),
                      len(items), '' if len(items) == 1 else 's',
                      ' &middot; %g pts' % pts if pts else '',
                      ''.join(row(i, showday=True) for i in items)))

    tasks = task_rows()
    byproj = {}
    for t in tasks:
        byproj.setdefault(t['tag'] or 'unfiled', []).append(t)
    tgroups = ''
    for tag in sorted(byproj, key=lambda t: (-len(byproj[t]), t)):
        tgroups += ('<div class="grp"><div class="grph"><span class="nm" %s>%s</span>'
                    '<span class="w">%d</span></div><ul>%s</ul></div>'
                    % (_style('--hue:%d' % hue(tag)), esc(tag),
                       len(byproj[tag]), ''.join(row(t, showday=True) for t in byproj[tag])))

    fl = flags()
    nopen = sum(len(v) for v in bycourse.values())
    body = ('<div class="wrap">%s<div class="head"><h1>%s</h1>'
            '<span class="meta">week of %s</span></div>'
            '<div class="rule"></div><div class="rule2"></div>'
            '<div class="cols">'
            '<div class="gutter"><div class="gh">%s</div>'
            '<div class="daycol" data-scrollnow><div class="daybody">%s</div></div>'
            '<div class="tot"><span><b>%d</b>meetings</span></div></div>'
            '<div><div class="day7">%s</div>'
            '<div class="sec"><div class="sech"><h2>Coursework</h2>'
            '<span class="c">%d open &middot; by course</span></div>%s</div>'
            '<div class="sec"><div class="sech"><h2>Tasks</h2>'
            '<span class="c">%d open &middot; by project</span></div>%s</div></div>'
            '<aside><div class="box"><h3>Due %s</h3><ul>%s</ul></div>'
            '<div class="box"><h3>Flags</h3>%s</div></aside>'
            '</div></div>'
            % (looknav(4), esc(d.strftime('%B %Y')), esc(wk['monday'][5:]),
               esc(d.strftime('%a %d').lower()), grid, len(blocks), d7,
               nopen, groups or '<p class="empty">Nothing outstanding this week.</p>',
               len(tasks), tgroups or '<p class="empty">No open tasks.</p>',
               esc(d.strftime('%a %d')),
               ''.join(row(i) for i in canvas_on(day))
               or '<li class="empty">nothing</li>',
               ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)
               or '<p class="empty">clean</p>'))
    return _page('Look 4 - Ledger', LEDGER_CSS, body)


LOOKS[4] = ('Ledger', 'grouped by course, not by day; schedule as a margin rule', look_ledger)


# ---------------------------------------------------------------- index

def index():
    def gist(n):
        doc = (LOOKS[n][2].__doc__ or '').split('\n\n')
        return ' '.join(doc[1].split()) if len(doc) > 1 else ''
    cards = ''.join(
        '<a class="lk" href="/look/%d"><b>%d &middot; %s</b><span>%s</span><em>%s</em></a>'
        % (n, n, esc(LOOKS[n][0]), esc(LOOKS[n][1]), esc(gist(n)))
        for n in sorted(LOOKS))
    css = """
:root{--bg:#f7f6f3;--fg:#191817;--dim:#6f6a62;--line:#e4e0d8;--card:#fff;--accent:#8c1d40}
@media(prefers-color-scheme:dark){:root{--bg:#131211;--fg:#eeeae3;--dim:#948d83;
  --line:#2b2825;--card:#1b1a18;--accent:#f0839f}}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:780px;margin:0 auto;padding:30px 22px 60px}
h1{font-size:24px;margin:0 0 6px}
p.lede{color:var(--dim);margin:0 0 10px;line-height:1.6}
ul.fixes{color:var(--dim);margin:0 0 22px;padding:0;font-size:13.5px;line-height:1.75}
ul.fixes li{padding-left:16px;position:relative}
ul.fixes li:before{content:'\\2013';position:absolute;left:0}
.lk{display:block;background:var(--card);border:1px solid var(--line);border-radius:13px;
  padding:14px 17px;margin-bottom:11px;text-decoration:none}
.lk:hover{border-color:var(--accent)}
.lk b{display:block;font-size:16px;margin-bottom:2px}
.lk span{display:block;color:var(--accent);font:12px/1.5 var(--mono);margin-bottom:6px}
.lk em{display:block;color:var(--dim);font-size:13px;font-style:normal;line-height:1.55}
"""
    return _page('Dashboard contenders', css,
                 '<div class="wrap"><h1>Five dashboards, round two</h1>'
                 '<p class="lede">All five carry the fixes:</p><ul class="fixes">'
                 '<li>blocks sit at their real clock position &mdash; the duplicate '
                 '<code>style</code> attribute that pinned everything to 06:00 is gone</li>'
                 '<li>the day column never sets the page height</li>'
                 '<li>Canvas work has its own section, separate from <code>Tasks/</code></li>'
                 '<li>clicking a day moves the schedule <em>and</em> the due list</li>'
                 '<li>monospaced furniture throughout</li></ul>%s'
                 '<p class="lede">The live dashboard is untouched &mdash; '
                 '<a href="/">go back</a>.</p></div>' % cards)
