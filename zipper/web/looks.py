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
                    % ('on' if i == n else '', i, i) for i in range(1, 6))
    return ('<nav class="looknav"><a href="/">&larr; live</a>'
            '<span style="opacity:.35">contenders</span>%s'
            '<span style="opacity:.45;margin-left:auto">%s</span></nav>'
            % (links, esc(LOOKS[n][0])))


def daystrip_days(day):
    mon = monday_of(day)
    return [(mon + datetime.timedelta(days=i)).isoformat() for i in range(7)]


# ================================================================ 1. Rail

RAIL_CSS = """
:root{--bg:#f7f6f3;--fg:#171614;--dim:#6e685f;--line:#e3dfd7;--card:#fff;
  --accent:#8c1d40;--warn:#b3541e;--ok:#3f6f4a}
@media(prefers-color-scheme:dark){:root{--bg:#121110;--fg:#efebe4;--dim:#948d83;
  --line:#2a2724;--card:#1a1917;--accent:#f0839f;--warn:#e0965c;--ok:#7fb98b}}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:1280px;margin:0 auto;padding:16px 22px 52px}
.head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:14px 0 14px}
h1{font:600 20px/1.2 var(--sans);margin:0;letter-spacing:-.015em}
.head .meta{font:11px/1 var(--mono);color:var(--dim);letter-spacing:.06em;text-transform:uppercase}
.head .back{margin-left:auto;font:11px/1 var(--mono);color:var(--accent);text-decoration:none}
/* The week is navigation and nothing else: counts and colour, never titles.
   Clicking a chip moves the schedule and the due list together -- that is the
   whole contract of this layout. */
.strip{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;margin:0 0 16px}
.sday{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:8px 10px 9px;text-decoration:none;display:block;position:relative}
.sday:hover{border-color:var(--accent)}
.sday.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.sday.today .num{color:var(--accent)}
.sday b{font:600 10.5px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;
  color:var(--dim);display:block}
.sday .num{font:600 21px/1.15 var(--mono);display:block}
.sday .bars{display:flex;gap:2px;margin-top:6px;height:4px}
.sday .bars i{flex:1;border-radius:2px;max-width:18px}
.sday .none{display:block;height:4px;margin-top:6px;border-radius:2px;background:var(--line);
  max-width:18px}
.sday .mt{position:absolute;top:8px;right:9px;font:10px/1 var(--mono);color:var(--dim);opacity:.75}
.cols{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:16px;align-items:start}
@media(max-width:960px){.cols{grid-template-columns:1fr}}
.stack{display:flex;flex-direction:column;gap:16px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:13px 15px}
.panel h2{font:600 10.5px/1 var(--mono);text-transform:uppercase;letter-spacing:.11em;
  color:var(--dim);margin:0 0 11px;display:flex;align-items:center;gap:8px}
.panel h2 .n{margin-left:auto;color:var(--accent)}
.panel h2 .lk{margin-left:auto;color:var(--accent);text-decoration:none}
/* The fix: a fixed-height viewport that scrolls. The day's contents never
   decide the page's height. */
.daycol{height:430px;overflow-y:auto;position:relative}
.daybody{position:relative;height:1000px}
.hr{position:absolute;left:0;right:0;border-top:1px solid var(--line)}
.hr span{position:absolute;top:-8px;left:0;font:10px/1 var(--mono);color:var(--dim);
  background:var(--card);padding-right:6px}
.nowline{position:absolute;left:42px;right:0;border-top:2px solid var(--accent);z-index:5}
.nowline:after{content:'';position:absolute;left:-5px;top:-4px;width:7px;height:7px;
  border-radius:50%;background:var(--accent)}
.blk{position:absolute;border-radius:8px;padding:4px 9px;overflow:hidden;cursor:pointer;z-index:2;
  display:flex;flex-direction:column;
  background:hsl(var(--hue) 60% 55% / .14);border:1px solid hsl(var(--hue) 60% 55% / .42);
  border-left:3px solid hsl(var(--hue) 60% 50%)}
@media(prefers-color-scheme:dark){.blk{background:hsl(var(--hue) 50% 55% / .2)}}
.blk.live{box-shadow:0 0 0 1px var(--accent),0 6px 18px rgba(0,0,0,.14)}
.blk.past{opacity:.42}
.blk.open{height:auto!important;min-height:var(--h);z-index:9;overflow:visible;
  box-shadow:0 10px 30px rgba(0,0,0,.24)}
.bt{font:600 13px/1.2 var(--sans);flex:none}
.bm{font:10.5px/1.4 var(--mono);color:var(--dim);flex:none}
.why{font:11.5px/1.4 var(--sans);color:var(--dim);margin-top:3px;flex:1;min-height:0;
  overflow:hidden;-webkit-mask-image:linear-gradient(#000 calc(100% - 10px),transparent);
  mask-image:linear-gradient(#000 calc(100% - 10px),transparent)}
.blk.open .why{flex:none;overflow:visible;-webkit-mask-image:none;mask-image:none}
.why p{margin:0 0 4px}
.acts{display:none;gap:5px;flex-wrap:wrap;margin-top:6px;padding-top:6px;border-top:1px solid var(--line)}
.blk.open .acts{display:flex}
.act{font:10.5px/1.5 var(--mono);border:1px solid var(--line);border-radius:5px;padding:2px 7px;
  color:var(--accent);text-decoration:none;white-space:nowrap;background:var(--card)}
.act:hover{border-color:var(--accent)}
li.row{display:flex;gap:9px;padding:8px 0;border-bottom:1px solid var(--line);align-items:flex-start}
li.row:last-child{border-bottom:0}
.scroll{max-height:290px;overflow-y:auto}
.pill{font:10px/1.6 var(--mono);letter-spacing:.05em;border-radius:99px;padding:0 7px;
  background:hsl(var(--hue) 60% 55% / .16);color:hsl(var(--hue) 55% 34%);
  border:1px solid hsl(var(--hue) 60% 55% / .34);white-space:nowrap;flex:none}
@media(prefers-color-scheme:dark){.pill{color:hsl(var(--hue) 70% 74%)}}
.at{color:var(--dim)}
.od{color:var(--warn);font-weight:600}
.sm{font:11px/1.5 var(--mono);color:var(--dim)}
.empty{font:12.5px/1.6 var(--mono);color:var(--dim);opacity:.65;padding:6px 0}
.flag{color:var(--warn);font-size:13px;padding:3px 0}
.carry{border-left:2px solid var(--warn);padding-left:10px}
"""


