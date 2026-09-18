"""The dashboard's front page.

One row, four panels, one height: which day, that day's hours, what is due,
what he has taken on himself. Read left to right.

Chosen out of a run of contenders that lived at `/look/N` through September
2026; the ones that lost are gone from the tree, and the reasoning that
survived is in the comments here. The page it replaced is still served, at
`/old` -- it is the only place the **Claude terminal** and the **queue** are,
and neither has an equivalent here yet.

Two rules this page exists to keep:

- **Nothing's height depends on what is in it.** The day is 06:00 to 23:00
  compressed to fit its panel exactly, so it never scrolls and never resizes;
  block height still means duration, at a tighter scale than a scrolling
  column could afford. All four panels share one height, set once as `--ph`.
- **Nothing appears twice.** Canvas work is in the Canvas panel and nowhere
  else; the week column carries counts, never titles.

Every positioned element writes **one** `style` attribute, via `_style()`. Two
`style=` attributes on a tag is not an error -- the browser keeps the first and
silently drops the rest, which once left every block stacked at 06:00 with the
arithmetic behind it perfectly correct.
"""
from .base import *
from .base import box, core, canvas, events, metrics, usage
from .feed import feed_load, feed_rows, note_rows
from .js import TICKJS
from .data import (canvas_items, class_notes, flags, monday_of, open_tasks,
                   priority, ranked, today_split, week_canvas)
from .render import _gcal_link, _join_link, _lanes, esc


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
    laned, _daywide = _lanes(timed)
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
            # `_lanes` sets this per overlap cluster, so a block that collides
            # with nothing stays full width however busy the rest of the day is.
            'lane': b['lane'], 'nlanes': b['nlanes'], 'hue': hue(e['label']),
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
document.addEventListener('click',e=>{
  const b=e.target.closest('.blk'); if(!b||e.target.closest('a')) return;
  b.classList.toggle('open');
});
// A row opens on a click anywhere in it except the two things that already mean
// something: the tick box crosses it off, a link opens the assignment.
document.addEventListener('click',e=>{
  const r=e.target.closest('li.row.has');
  if(!r||e.target.closest('a')||e.target.closest('.tick')) return;
  r.classList.toggle('open');
});
document.querySelectorAll('[data-tabs]').forEach(w=>{
  w.addEventListener('click',e=>{
    const t=e.target.closest('[data-tab]'); if(!t) return;
    w.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('on',x===t));
    w.querySelectorAll('[data-pane]').forEach(p=>p.hidden=p.dataset.pane!==t.dataset.tab);
  });
});
"""

# The furniture -- labels, clocks, counts, metadata -- is monospaced; only
# titles and prose are not.
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
.rowbody{display:flex;flex-direction:column;gap:3px;min-width:0;flex:1}
/* The title carries the row. It was the same size as the metadata under it,
   which is what made a list of forty read as a wall of text rather than as
   forty things. */
.rowtitle{font:500 15.5px/1.32 var(--sans);letter-spacing:-.005em;overflow-wrap:anywhere}
/* Closed by default; the whole of it on click. */
.rowdesc{display:none;font:12.5px/1.5 var(--sans);opacity:.68;overflow-wrap:anywhere;
  padding:1px 0 2px}
.row.open .rowdesc{display:block}
/* The caret is the only thing that says a row has more in it, so it is on the
   title line where the eye already is -- and it only exists on rows that do. */
.row.has{cursor:pointer}
.row.has .rowtitle:after{content:'\203a';display:inline-block;margin-left:6px;
  font:400 14px/1 var(--mono);opacity:.35;transform:translateY(-1px)}
.row.has:hover .rowtitle:after{opacity:.7}
.row.has.open .rowtitle:after{transform:translateY(-1px) rotate(90deg)}
.rowmeta{font:10.5px/1.45 var(--mono);opacity:.62;letter-spacing:.03em}
.nav{display:flex;gap:6px;flex-wrap:wrap;align-items:center;
  font:11px/1 var(--mono);padding:10px 0 0;letter-spacing:.05em}
.nav a{text-decoration:none;opacity:.55;padding:4px 9px;border-radius:99px;
  border:1px solid currentColor}
.nav a:hover{opacity:1}
.nav .gap{margin-left:auto}
[hidden]{display:none!important}
"""