def look_rail(day=None):
    """1 -- Rail. The one he picked, with the four fixes in.

    Geometry is correct now: blocks sit at their real clock position, because
    the hue and the position finally share one `style` attribute.

    The week strip is navigation and carries no titles -- clicking a day moves
    **both** the schedule and the *Due* list, so the day being read is never
    ambiguous. Canvas work and `Tasks/` are separate panels, because a deadline
    someone else set and a note he wrote to himself are not the same kind of
    thing and ranking them into one list hid that. And the furniture is
    monospaced throughout.
    """
    day = day or core.TODAY.isoformat()
    today_iso = core.TODAY.isoformat()
    is_today = day == today_iso
    d = datetime.date(*map(int, day.split('-')))
    blocks, nowpct = _blocks(day, is_today)
    wk = week_canvas(day)

    chips = []
    for i, dd in enumerate(daystrip_days(day)):
        items = [it for it in wk['days'][dd] if not it['done']]
        nmeet = len(today_split(dd)[1])
        bars = (''.join('<i %s></i>' % _style('background:hsl(%d 60%% 55%%)' % hue(it['tag']))
                        for it in items[:5])
                if items else '<span class="none"></span>')
        chips.append('<a class="sday%s%s" href="/look/1?day=%s"><b>%s</b>'
                     '<span class="num">%s</span>%s%s</a>'
                     % (' on' if dd == day else '', ' today' if dd == today_iso else '',
                        dd, DOW[i], dd[8:10],
                        '<span class="mt">%s</span>' % ('●' * min(nmeet, 3)) if nmeet else '',
                        '<span class="bars">%s</span>' % bars))

    grid = ''.join('<div class="hr" %s><span>%02d:00</span></div>'
                   % (_style('top:%.3f%%' % p), m // 60) for m, p in _hours())
    if nowpct is not None:
        grid += '<div class="nowline" %s></div>' % _style('top:%.3f%%' % nowpct)
    for b in blocks:
        e = b['ev']
        grid += ('<div class="blk%s%s" %s>'
                 '<div class="bt">%s</div><div class="bm">%s &middot; %s</div>%s'
                 '<div class="acts">%s</div></div>'
                 % (' past' if b['past'] else '', ' live' if b['live'] else '',
                    _style('--hue:%d' % b['hue'], 'top:%.3f%%' % b['top'],
                           '--h:%.3f%%' % b['h'], 'height:%.3f%%' % b['h'],
                           'left:calc(46px + %.3f%%)' % (b['lane'] * 100.0 / b['nlanes']),
                           'width:calc(%.3f%% - 50px)' % (100.0 / b['nlanes'])),
                    esc(e['summary']), b['span'], core._dur(b['mins']),
                    _why(b['rec']), _blk_acts(e, b['rec'])))
    if not blocks:
        grid += ('<p class="empty" %s>Nothing scheduled.</p>'
                 % _style('position:absolute', 'top:44%', 'left:50px'))

    def crow(it):
        title = plain(it['title'])
        if it['url']:
            title = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
        meta = []
        if it.get('at') and it['at'] not in ('', '23:59'):
            meta.append('<span class="at">%s</span>' % esc(it['at']))
        if it.get('points'):
            meta.append('%s pts' % esc(str(it['points'])))
        if it.get('elsewhere'):
            meta.append('on %s' % esc(it['elsewhere']))
        if it.get('overdue'):
            meta.append('<span class="od">late</span>')
        return ('<li class="row%s"><button class="tick" data-key="%s"%s>%s</button>'
                '<span class="rowbody"><span class="rowtitle">%s</span>'
                '<span class="rowmeta">%s</span></span><span class="pill" %s>%s</span></li>'
                % (' crossed' if it['done'] else '', esc(it['key']),
                   ' disabled title="submitted in Canvas"' if it.get('submitted') else '',
                   '&#10003;' if it['done'] else '', title, ' &middot; '.join(meta),
                   _style('--hue:%d' % hue(it['tag'])), esc(it['tag'])))

    due = canvas_on(day)
    duehtml = ''.join(crow(it) for it in due)
    carried = ''
    if is_today and wk['carried']:
        carried = ('<div class="carry" style="margin-top:10px"><h2 class="sm" '
                   'style="margin:0 0 4px">Carried in &middot; %d</h2><ul>%s</ul></div>'
                   % (len(wk['carried']), ''.join(crow(it) for it in wk['carried'])))

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
               else (t['due'][5:] if t['due'] else ''),
               'next' if t.get('next') else ''] if x))
        for t in tasks[:12])

    fl = flags()
    label = ('Today' if is_today else
             'Tomorrow' if d == core.TODAY + datetime.timedelta(days=1) else
             d.strftime('%A'))
    nopen = sum(1 for it in due if not it['done'])
    body = ('<div class="wrap">%s'
            '<div class="head"><h1>%s</h1><span class="meta">%s</span>%s</div>'
            '<div class="strip">%s</div>'
            '<div class="cols">'
            '<div class="panel"><h2>Schedule <span class="n">%d</span></h2>'
            '<div class="daycol" data-scrollnow><div class="daybody">%s</div></div></div>'
            '<div class="stack">'
            '<div class="panel"><h2>Due %s <span class="n">%d</span></h2>'
            '<div class="scroll"><ul>%s</ul></div>%s</div>'
            '<div class="panel"><h2>Tasks <a class="lk" href="/tasks" target="_blank" '
            'rel="noopener">see all</a></h2><div class="scroll"><ul>%s</ul></div></div>'
            '%s</div></div></div>'
            % (looknav(1), esc(d.strftime('%A %d %B')), esc(label),
               '' if is_today else '<a class="back" href="/look/1">back to today &rarr;</a>',
               ''.join(chips), len(blocks), grid,
               esc(label.lower()), nopen,
               duehtml or '<li class="empty">Nothing due %s.</li>' % esc(label.lower()),
               carried, trows or '<li class="empty">No open tasks.</li>',
               ('<div class="panel"><h2>Flags <span class="n">%d</span></h2>%s</div>'
                % (len(fl), ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)))
               if fl else ''))
    return _page('Look 1 - Rail', RAIL_CSS, body)


LOOKS[1] = ('Rail', 'refined: real positions, split sections, day picks the due list', look_rail)


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


# ================================================================ 3. Now

NOW_CSS = """
:root{--bg:#0f0f11;--fg:#f2f0ee;--dim:#85817c;--line:#232225;--card:#171618;
  --accent:#7fe7c4;--warn:#f0a95c;--hot:#f2727e}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:1200px;margin:0 auto;padding:16px 22px 48px}
.head{display:flex;align-items:baseline;gap:12px;margin:14px 0 18px;flex-wrap:wrap}
h1{font:600 13px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dim);margin:0}
.head .clock{margin-left:auto;font:600 13px/1 var(--mono);color:var(--dim)}
/* The hero answers one question -- what is happening, and what is next -- at a
   size you can read from across the room. Everything else on the page is
   deliberately smaller than it. */
.hero{border:1px solid var(--line);border-radius:18px;padding:22px 24px;margin-bottom:14px;
  background:linear-gradient(135deg,hsl(var(--hue) 45% 22%),var(--card) 62%);position:relative;
  overflow:hidden}
.hero:before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
  background:hsl(var(--hue) 65% 58%)}
.hero .kick{font:11px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;
  color:hsl(var(--hue) 60% 70%);margin-bottom:9px}
.hero .big{font:600 30px/1.15 var(--sans);letter-spacing:-.025em;margin:0 0 6px}
@media(max-width:620px){.hero .big{font-size:23px}}
.hero .when{font:13px/1.5 var(--mono);color:var(--dim)}
.hero .why{font:13.5px/1.55 var(--sans);color:var(--dim);margin-top:11px;max-width:62ch;
  border-top:1px solid var(--line);padding-top:10px}
.hero .why p{margin:0 0 6px}
.hero .acts{display:flex;gap:7px;flex-wrap:wrap;margin-top:13px}
.act{font:11px/1.6 var(--mono);border:1px solid var(--line);border-radius:7px;padding:3px 10px;
  color:var(--accent);text-decoration:none;background:rgba(255,255,255,.03)}
.act:hover{border-color:var(--accent)}
.hero.idle{background:var(--card)}
.hero.idle:before{background:var(--line)}
.next{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin-bottom:16px}
.nx{border:1px solid var(--line);border-radius:12px;padding:11px 13px;background:var(--card);
  border-left:3px solid hsl(var(--hue) 62% 56%)}
.nx .t{font:11px/1 var(--mono);color:var(--dim);letter-spacing:.07em}
.nx .s{font:600 14px/1.3 var(--sans);margin-top:4px}
.cols{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
@media(max-width:920px){.cols{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:13px;
  display:flex;flex-direction:column;height:360px;overflow:hidden}
.ph{padding:11px 14px 9px;border-bottom:1px solid var(--line);display:flex;align-items:center;
  gap:8px;font:600 10.5px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase;color:var(--dim)}
.ph .n{margin-left:auto;color:var(--accent)}
.pb{flex:1;overflow-y:auto;padding:6px 14px 12px}
/* Seven buttons, not a grid: the week here is purely "which day's deadlines am
   I reading", and it costs one row. */
.days{display:flex;gap:5px;padding:9px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.dbtn{font:10.5px/1 var(--mono);letter-spacing:.06em;text-decoration:none;border-radius:7px;
  border:1px solid var(--line);padding:5px 7px;color:var(--dim);text-align:center;flex:1;min-width:34px}
.dbtn b{display:block;font-size:13px;color:var(--fg);margin-top:2px;font-weight:600}
.dbtn.on{border-color:var(--accent);color:var(--accent)}
.dbtn.on b{color:var(--accent)}
.dbtn.has{border-color:hsl(var(--hue) 50% 42%)}
.dbtn:hover{color:var(--fg)}
li.row{display:flex;gap:9px;padding:8px 0;border-bottom:1px solid var(--line);align-items:flex-start}
li.row:last-child{border-bottom:0}
.rowtitle{font-size:13px}
.tt{font:10px/1.6 var(--mono);padding:0 6px;border-radius:99px;white-space:nowrap;flex:none;
  background:hsl(var(--hue) 55% 55% / .18);color:hsl(var(--hue) 68% 74%)}
.empty{font:12px/1.7 var(--mono);color:var(--dim);opacity:.55;padding:8px 0}
.flag{color:var(--warn);font:12.5px/1.6 var(--mono);padding:4px 0}
.od{color:var(--hot)}
.later{font:11px/1.6 var(--mono);color:var(--dim);padding:7px 0 0;border-top:1px solid var(--line);
  margin-top:7px;display:flex;gap:9px}
.later b{color:var(--fg);font-weight:500}
"""