# The terminal and the queue live only on the old page. Until they have a home
# here that link is not a nicety, it is the route to half of what Zipper does.
NAV = ('<nav class="nav"><a href="/old">terminal &middot; queue</a>'
       '<a href="/tasks">tasks</a><a href="/canvas">canvas</a>'
       '<a class="gap" href="/views/now">views</a></nav>')


def daystrip_days(day):
    mon = monday_of(day)
    return [(mon + datetime.timedelta(days=i)).isoformat() for i in range(7)]


# One row, four panels, one height. The height is a variable rather than a
# number in four places -- "make them all the same height" is a rule, and a
# rule stated once cannot drift.
CSS = """
:root{--bg:#fbfaf7;--fg:#1a1916;--dim:#726c62;--line:#e6e1d8;--card:#fff;
  --accent:#1f5f4f;--warn:#a3521c;--paper:#f3f0e9;--tl:35%;--ph:620px;--ph2:298px}
@media(prefers-color-scheme:dark){:root{--bg:#121311;--fg:#eceae4;--dim:#8f8a80;
  --line:#272825;--card:#191a18;--accent:#6fcfae;--warn:#dd9455;--paper:#1f201d;--tl:68%}}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:1380px;margin:0 auto;padding:16px 22px 46px}
.head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:14px 0 14px}
h1{font:600 25px/1.15 var(--sans);margin:0;letter-spacing:-.02em}
.head .meta{font:10.5px/1 var(--mono);color:var(--dim);letter-spacing:.11em;text-transform:uppercase}
.head .back{margin-left:auto;font:11px/1 var(--mono);color:var(--accent);text-decoration:none}

.cols{display:grid;grid-template-columns:92px 216px minmax(0,1fr) minmax(0,1fr);gap:12px;
  align-items:start}
@media(max-width:1180px){.cols{grid-template-columns:92px 216px minmax(0,1fr)}
  .panel.tasks{grid-column:1/-1}}
@media(max-width:820px){.cols{grid-template-columns:1fr}.panel{height:auto!important;max-height:var(--ph)}
  .panel.day{height:var(--ph)!important}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:13px;
  height:var(--ph);display:flex;flex-direction:column;overflow:hidden}
.ph{padding:13px 14px 11px;display:flex;align-items:center;gap:8px;flex:none;
  font:600 12.5px/1 var(--mono);text-transform:uppercase;letter-spacing:.1em;color:var(--fg);
  border-bottom:1px solid var(--line)}
.ph .n{margin-left:auto;color:var(--accent)}
/* The lists scroll inside their panel; the day never does. */
.pb{flex:1;min-height:0;overflow-y:auto;padding:4px 14px 12px}
.pb.fit{overflow:hidden;padding:8px 10px 10px}

/* --- 1. the week, down the left --------------------------------------- */
.wk{display:flex;flex-direction:column;height:100%;padding:6px}
.wd{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;
  text-decoration:none;border-radius:9px;padding:5px 7px;border:1px solid transparent}
.wd:hover{background:var(--paper)}
.wd.on{border-color:var(--accent);background:var(--paper)}
.wd b{font:600 10px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase;color:var(--dim)}
.wd.on b,.wd.today b{color:var(--accent)}
.wd .num{font:600 18px/1.25 var(--mono)}
.wd .sub{font:9.5px/1.45 var(--mono);color:var(--dim);display:flex;
  flex-direction:column;align-items:flex-start}

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
.grph .nm{font:600 12px/1.2 var(--mono);letter-spacing:.07em;text-transform:uppercase;
  color:hsl(var(--hue) 45% var(--tl))}
.grph .nm.warn{color:var(--warn)}
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
.grp.flags{margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--line)}
.ph .warn{color:var(--warn)}
.flag{color:var(--warn);font-size:13.5px;padding:5px 0;line-height:1.4;
  padding-left:11px;border-left:2px solid var(--warn);margin:5px 0}

/* --- the second row: queue, flags, system ----------------------------- */
/* Shorter than the top row -- these are read to check on something, not
   worked through, and giving them equal weight said otherwise. */
.cols2{display:grid;grid-template-columns:minmax(0,1fr) 320px;
  gap:12px;align-items:start;margin-top:12px}
@media(max-width:820px){.cols2{grid-template-columns:1fr}}
.cols2 .panel{height:var(--ph2)}
.qrow{display:flex;gap:9px;align-items:baseline;font:12.5px/1.5 var(--mono);
  padding:2.5px 0}
.qrow .qt{flex:none;color:var(--dim);font-size:11px}
.qrow .qx{white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}
.qrow.crossed{opacity:.38;text-decoration:line-through}
.sub{font:10.5px/1.5 var(--mono);color:var(--dim);margin:10px 0 2px}
.sub code{font-size:10px}
.ok{font-size:12.5px;color:var(--accent);margin:2px 0 0}

/* meters and gauges share one bar so the plan and the box read as one scale */
.mt{margin:0 0 11px}
.mtl{display:flex;justify-content:space-between;font:10.5px/1 var(--mono);
  text-transform:uppercase;letter-spacing:.1em;color:var(--dim)}
.mtl .v{color:var(--fg);letter-spacing:0}
.bar{height:6px;border-radius:99px;background:var(--paper);margin:5px 0 3px;
  overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent);border-radius:99px}
.bar i.hot,.gb i.hot{background:var(--warn)}
.mtr{font:9.5px/1 var(--mono);color:var(--dim);letter-spacing:.06em}
/* --- the box, as three small multiples -------------------------------- */
/* One chart per metric. Three lines on shared axes would say cpu, memory and
   disk are comparable quantities, and the eye would try to compare them. */
.sparks{border-top:1px solid var(--line);margin-top:6px;padding-top:12px}
.spark{margin:0 0 13px}
.skh{display:flex;align-items:baseline;gap:7px;font:10.5px/1 var(--mono);color:var(--dim)}
.skl{text-transform:uppercase;letter-spacing:.1em}
.skv{color:var(--fg);margin-left:auto;font-variant-numeric:tabular-nums}
.skt{position:absolute;right:0;top:0;font-size:9.5px;opacity:0;
  font-variant-numeric:tabular-nums}
.skh{position:relative}
.spark svg{display:block;width:100%;height:40px;margin:5px 0 1px;overflow:visible}
/* Hairline, solid, one shade off the surface -- a dashed rule would read as a
   threshold when it is only the midpoint. */
.spark .gl{stroke:var(--line);stroke-width:1;vector-effect:non-scaling-stroke}
.spark .ln{fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round;
  stroke-linecap:round;vector-effect:non-scaling-stroke}
.spark .end{fill:var(--accent)}
.spark svg.hot .ln{stroke:var(--warn)}
.spark svg.hot .end{fill:var(--warn)}
.spark .cross{stroke:var(--fg);stroke-width:1;opacity:.45;vector-effect:non-scaling-stroke}
.skx{display:flex;justify-content:space-between;font:9px/1 var(--mono);
  color:var(--dim);letter-spacing:.06em;opacity:.75}
.skempty{font:10px/1.4 var(--mono);color:var(--dim);margin:6px 0 2px}
/* The trend sits where the axis label is until the pointer arrives, then the
   readout takes the same slot -- one line of furniture, never two. */
.spark:hover .skt,.spark.live .skt{opacity:1}
.spark:hover .skv,.spark.live .skv{opacity:0}
.svcs{display:flex;flex-wrap:wrap;gap:5px;margin-top:11px;padding-top:10px;
  border-top:1px solid var(--line)}
.svc{font:9.5px/1 var(--mono);padding:4px 7px;border-radius:99px;
  border:1px solid var(--line);color:var(--dim);letter-spacing:.05em}
.svc.up{color:var(--accent);border-color:currentColor}
.svc.down,.svc.stale{color:var(--warn);border-color:currentColor}
.svc em{font-style:normal;opacity:.75}
"""