def look_now(day=None):
    """3 -- Now. One question at hero size: what is happening, and what is next.

    The other four looks all draw the whole day and leave the reading to him.
    This one does the reading: the largest thing on the page is the meeting
    currently running -- with its note's *why* in full, at a size that can
    actually be read before walking into the room -- and beside it what follows.
    The day's remaining blocks shrink to a strip of times, because once you know
    what is now and next, the rest of the morning is reference.

    There is no pixel grid at all, so there is no height to control. The three
    panels below are fixed 360px: **Due** (Canvas, day-selectable from its own
    seven-button row), **Tasks**, **Flags**.
    """
    day = day or core.TODAY.isoformat()
    today_iso = core.TODAY.isoformat()
    is_today = day == today_iso
    blocks, _ = _blocks(today_iso, True)
    now = datetime.datetime.now()
    nowm = now.hour * 60 + now.minute

    cur = next((b for b in blocks if b['s'] <= nowm < b['e']), None)
    later = [b for b in blocks if b['s'] > nowm]
    nxt = later[0] if later else None

    if cur:
        e, rec = cur['ev'], cur['rec']
        left = cur['e'] - nowm
        hero = ('<div class="hero" %s><div class="kick">now &middot; %d min left</div>'
                '<h2 class="big">%s</h2><div class="when">%s &middot; %s%s</div>%s%s</div>'
                % (_style('--hue:%d' % cur['hue']), left, esc(e['summary']),
                   cur['span'], core._dur(cur['mins']),
                   ' &middot; %s' % esc(e['loc'][:40])
                   if e['loc'] and 'http' not in e['loc'] else '',
                   _why(rec),
                   '<div class="acts">%s</div>' % _blk_acts(e, rec)))
    elif nxt:
        e, rec = nxt['ev'], nxt['rec']
        mins = nxt['s'] - nowm
        hero = ('<div class="hero" %s><div class="kick">next &middot; in %s</div>'
                '<h2 class="big">%s</h2><div class="when">%s &middot; %s</div>%s%s</div>'
                % (_style('--hue:%d' % nxt['hue']), core._dur(mins), esc(e['summary']),
                   nxt['span'], core._dur(nxt['mins']), _why(rec),
                   '<div class="acts">%s</div>' % _blk_acts(e, rec)))
        nxt = later[1] if len(later) > 1 else None
    else:
        done = [b for b in blocks if b['e'] <= nowm]
        hero = ('<div class="hero idle"><div class="kick">now</div>'
                '<h2 class="big">Nothing scheduled.</h2>'
                '<div class="when">%s</div></div>'
                % ('%d meeting%s done today' % (len(done), '' if len(done) == 1 else 's')
                   if done else 'No meetings today at all.'))

    cards = []
    if nxt:
        cards.append('<div class="nx" %s><div class="t">next &middot; %s</div>'
                     '<div class="s">%s</div></div>'
                     % (_style('--hue:%d' % nxt['hue']), nxt['span'],
                        esc(nxt['ev']['summary'])))
    duetoday = [it for it in canvas_on(today_iso) if not it['done']]
    if duetoday:
        cards.append('<div class="nx" %s><div class="t">due today &middot; %d</div>'
                     '<div class="s">%s</div></div>'
                     % (_style('--hue:%d' % hue(duetoday[0]['tag'])), len(duetoday),
                        plain(duetoday[0]['title'])))
    tasks = task_rows()
    od = [t for t in tasks if t['overdue']]
    if od:
        cards.append('<div class="nx" %s><div class="t">overdue tasks &middot; %d</div>'
                     '<div class="s">%s</div></div>'
                     % (_style('--hue:2'), len(od), plain(od[0]['title'])))

    rest = ''
    if later:
        rest = ('<div class="later"><span>rest of today</span>%s</div>'
                % ''.join('<span><b>%s</b> %s</span>' % (b['from'], esc(b['ev']['summary'][:26]))
                          for b in later[:5]))

    wk = week_canvas(day)
    dbtns = ''
    for i, dd in enumerate(daystrip_days(day)):
        n = sum(1 for it in wk['days'][dd] if not it['done'])
        dbtns += ('<a class="dbtn%s%s" href="/look/3?day=%s" %s>%s<b>%s</b></a>'
                  % (' on' if dd == day else '', ' has' if n else '', dd,
                     _style('--hue:%d' % (150 if n else 0)), DOW[i][:2], dd[8:10]))

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
                '<span class="rowmeta">%s</span></span><span class="tt" %s>%s</span></li>'
                % (' crossed' if it['done'] else '', esc(it['key']),
                   ' disabled' if it.get('submitted') else '',
                   '&#10003;' if it['done'] else '', title, ' &middot; '.join(meta),
                   _style('--hue:%d' % hue(it['tag'])), esc(it['tag'])))

    due = canvas_on(day)
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
        for t in tasks)
    fl = flags()

    body = ('<div class="wrap">%s<div class="head"><h1>%s</h1>'
            '<span class="clock">%s</span></div>%s%s%s'
            '<div class="cols">'
            '<div class="panel"><div class="ph">due<span class="n">%d</span></div>'
            '<div class="days">%s</div><div class="pb"><ul>%s</ul></div></div>'
            '<div class="panel"><div class="ph">tasks<span class="n">%d</span></div>'
            '<div class="pb"><ul>%s</ul></div></div>'
            '<div class="panel"><div class="ph">flags<span class="n">%d</span></div>'
            '<div class="pb">%s</div></div></div></div>'
            % (looknav(3), esc(core.TODAY.strftime('%A %d %B')),
               esc(now.strftime('%H:%M')), hero,
               '<div class="next">%s</div>' % ''.join(cards) if cards else '',
               rest, sum(1 for it in due if not it['done']), dbtns,
               ''.join(crow(it) for it in due) or '<li class="empty">Nothing due.</li>',
               len(tasks), trows or '<li class="empty">No open tasks.</li>',
               len(fl), ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)
               or '<p class="empty">Clean.</p>'))
    return _page('Look 3 - Now', NOW_CSS, body)