PAGE_JS = """
document.addEventListener('click',e=>{
  const b=e.target.closest('.moretog'); if(!b) return;
  b.closest('.grp').classList.toggle('expand');
});

// A line chart that cannot be interrogated is a picture. The crosshair snaps to
// the nearest real sample rather than interpolating along the line, so the
// readout is always a number that was actually measured.
document.querySelectorAll('.spark[data-spark]').forEach(el=>{
  const pts=JSON.parse(el.dataset.spark), svg=el.querySelector('svg');
  if(!svg||!pts.length) return;
  const cross=svg.querySelector('.cross'), out=el.querySelector('.skt');
  const trend=out.textContent, W=240, DAY=86400;
  // The server's clock, not the browser's: the points were placed against
  // `now` at render time, and snapping against a different one would put the
  // crosshair a few pixels off its own dot.
  const now=+el.dataset.now;
  svg.addEventListener('pointermove',ev=>{
    const r=svg.getBoundingClientRect();
    const t=now-DAY*(1-(ev.clientX-r.left)/r.width);
    let best=pts[0];
    for(const p of pts) if(Math.abs(p[0]-t)<Math.abs(best[0]-t)) best=p;
    const x=W*Math.max(0,1-(now-best[0])/DAY);
    cross.setAttribute('x1',x); cross.setAttribute('x2',x); cross.hidden=false;
    out.textContent=best[1]+'% at '+
      new Date(best[0]*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
    el.classList.add('live');
  });
  svg.addEventListener('pointerleave',()=>{
    cross.hidden=true; out.textContent=trend; el.classList.remove('live');
  });
});
"""