LOOKS[3] = ('Now', 'hero answers what is happening and what is next', look_now)


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


# ================================================================ 5. Matrix

MATRIX_CSS = """
:root{--bg:#0a0b0e;--fg:#e9ebf0;--dim:#78818f;--line:#1b1e24;--card:#111318;
  --accent:#f0abfc;--accent2:#67e8f9;--warn:#fcd34d;--hot:#fb7185}
body{background:var(--bg);color:var(--fg);
  background-image:radial-gradient(1000px 420px at 50% -10%,rgba(240,171,252,.07),transparent)}
.wrap{max-width:1500px;margin:0 auto;padding:16px 20px 46px}
.head{display:flex;align-items:baseline;gap:13px;flex-wrap:wrap;margin:14px 0 13px}
h1{font:600 18px/1.2 var(--mono);margin:0;letter-spacing:.01em;
  background:linear-gradient(92deg,var(--accent),var(--accent2));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.head .meta{font:10.5px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase;color:var(--dim)}
.head .clock{margin-left:auto;font:600 14px/1 var(--mono);color:var(--accent2)}
/* The whole week as one grid: seven day columns sharing one hour axis, so the
   shape of the week and the shape of each day are the same picture. Fixed
   height, because the axis is fixed -- adding a meeting cannot resize it. */
.matrix{background:var(--card);border:1px solid var(--line);border-radius:14px;
  overflow:hidden;margin-bottom:13px}
.mh{display:grid;grid-template-columns:44px repeat(7,minmax(0,1fr));
  border-bottom:1px solid var(--line)}
.mh .sp{border-right:1px solid var(--line)}
.mh a{padding:9px 6px 8px;text-align:center;text-decoration:none;
  border-right:1px solid var(--line);font:10px/1 var(--mono);letter-spacing:.09em;
  text-transform:uppercase;color:var(--dim)}
.mh a:last-child{border-right:0}
.mh a b{display:block;font-size:16px;font-weight:600;margin-top:3px;color:var(--fg);letter-spacing:0}
.mh a .due{display:block;margin-top:4px;font-size:9.5px;color:var(--accent)}
.mh a.on{background:rgba(240,171,252,.09);color:var(--accent)}
.mh a.on b{color:var(--accent)}
.mh a.today b{text-decoration:underline;text-underline-offset:3px;text-decoration-color:var(--accent2)}
.mh a:hover{background:rgba(255,255,255,.04)}
.mbody{height:430px;overflow-y:auto;position:relative}
.mgrid{display:grid;grid-template-columns:44px repeat(7,minmax(0,1fr));position:relative;
  height:1000px}
.axis{position:relative;border-right:1px solid var(--line)}
.axis span{position:absolute;right:6px;font:9.5px/1 var(--mono);color:var(--dim);transform:translateY(-4px)}
.dcell{position:relative;border-right:1px solid var(--line)}
.dcell:last-child{border-right:0}
.dcell.on{background:rgba(240,171,252,.05)}
.dcell.past{opacity:.5}
.hr{position:absolute;left:0;right:0;border-top:1px solid var(--line);opacity:.55}
.nowline{position:absolute;left:0;right:0;border-top:2px solid var(--hot);z-index:6;
  box-shadow:0 0 12px var(--hot)}
.blk{position:absolute;border-radius:5px;padding:2px 5px;overflow:hidden;cursor:pointer;z-index:2;
  background:hsl(var(--hue) 58% 55% / .22);border-left:2px solid hsl(var(--hue) 68% 62%);
  font:10.5px/1.2 var(--sans);min-width:0}
.blk.past{opacity:.4}
.blk.live{box-shadow:0 0 0 1px var(--hot)}
.blk.open{height:auto!important;z-index:9;background:var(--card);min-width:210px;
  box-shadow:0 12px 32px rgba(0,0,0,.65);border:1px solid var(--accent);padding:6px 9px}
.bt{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.blk.open .bt{white-space:normal}
.bm{font:9px/1.4 var(--mono);color:var(--dim)}
.why{display:none;font:11px/1.4 var(--sans);color:var(--dim);margin-top:5px}
.blk.open .why{display:block}
.why p{margin:0 0 4px}
.acts{display:none;gap:5px;flex-wrap:wrap;margin-top:6px}
.blk.open .acts{display:flex}
.act{font:10px/1.5 var(--mono);border:1px solid var(--line);border-radius:4px;padding:2px 6px;
  color:var(--accent2);text-decoration:none}
.cols{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr) minmax(0,.85fr);gap:12px}
@media(max-width:1060px){.cols{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:13px;
  display:flex;flex-direction:column;height:340px;overflow:hidden}
.ph{padding:11px 14px 9px;border-bottom:1px solid var(--line);display:flex;align-items:center;
  gap:8px;font:600 10.5px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase;color:var(--dim)}
.ph .n{margin-left:auto;color:var(--accent)}
.pb{flex:1;overflow-y:auto;padding:6px 14px 12px}
li.row{display:flex;gap:9px;padding:8px 0;border-bottom:1px solid var(--line);align-items:flex-start}
li.row:last-child{border-bottom:0}
.rowtitle{font-size:13px}
.tt{font:10px/1.6 var(--mono);padding:0 6px;border-radius:99px;white-space:nowrap;flex:none;
  background:hsl(var(--hue) 55% 55% / .2);color:hsl(var(--hue) 70% 76%)}
.empty{font:12px/1.7 var(--mono);color:var(--dim);opacity:.55;padding:8px 0}
.flag{color:var(--warn);font:12.5px/1.6 var(--mono);padding:4px 0}
.od{color:var(--hot)}
"""


def look_matrix(day=None):
    """5 -- Matrix. The whole week on one hour axis.

    The furthest from the live page. Seven day columns share a single 06:00
    -23:00 rule, so every meeting of the week is placed at its real time in one
    picture -- Tuesday being solid and Thursday being empty is a thing you see
    rather than count. The day and the week stop being two views of the same
    calendar; they are one.

    Height is structural here: the axis is fixed, so the grid is 430px whether
    the week holds three meetings or thirty. Below it, **Due**, **Tasks** and
    **Flags** as three fixed panels, with the day headers selecting which day
    the Due panel reads.
    """
    day = day or core.TODAY.isoformat()
    today_iso = core.TODAY.isoformat()
    days = daystrip_days(day)
    wk = week_canvas(day)

    heads = ['<div class="sp"></div>']
    cells = ['<div class="axis">%s</div>'
             % ''.join('<span %s>%02d</span>' % (_style('top:%.3f%%' % p), m // 60)
                       for m, p in _hours())]
    for i, dd in enumerate(days):
        ndue = sum(1 for it in wk['days'][dd] if not it['done'])
        heads.append('<a class="%s%s" href="/look/5?day=%s">%s<b>%s</b>%s</a>'
                     % ('on' if dd == day else '', ' today' if dd == today_iso else '',
                        dd, DOW[i], dd[8:10],
                        '<span class="due">%d due</span>' % ndue if ndue else ''))
        blocks, nowpct = _blocks(dd, dd == today_iso)
        inner = ''.join('<div class="hr" %s></div>' % _style('top:%.3f%%' % p)
                        for _m, p in _hours())
        if nowpct is not None:
            inner += '<div class="nowline" %s></div>' % _style('top:%.3f%%' % nowpct)
        for b in blocks:
            e = b['ev']
            inner += ('<div class="blk%s%s" %s><div class="bt">%s</div>'
                      '<div class="bm">%s</div>%s<div class="acts">%s</div></div>'
                      % (' past' if b['past'] else '', ' live' if b['live'] else '',
                         _style('--hue:%d' % b['hue'], 'top:%.3f%%' % b['top'],
                                'height:%.3f%%' % b['h'],
                                'left:calc(2px + %.3f%%)' % (b['lane'] * 96.0 / b['nlanes']),
                                'width:calc(%.3f%% - 4px)' % (96.0 / b['nlanes'])),
                         esc(e['summary']), b['from'], _why(b['rec']),
                         _blk_acts(e, b['rec'])))
        cells.append('<div class="dcell%s%s">%s</div>'
                     % (' on' if dd == day else '', ' past' if dd < today_iso else '', inner))

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
        if it.get('overdue'):
            meta.append('<span class="od">late</span>')
        return ('<li class="row%s"><button class="tick" data-key="%s"%s>%s</button>'
                '<span class="rowbody"><span class="rowtitle">%s</span>'
                '<span class="rowmeta">%s</span></span><span class="tt" %s>%s</span></li>'
                % (' crossed' if it['done'] else '', esc(it['key']),
                   ' disabled' if it.get('submitted') else '',
                   '&#10003;' if it['done'] else '', title, ' &middot; '.join(meta),
                   _style('--hue:%d' % hue(it['tag'])), esc(it['tag'])))

    due = canvas_on(day) + ([] if day != today_iso else wk['carried'])
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
        for t in tasks)
    fl = flags()
    d = datetime.date(*map(int, day.split('-')))

    body = ('<div class="wrap">%s<div class="head"><h1>%s</h1>'
            '<span class="meta">week of %s</span><span class="clock">%s</span></div>'
            '<div class="matrix"><div class="mh">%s</div>'
            '<div class="mbody" data-scrollnow><div class="mgrid">%s</div></div></div>'
            '<div class="cols">'
            '<div class="panel"><div class="ph">due %s<span class="n">%d</span></div>'
            '<div class="pb"><ul>%s</ul></div></div>'
            '<div class="panel"><div class="ph">tasks<span class="n">%d</span></div>'
            '<div class="pb"><ul>%s</ul></div></div>'
            '<div class="panel"><div class="ph">flags<span class="n">%d</span></div>'
            '<div class="pb">%s</div></div></div></div>'
            % (looknav(5), esc(core.TODAY.strftime('%A %d %B')), esc(wk['monday'][5:]),
               esc(datetime.datetime.now().strftime('%H:%M')),
               ''.join(heads), ''.join(cells), esc(d.strftime('%a %d').lower()),
               sum(1 for it in due if not it['done']),
               ''.join(crow(it) for it in due) or '<li class="empty">Nothing due.</li>',
               len(tasks), trows or '<li class="empty">No open tasks.</li>',
               len(fl), ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)
               or '<p class="empty">Clean.</p>'))
    return _page('Look 5 - Matrix', MATRIX_CSS, body)


LOOKS[5] = ('Matrix', 'the whole week on one hour axis', look_matrix)


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