SYSNAME = {'github': 'GitHub', 'calendar': 'Calendar', 'canvas': 'Canvas',
           'vault': 'Vault'}


def _gb(n):
    """Bytes as a short human string. One decimal below 10, none above -- a
    dashboard number is read at a glance and `13.7G` and `14G` say the same
    thing at different widths."""
    if n is None:
        return '--'
    for unit, size in (('T', 1 << 40), ('G', 1 << 30), ('M', 1 << 20)):
        if n >= size:
            v = n / size
            return ('%.1f%s' if v < 10 else '%.0f%s') % (v, unit)
    return '%dK' % (n / 1024)


def _dur(secs):
    if secs is None:
        return '--'
    secs = int(secs)
    if secs >= 86400:
        return '%dd %dh' % (secs // 86400, (secs % 86400) // 3600)
    if secs >= 3600:
        return '%dh %dm' % (secs // 3600, (secs % 3600) // 60)
    return '%dm' % (secs // 60)


def queue_panel(fl):
    """The queue, grouped by the system an event came from, with the flags on
    top.

    **Ticked rows are not drawn.** They were there to show the panel had been
    worked -- but a ticked row is a finished thought, and the queue is a list of
    unfinished ones. Keeping them meant the count in the header and the length
    of the list disagreed, and the thing being counted was the one that mattered.
    They are still in `Inbox/feed.json`; `zipper commit` prunes them.

    **Still read-only**, the same rule the old card states at length: a row means
    something happened that the vault has not accounted for, and the only thing
    that accounts for it is a pass. A tick box would offer to shorten the list
    without the reasoning the list exists to prompt.

    The flags sit above the events rather than in a panel of their own because
    they are read in the same glance and answer the same question -- what has not
    been dealt with. They are a different *kind* of thing, though, so they keep
    their own heading and their own colour: an event happened once and clears by
    being accounted for, a flag is a condition derived fresh every run and stops
    only when the underlying data changes.
    """
    rows = [r for r in (feed_rows() or feed_load()) if not r.get('done')]
    notes = note_rows()
    groups = {}
    for r in rows:
        groups.setdefault(r.get('system') or 'other', []).append(
            {'at': r.get('at') or '', 'text': r.get('text') or ''})
    for r in notes:
        groups.setdefault('vault', []).append(
            {'at': (r.get('when') or '')[11:16],
             'text': r.get('text') or '%-11s %s' % (r.get('action'), r.get('path'))})
    nopen = sum(len(g) for g in groups.values())

    out = []
    for sysk in ('vault', 'github', 'canvas', 'calendar'):
        g = groups.pop(sysk, None)
        if g:
            out.append((sysk, g))
    out.extend(sorted(groups.items()))

    html_ = []
    if fl:
        html_.append('<div class="grp flags"><div class="grph">'
                     '<span class="nm warn">flags</span><span class="w">%d</span></div>%s'
                     '</div>'
                     % (len(fl),
                        ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)))
    for sysk, g in out:
        g.sort(key=lambda r: r['at'], reverse=True)
        html_.append(
            '<div class="grp"><div class="grph"><span class="nm" %s>%s</span>'
            '<span class="w">%d</span></div>%s</div>'
            % (_style('--hue:%d' % hue(sysk)), esc(SYSNAME.get(sysk, sysk)), len(g),
               ''.join('<div class="qrow"><span class="qt">%s</span>'
                       '<span class="qx">%s</span></div>'
                       % (esc(r['at'][:5]), esc(r['text'])) for r in g)))
    if not html_:
        html_ = ['<p class="ok">Nothing outstanding &mdash; no open events, no flags.</p>']
    return nopen, len(fl), ''.join(html_)


SPARK_W, SPARK_H = 240.0, 40.0


def spark(samples, key, label, unit='%'):
    """One metric over 24 hours. A small multiple, not a third line on a shared
    chart: cpu, memory and disk are three different questions and putting them
    on one pair of axes would make them look comparable.

    **The y domain is 0-100 and fixed.** These are percentages of a capacity, and
    a sparkline auto-scaled to its own range turns 39.6%-40.1% of memory into an
    alarming climb. The trend question is answered by the delta printed beside
    the label instead -- a number that cannot be exaggerated by a scale.

    **The x domain is always the full 24 hours**, so a history that only goes
    back ten minutes draws ten minutes of line on the right and leaves the rest
    empty. That is the honest picture the first day the sampler runs.

    A gap longer than three sample intervals breaks the line rather than joining
    across it. `zipper-web` restarting is a normal event here, and a straight
    segment drawn over the hole would be invented data at exactly the moment
    someone is looking to see what happened.
    """
    now = time.time()
    pts = [(r['t'], r.get(key)) for r in samples if r.get(key) is not None]
    cur = pts[-1][1] if pts else None
    head = ('<span class="skl">%s</span><span class="skv">%s</span>'
            % (esc(label), '--' if cur is None else '%g%s' % (round(cur, 1), unit)))
    if len(pts) < 2:
        return ('<div class="spark"><div class="skh">%s</div>'
                '<p class="skempty">collecting &mdash; %d sample%s</p></div>'
                % (head, len(pts), '' if len(pts) == 1 else 's'))

    def xy(t, v):
        return (SPARK_W * max(0.0, 1 - (now - t) / box.WINDOW),
                SPARK_H * (1 - min(100.0, max(0.0, v)) / 100.0))

    segs, cur_seg = [], []
    prev_t = None
    for t, v in pts:
        if prev_t is not None and t - prev_t > box.SAMPLE_EVERY * 3:
            segs.append(cur_seg)
            cur_seg = []
        cur_seg.append('%.2f,%.2f' % xy(t, v))
        prev_t = t
    segs.append(cur_seg)

    first = pts[0][1]
    delta = cur - first
    span = (now - pts[0][0]) / 3600.0
    # "Over the last N hours" and not "over 24h" until there are 24 hours of it.
    trend = ('%+.1f%s in %s' % (delta, unit,
                                '%dh' % round(span) if span >= 1 else '%dm' % round(span * 60))
             if abs(delta) >= 0.05 else 'flat')
    ex, ey = xy(*pts[-1])
    hot = (cur or 0) >= 85
    return ('<div class="spark" data-spark="%s" data-now="%d">'
            '<div class="skh">%s<span class="skt">%s</span></div>'
            '<svg viewBox="0 0 %g %g" preserveAspectRatio="none" class="%s">'
            '<line class="gl" x1="0" y1="%g" x2="%g" y2="%g"/>'
            '%s<circle class="end" cx="%.2f" cy="%.2f" r="2.2"/>'
            '<line class="cross" x1="0" y1="0" x2="0" y2="%g" hidden/>'
            '</svg><div class="skx"><span>24h ago</span><span>now</span></div></div>'
            % (esc(json.dumps([[int(t), v] for t, v in pts])), int(now),
               head, esc(trend),
               SPARK_W, SPARK_H, 'hot' if hot else '',
               SPARK_H / 2, SPARK_W, SPARK_H / 2,
               ''.join('<polyline class="ln" points="%s"/>' % ' '.join(sg)
                       for sg in segs if len(sg) > 1),
               ex, ey, SPARK_H))


def system_panel():
    """The plan and the box, in one panel.

    Both answer "can I keep working right now", which is why they are one panel
    and not two: the session meter and the disk bar fail the same way, and the
    answer is the same shape.

    The usage meters are Anthropic's own numbers -- see `zipper.usage`; nothing
    on this box can compute them, and a locally-estimated meter that looked
    authoritative would be worse than none. The box's own numbers are the
    opposite case: they are entirely local, so they get a series rather than a
    bar, because "39%" is worth much less than "39% and falling".
    """
    u = usage.read()
    b = box.read()
    bits = []

    for m in u.get('meters', []):
        r = ''
        if m.get('resets'):
            try:
                t = datetime.datetime.fromisoformat(m['resets'].replace('Z', '+00:00'))
                r = t.astimezone().strftime('%a %H:%M')
            except Exception:
                r = ''
        bits.append('<div class="mt"><div class="mtl"><span>%s</span>'
                    '<span class="v">%s%%</span></div>'
                    '<div class="bar"><i class="%s" %s></i></div>'
                    '<div class="mtr">%s</div></div>'
                    % (esc(m['label']), esc('%g' % m['pct']),
                       'hot' if m['pct'] >= 80 else '',
                       _style('width:%.1f%%' % m['pct']),
                       'resets %s' % esc(r) if r else ''))
    if not u.get('meters'):
        bits.append('<p class="sub">usage: %s</p>'
                    % esc(u.get('error') or 'unavailable'))
    elif u.get('stale'):
        bits.append('<p class="sub">last good reading &mdash; %s</p>'
                    % esc(u.get('error') or 'refetch failed'))

    hist = box.history()
    mem, dsk = b.get('mem'), b.get('disk')
    bits.append('<div class="sparks">%s%s%s</div>'
                % (spark(hist, 'mem', 'memory'),
                   spark(hist, 'cpu', 'cpu'),
                   spark(hist, 'disk', 'disk')))
    bits.append('<p class="sub">%s of %s memory &middot; %s disk free &middot; '
                'load %.2f</p>'
                % (_gb(mem and mem['used']), _gb(mem and mem['total']),
                   _gb(dsk and (dsk['total'] - dsk['used'])), (b.get('load') or [0])[0]))

    # A unit that started before the current commit is serving code that is not
    # what HEAD says. That exact combination cost an afternoon on 2026-09-17 and
    # nothing said so; this is the surface that says so.
    svc = []
    for un in b.get('units', []):
        cls = 'up' if un['state'] == 'active' else 'down'
        note = ''
        if un['stale']:
            cls, note = 'stale', ' <em>pre-HEAD</em>'
        svc.append('<span class="svc %s">%s%s</span>'
                   % (cls, esc(un['name'].replace('zipper-', '')), note))
    bits.append('<div class="svcs">%s</div>' % ''.join(svc))
    bits.append('<p class="sub">up %s &middot; %d cpu</p>'
                % (_dur(b.get('uptime')), b.get('cpus') or 1))
    return ''.join(bits)


def _row(it, showat=True, showdue=False, pill=False):
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
    if it.get('overdue') and not showdue:
        meta.append('<span class="od">late</span>')
    # The day is the section heading in the Canvas panel, so without this the
    # course is nowhere on the row -- and which class a thing belongs to is most
    # of what makes a week legible at a glance.
    tag = ('<span class="pill" %s>%s</span>'
           % (_style('--hue:%d' % hue(it.get('tag'))), esc(it['tag']))
           if pill and it.get('tag') else '')
    # The description is the rest of what he wrote, kept off the title line and
    # **closed until the row is clicked**. A list is for finding the thing you
    # meant; the reasons are for after you have found it. Open, it shows in
    # full -- there is no second click, so there is nothing to truncate to.
    #
    # A task's description is always shown: he wrote it, and the title rule
    # means it is where the content deliberately went. A Canvas one is only
    # shown when it is short enough to be a summary -- those bodies run to
    # thousands of characters of course boilerplate, and two clamped lines of
    # "Submit your work using the template below" on twenty rows is the wall
    # this whole change is getting rid of. `/old` still expands the full text.
    d = it.get('desc') or ''
    if it.get('source') == 'canvas' and len(d) > 140:
        d = ''
    desc = '<span class="rowdesc">%s</span>' % esc(plain(d)) if d else ''
    return ('<li class="row%s%s"><button class="tick" data-key="%s"%s>%s</button>'
            '<span class="rowbody"><span class="rowtitle">%s</span>%s'
            '<span class="rowmeta">%s</span></span>%s</li>'
            % (' crossed' if it.get('done') else '', ' has' if desc else '',
               esc(it['key']),
               ' disabled title="submitted in Canvas"' if it.get('submitted') else '',
               '&#10003;' if it.get('done') else '', title, desc,
               ' &middot; '.join(meta), tag))


def page(day=None):
    """The whole page. `day` selects which day the second panel draws.

    The week is a column down the left rather than a strip across the top, so
    selecting a day is a vertical gesture right beside the thing it changes.
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
        # Counts only. The colour bars repeated what the course pills in the
        # Canvas panel already say, at a size too small to name anything.
        # Meetings first and on their own line: they are the fixed points of a
        # day, and stacking them lets the column be narrow enough to stop
        # competing with the panels it is there to select.
        sub = []
        if nmeet:
            sub.append('<span>%d mtg</span>' % nmeet)
        if items:
            sub.append('<span>%d due</span>' % len(items))
        wdays.append('<a class="wd%s%s" href="/?day=%s"><b>%s</b>'
                     '<span class="num">%s</span><span class="sub">%s</span></a>'
                     % (' on' if dd == day else '', ' today' if dd == today_iso else '',
                        dd, DOW[i], dd_d.strftime('%d'),
                        ''.join(sub) or '<span>&mdash;</span>'))

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
                          ''.join(_row(it, pill=True) for it in items)))
        return ''.join(out)

    allit = [it for v in wk['days'].values() for it in v]
    nopen = sum(1 for it in allit if not it['done'])
    ndone = len(allit) - nopen
    carry = ''
    if wk['carried']:
        carry = ('<div class="carry"><div class="ch">Carried in &middot; %d</div><ul>%s</ul></div>'
                 % (len(wk['carried']),
                    ''.join(_row(it, showdue=True, pill=True) for it in wk['carried'])))

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
                _row(t, showat=False, showdue=True).replace(
                    '<li class="row', '<li class="row hid', 1) if n >= 3
                else _row(t, showat=False, showdue=True)
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
    nqueue, nflags, queue_html = queue_panel(fl)
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

            '</div>'

            '<div class="cols2">'
            '<div class="panel"><div class="ph">queue%s<span class="n">%d</span></div>'
            '<div class="pb">%s</div></div>'
            '<div class="panel"><div class="ph">zipper</div>'
            '<div class="pb">%s</div></div>'
            '</div>'

            '</div>'
            % (NAV, esc(d.strftime('%A %d %B')), esc(label),
               '' if is_today else '<a class="back" href="/">back to today &rarr;</a>',
               ''.join(wdays), esc(label), len(blocks), ''.join(grid),
               nopen, ndone,
               carry, day_sections(lambda it: not it['done'])
               or '<p class="empty">Nothing due this week.</p>',
               day_sections(lambda it: it['done'])
               or '<p class="empty">Nothing handed in this week yet.</p>',
               len(tasks), len(donetasks),
               project_groups(tasks, True) or '<p class="empty">No open tasks.</p>',
               project_groups(donetasks, False) or '<p class="empty">Nothing ticked off yet.</p>',
               (' <span class="warn">&middot; %d flag%s</span>'
                % (nflags, '' if nflags == 1 else 's')) if nflags else '',
               nqueue, queue_html,
               system_panel()))
    return _page('Zipper', CSS, body, PAGE_JS)




