"""Five candidate dashboards, at /look/1 .. /look/5.

Contenders, not replacements: the live page at `/` is untouched, and each of
these is a whole-page render of the same data so they can be compared by
looking rather than by argument. They share `data.py` with the real dashboard
and own nothing.

Three complaints shape all five, and every look answers all three -- what
differs is *how*:

1. **The schedule's height followed its contents.** A day with an 08:00 and a
   21:00 booking drew a 13-hour column and shoved everything below it off the
   screen; a day with one meeting drew a stub. Here the day column is a
   fixed-height viewport scrolled to now, with a full 06:00-23:00 span inside
   it. Block height still means duration -- that is the part he likes -- but
   the *page* stops reflowing around it.
2. **The same Canvas items appeared three times** -- once as "Due today", again
   in the week grid, and a third time in "What to work on". Each look picks one
   home for an assignment and makes the other surfaces either navigation or
   summary. Nothing here shows the same row twice.
3. **It read as dull.** Courses get a stable hue, so a glance says *which
   class* before any text is read.
"""
from .base import *
from .base import core, canvas, events, metrics
from .js import TICKJS
from .data import (canvas_items, flags, monday_of, open_tasks, ranked,
                   today_split, week_canvas)
from .render import _gcal_link, _join_link, _lanes, esc


LOOKS = {}          # n -> (title, blurb, renderer)

DOW = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')

# The day column always spans the same hours, whatever is booked. 06:00-23:00
# covers every event these calendars have ever carried; anything outside it
# still renders, clamped to the edge, because a clipped block is better than a
# column that resizes the page.
DAY_LO, DAY_HI = 6 * 60, 23 * 60


def hue(name):
    """A stable hue per course/label. Not random per load -- the point of a
    colour is that CSE 434 is the same colour tomorrow."""
    if not name:
        return 210
    h = 0
    for ch in str(name):
        h = (h * 31 + ord(ch)) % 360
    return (h * 47) % 360


def swatch(name):
    return 'style="--hue:%d"' % hue(name)


def _blocks(day, is_today):
    """Positioned blocks for one day, as fractions of the fixed span.

    Percentages rather than pixels: the viewport is a fixed height in every
    look, but not the *same* fixed height, so the same builder serves a 420px
    rail and a 620px hero without a second set of numbers.
    """
    _, timed = today_split(day)
    laned, nlanes = _lanes(timed)
    notes = events.event_note_map()
    span = DAY_HI - DAY_LO
    now = datetime.datetime.now()
    nowm = now.hour * 60 + now.minute if is_today else None
    out = []
    for b in laned:
        e = b['ev']
        s = max(b['s'], DAY_LO)
        en = min(b['e'], DAY_HI)
        if en <= DAY_LO or s >= DAY_HI:
            continue
        rec = notes.get((e.get('uid', ''), core._fmt_dt(e['start'])))
        out.append({
            'ev': e, 'rec': rec,
            'top': (s - DAY_LO) * 100.0 / span,
            'h': max(en - s, 18) * 100.0 / span,
            'lane': b['lane'], 'nlanes': nlanes,
            'past': bool(nowm is not None and b['e'] <= nowm),
            'live': bool(nowm is not None and b['s'] <= nowm < b['e']),
            'span': '%02d:%02d–%02d:%02d' % (b['s'] // 60, b['s'] % 60,
                                                  b['e'] // 60, b['e'] % 60),
            'mins': b['e'] - b['s'],
        })
    nowpct = ((nowm - DAY_LO) * 100.0 / span
              if nowm is not None and DAY_LO <= nowm <= DAY_HI else None)
    return out, nowpct


def _hours():
    return [(m, (m - DAY_LO) * 100.0 / (DAY_HI - DAY_LO))
            for m in range(DAY_LO, DAY_HI + 1, 60)]


def _blk_acts(e, rec):
    """The same actions the live grid offers. Copied in shape, not in markup:
    each look styles them itself, and every one of them is a real link that
    already works on the current page."""
    acts = []
    if rec:
        acts.append('<a class="act" href="obsidian://open?vault=%s&amp;file=%s">%s</a>'
                    % (urllib.parse.quote(os.path.basename(core.VAULT)),
                       urllib.parse.quote('Events/' + rec['title']),
                       'write the debrief' if rec['state'] == 'due' else 'open note'))
    j = _join_link(e)
    if j:
        acts.append('<a class="act join" href="%s" target="_blank" rel="noopener">join</a>' % esc(j))
    g = _gcal_link(e, False)
    if g:
        acts.append('<a class="act" href="%s" target="_blank" rel="noopener">calendar</a>' % g)
    return ''.join(acts)


def _page(title, css, body, extra_js=''):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s%s</style></head><body>%s'
            '<script>%s%s</script></body></html>'
            % (esc(title), BASE_CSS, css, body, TICKJS, SCROLLJS + extra_js))


# Scroll the day column to now on load. Every look has one, and none of them
# should open showing 06:00 when it is four in the afternoon.
SCROLLJS = """
document.querySelectorAll('[data-scrollnow]').forEach(el=>{
  const n=el.querySelector('.nowline');
  const t=n?n.offsetTop:(el.querySelector('.blk')||{}).offsetTop;
  if(t!=null) el.scrollTop=Math.max(0,t-el.clientHeight*0.35);
});
document.addEventListener('click',e=>{
  const b=e.target.closest('.blk'); if(!b||e.target.closest('a')) return;
  b.classList.toggle('open');
});
document.querySelectorAll('[data-tabs]').forEach(w=>{
  w.addEventListener('click',e=>{
    const t=e.target.closest('[data-tab]'); if(!t) return;
    w.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('on',x===t));
    w.querySelectorAll('[data-pane]').forEach(p=>p.hidden = p.dataset.pane!==t.dataset.tab);
  });
});
"""

# Shared skeleton only -- palette, reset, the tick box, the row. Everything that
# makes a look *a look* lives in its own sheet, so two contenders can disagree
# about density and colour without either editing the other.
BASE_CSS = """
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:inherit}
ul{list-style:none;margin:0;padding:0}
.tick{flex:none;width:17px;height:17px;margin-top:2px;border:1.5px solid currentColor;opacity:.55;
  border-radius:5px;background:none;color:inherit;cursor:pointer;font-size:11px;line-height:1;
  padding:0;display:flex;align-items:center;justify-content:center}
.tick:hover{opacity:1}
li.crossed{opacity:.45}
li.crossed .rowtitle{text-decoration:line-through}
.rowbody{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1}
.rowtitle{line-height:1.3;overflow-wrap:anywhere}
.rowmeta{font-size:11px;opacity:.65;font-variant-numeric:tabular-nums}
.dot{width:7px;height:7px;border-radius:50%;flex:none;
  background:hsl(var(--hue),62%,55%)}
.looknav{display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:12px;padding:10px 0 0}
.looknav a{text-decoration:none;opacity:.6;padding:3px 9px;border-radius:99px;border:1px solid currentColor}
.looknav a.on,.looknav a:hover{opacity:1}
[hidden]{display:none!important}
"""


def looknav(n):
    links = ''.join('<a class="%s" href="/look/%d">%d</a>'
                    % ('on' if i == n else '', i, i) for i in range(1, 6))
    return ('<nav class="looknav"><a href="/">&larr; live dashboard</a>'
            '<span style="opacity:.4">contenders</span>%s'
            '<span style="opacity:.5;margin-left:auto">%s</span></nav>'
            % (links, esc(LOOKS[n][0])))


# ================================================================ 1. Rail

RAIL_CSS = """
:root{--bg:#f7f6f3;--fg:#191817;--dim:#6f6a62;--line:#e4e0d8;--card:#fff;--accent:#8c1d40}
@media(prefers-color-scheme:dark){:root{--bg:#131211;--fg:#eeeae3;--dim:#948d83;
  --line:#2b2825;--card:#1b1a18;--accent:#f0839f}}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:1240px;margin:0 auto;padding:18px 22px 50px}
h1{font-size:19px;margin:0;font-weight:600;letter-spacing:-.01em}
h1 small{font-weight:400;color:var(--dim);font-size:13px;margin-left:8px}
.strip{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;margin:16px 0 18px}
.sday{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 10px 9px;
  text-decoration:none;display:block;position:relative;overflow:hidden}
.sday:hover{border-color:var(--accent)}
.sday.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.sday b{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--dim);display:block}
.sday .num{font-size:21px;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.1}
.sday.on .num{color:var(--accent)}
.heat{display:flex;gap:2px;margin-top:6px;height:4px}
.heat i{flex:1;border-radius:2px;background:hsl(var(--hue),62%,55%)}
.sday .none{opacity:.3;font-size:11px;margin-top:4px;display:block}
.cols{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:18px}
@media(max-width:940px){.cols{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.panel h2{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
  margin:0 0 10px;font-weight:600;display:flex;align-items:center;gap:8px}
.panel h2 .n{margin-left:auto;font-variant-numeric:tabular-nums;opacity:.8}
/* The fix: a fixed-height viewport that scrolls, not a column that grows. */
.daycol{height:460px;overflow-y:auto;position:relative;
  -webkit-mask-image:linear-gradient(#0000,#000 14px,#000 calc(100% - 14px),#0000);
  mask-image:linear-gradient(#0000,#000 14px,#000 calc(100% - 14px),#0000)}
.daybody{position:relative;height:1020px}
.hr{position:absolute;left:0;right:0;border-top:1px solid var(--line)}
.hr span{position:absolute;top:-8px;left:0;font-size:10px;color:var(--dim);
  font-variant-numeric:tabular-nums;background:var(--card);padding-right:6px}
.nowline{position:absolute;left:42px;right:0;border-top:2px solid var(--accent);z-index:4}
.nowline:after{content:'';position:absolute;left:-5px;top:-4px;width:7px;height:7px;
  border-radius:50%;background:var(--accent)}
.blk{position:absolute;border-radius:8px;padding:5px 9px;overflow:hidden;cursor:pointer;z-index:2;
  background:hsl(var(--hue),62%,55%,.13);border:1px solid hsl(var(--hue),62%,55%,.45);
  border-left:3px solid hsl(var(--hue),62%,52%);display:flex;flex-direction:column}
@media(prefers-color-scheme:dark){.blk{background:hsl(var(--hue),50%,52%,.18)}}
.blk.live{box-shadow:0 0 0 1px var(--accent),0 6px 20px rgba(0,0,0,.14)}
.blk.past{opacity:.45}
.blk.open{height:auto!important;min-height:var(--h);z-index:9;overflow:visible;
  box-shadow:0 10px 30px rgba(0,0,0,.22)}
.bt{font-size:13px;font-weight:600;line-height:1.2;flex:none}
.bm{font-size:10.5px;color:var(--dim);font-variant-numeric:tabular-nums;flex:none}
.why{font-size:11px;color:var(--dim);line-height:1.35;margin-top:3px;flex:1;min-height:0;
  overflow:hidden;-webkit-mask-image:linear-gradient(#000 calc(100% - 10px),transparent);
  mask-image:linear-gradient(#000 calc(100% - 10px),transparent)}
.blk.open .why{flex:none;overflow:visible;-webkit-mask-image:none;mask-image:none}
.why p{margin:0 0 4px}
.acts{display:none;gap:5px;flex-wrap:wrap;margin-top:6px;padding-top:6px;
  border-top:1px solid var(--line)}
.blk.open .acts{display:flex}
.act{font-size:10.5px;border:1px solid var(--line);border-radius:5px;padding:2px 7px;
  color:var(--accent);text-decoration:none;white-space:nowrap;background:var(--card)}
.act:hover{border-color:var(--accent)}
li.row{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid var(--line);align-items:flex-start}
li.row:last-child{border-bottom:0}
.worklist{max-height:460px;overflow-y:auto}
.pill{font-size:10px;text-transform:uppercase;letter-spacing:.05em;border-radius:99px;
  padding:1px 7px;background:hsl(var(--hue),62%,55%,.16);color:hsl(var(--hue),55%,38%);
  border:1px solid hsl(var(--hue),62%,55%,.35);white-space:nowrap}
@media(prefers-color-scheme:dark){.pill{color:hsl(var(--hue),70%,72%)}}
.od{color:#c0521c;font-weight:600}
.empty{color:var(--dim);font-size:13px;padding:6px 0}
.flag{color:#c0521c;font-size:13px;padding:3px 0}
"""


def look_rail(day=None):
    """1 -- Rail. The week becomes navigation; the list stays one list.

    The redundancy is cut by *demoting* the week grid: it is a seven-chip strip
    of counts, and clicking a chip changes which day the rail shows. It carries
    no assignment titles at all, so the only place an assignment's name appears
    is the worklist beside it. Canvas work and `Tasks/` lines are interleaved in
    that one ranked list rather than split into two half-width columns, because
    "what do I do next" does not care which system a thing came from.
    """
    day = day or core.TODAY.isoformat()
    is_today = day == core.TODAY.isoformat()
    blocks, nowpct = _blocks(day, is_today)
    wk = week_canvas(day)
    mon = datetime.date(*map(int, wk['monday'].split('-')))

    chips = []
    for i in range(7):
        d = (mon + datetime.timedelta(days=i)).isoformat()
        items = wk['days'][d]
        openn = [it for it in items if not it['done']]
        heat = ''.join('<i style="--hue:%d"></i>' % hue(it['tag']) for it in openn[:6])
        chips.append('<a class="sday %s" href="/look/1?day=%s"><b>%s</b>'
                     '<span class="num">%s</span>%s</a>'
                     % ('on' if d == day else '', d, DOW[i], d[8:10],
                        '<span class="heat">%s</span>' % heat if heat
                        else '<span class="none">clear</span>'))

    grid = ''.join('<div class="hr" style="top:%.2f%%"><span>%02d:00</span></div>'
                   % (p, m // 60) for m, p in _hours())
    if nowpct is not None:
        grid += '<div class="nowline" style="top:%.2f%%"></div>' % nowpct
    for b in blocks:
        e, rec = b['ev'], b['rec']
        why = ''
        if rec and rec.get('why'):
            why = '<div class="why">%s</div>' % ''.join(
                '<p>%s</p>' % esc(x) for x in (rec.get('why_all') or [rec['why']]))
        grid += ('<div class="blk%s%s" %s style="top:%.2f%%;--h:%.2f%%;height:%.2f%%;'
                 'left:calc(46px + %.3f%%);width:calc(%.3f%% - 50px)">'
                 '<div class="bt">%s</div><div class="bm">%s &middot; %s</div>%s'
                 '<div class="acts">%s</div></div>'
                 % (' past' if b['past'] else '', ' live' if b['live'] else '',
                    swatch(e['label']), b['top'], b['h'], b['h'],
                    b['lane'] * 100.0 / b['nlanes'], 100.0 / b['nlanes'],
                    esc(e['summary']), b['span'], core._dur(b['mins']), why,
                    _blk_acts(e, rec)))
    if not blocks:
        grid += ('<p class="empty" style="position:absolute;top:46%;left:50px">'
                 'Nothing scheduled.</p>')

    top, _ = ranked(limit=14)
    rows = []
    for it in top:
        meta = []
        if it['due']:
            meta.append('<span class="%s">%s</span>'
                        % ('od' if it['overdue'] else '', it['due'][5:]))
        if it.get('points'):
            meta.append('%s pts' % esc(str(it['points'])))
        if it.get('elsewhere'):
            meta.append('on %s' % esc(it['elsewhere']))
        title = esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', it['title']))
        if it['url']:
            title = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
        rows.append('<li class="row%s"><button class="tick" data-key="%s">%s</button>'
                    '<span class="rowbody"><span class="rowtitle">%s</span>'
                    '<span class="rowmeta">%s</span></span>'
                    '<span class="pill" %s>%s</span></li>'
                    % (' crossed' if it['done'] else '', esc(it['key']),
                       '&#10003;' if it['done'] else '', title,
                       ' &middot; '.join(meta), swatch(it['tag']),
                       esc(it['tag'] or ('task' if it['source'] == 'task' else '?'))))

    fl = flags()
    body = ('<div class="wrap">%s<h1>%s<small>%s</small></h1>'
            '<div class="strip">%s</div><div class="cols">'
            '<div class="panel"><h2>Schedule<span class="n">%d</span></h2>'
            '<div class="daycol" data-scrollnow><div class="daybody">%s</div></div></div>'
            '<div class="panel"><h2>Work queue<span class="n">%d</span></h2>'
            '<ul class="worklist">%s</ul>%s</div></div>'
            '%s</div>'
            % (looknav(1),
               esc(datetime.date(*map(int, day.split('-'))).strftime('%A %d %B')),
               'today' if is_today else 'browsing',
               ''.join(chips), len(blocks), grid, len(top),
               ''.join(rows) or '<p class="empty">Nothing outstanding.</p>',
               '',
               ('<div class="panel" style="margin-top:18px"><h2>Flags</h2>%s</div>'
                % ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)) if fl else ''))
    return _page('Look 1 - Rail', RAIL_CSS, body)


LOOKS[1] = ('Rail', 'week as navigation, one ranked list', look_rail)


# ================================================================ 2. Week-first

WEEK_CSS = """
:root{--bg:#f6f7f9;--fg:#14171c;--dim:#697080;--line:#e3e6ec;--card:#fff;--accent:#2f6df0}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e9ecf2;--dim:#8d95a6;
  --line:#242832;--card:#171a20;--accent:#6f9bff}}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:1360px;margin:0 auto;padding:18px 22px 50px}
h1{font-size:20px;margin:0 0 2px;font-weight:650;letter-spacing:-.015em}
.ribbon{display:flex;gap:10px;margin:14px 0 16px;flex-wrap:wrap}
.nx{flex:1 1 220px;min-width:0;border-radius:12px;padding:10px 13px;color:#fff;
  background:linear-gradient(135deg,hsl(var(--hue),68%,52%),hsl(var(--hue),68%,40%));
  box-shadow:0 4px 14px hsl(var(--hue),60%,45%,.28);text-decoration:none;display:block}
.nx b{display:block;font-size:14px;line-height:1.25;font-weight:600}
.nx span{font-size:11px;opacity:.85;font-variant-numeric:tabular-nums}
.nx.calm{background:var(--card);color:var(--fg);border:1px solid var(--line);box-shadow:none}
.wk{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px}
@media(max-width:1000px){.wk{grid-template-columns:repeat(2,1fr)}}
.col{background:var(--card);border:1px solid var(--line);border-radius:13px;
  display:flex;flex-direction:column;overflow:hidden;height:440px}
.col.today{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 8px 26px rgba(47,109,240,.14)}
.col.past{opacity:.5}
.ch{padding:9px 11px 8px;border-bottom:1px solid var(--line);display:flex;align-items:baseline;gap:6px}
.ch b{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
.col.today .ch b{color:var(--accent)}
.ch .num{font-size:17px;font-weight:650;font-variant-numeric:tabular-nums;margin-left:auto}
.cb{flex:1;overflow-y:auto;padding:4px 11px 10px}
li.row{display:flex;gap:8px;padding:7px 0;border-bottom:1px solid var(--line);align-items:flex-start}
li.row:last-child{border-bottom:0}
.rowtitle{font-size:12.5px}
.tt{font-size:10.5px;padding:1px 6px;border-radius:99px;white-space:nowrap;
  background:hsl(var(--hue),62%,55%,.16);color:hsl(var(--hue),55%,36%)}
@media(prefers-color-scheme:dark){.tt{color:hsl(var(--hue),70%,74%)}}
.cempty{color:var(--dim);font-size:12px;opacity:.5;padding:8px 0}
/* Today's column carries the schedule instead of assignments, at the same
   fixed height as its six neighbours -- so a heavy day cannot stretch the row. */
.sched{position:relative;height:100%;min-height:760px}
.hr{position:absolute;left:0;right:0;border-top:1px solid var(--line)}
.hr span{position:absolute;top:-7px;left:0;font-size:9.5px;color:var(--dim);background:var(--card);
  padding-right:4px;font-variant-numeric:tabular-nums}
.nowline{position:absolute;left:34px;right:0;border-top:2px solid var(--accent);z-index:4}
.blk{position:absolute;left:36px;right:2px;border-radius:7px;padding:4px 7px;overflow:hidden;
  background:hsl(var(--hue),66%,54%,.15);border-left:3px solid hsl(var(--hue),66%,50%);
  font-size:11.5px;cursor:pointer;z-index:2}
.blk.past{opacity:.45}
.blk.open{height:auto!important;z-index:9;background:var(--card);
  box-shadow:0 8px 24px rgba(0,0,0,.2);border:1px solid var(--accent);border-left:3px solid var(--accent)}
.bt{font-weight:600;line-height:1.2}
.bm{font-size:10px;color:var(--dim);font-variant-numeric:tabular-nums}
.acts{display:none;gap:5px;flex-wrap:wrap;margin-top:5px}
.blk.open .acts{display:flex}
.act{font-size:10px;border:1px solid var(--line);border-radius:5px;padding:1px 6px;
  color:var(--accent);text-decoration:none}
.tray{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-top:16px}
@media(max-width:900px){.tray{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:13px 15px}
.panel h2{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
  margin:0 0 8px;font-weight:600}
.carry{border-left:3px solid #d9822b}
.flag{color:#d9822b;font-size:13px;padding:3px 0}
.od{color:#d9822b;font-weight:600}
.sum{margin:12px 0 0;font-size:12px;color:var(--dim);text-align:right;font-variant-numeric:tabular-nums}
"""


def look_week(week=None):
    """2 -- Week-first. The seven-day grid is the page.

    The week grid is the thing he said he likes, so here it is the only list of
    assignments on the page: no "due today" column, no ranked sidebar repeating
    the same six rows. Today's column swaps its assignment list for the
    schedule, which puts the two calendars he likes side by side in one object
    and gives the meeting column a fixed height for free -- it is a seventh of a
    row whose height is set by the row, not by the bookings.

    What survives of ranking is the ribbon: the three most pressing things,
    stated once at the top. `Tasks/` lines live in the tray below, because they
    have no day.
    """
    wk = week_canvas(week)
    mon = datetime.date(*map(int, wk['monday'].split('-')))
    today = core.TODAY.isoformat()
    top, allitems = ranked(limit=3)

    ribbon = []
    for it in top:
        title = esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', it['title']))
        meta = ' &middot; '.join(x for x in [
            esc(it['tag']), ('due %s' % it['due'][5:]) if it['due'] else '',
            'overdue' if it['overdue'] else ''] if x)
        ribbon.append('<a class="nx" %s href="%s" target="_blank" rel="noopener">'
                      '<b>%s</b><span>%s</span></a>'
                      % (swatch(it['tag']), esc(it['url'] or '#'), title, meta))
    while len(ribbon) < 3:
        ribbon.append('<div class="nx calm"><b>Clear</b><span>nothing pressing</span></div>')

    cols = []
    for i in range(7):
        d = (mon + datetime.timedelta(days=i)).isoformat()
        items = wk['days'][d]
        openn = sum(1 for it in items if not it['done'])
        head = ('<div class="ch"><b>%s</b><span class="num">%s</span></div>'
                % (DOW[i], d[8:10]))
        if d == today:
            blocks, nowpct = _blocks(d, True)
            inner = ''.join('<div class="hr" style="top:%.2f%%"><span>%02d</span></div>'
                            % (p, m // 60) for m, p in _hours())
            if nowpct is not None:
                inner += '<div class="nowline" style="top:%.2f%%"></div>' % nowpct
            for b in blocks:
                e = b['ev']
                inner += ('<div class="blk%s" %s style="top:%.2f%%;height:%.2f%%">'
                          '<div class="bt">%s</div><div class="bm">%s</div>'
                          '<div class="acts">%s</div></div>'
                          % (' past' if b['past'] else '', swatch(e['label']),
                             b['top'], b['h'], esc(e['summary']), b['span'],
                             _blk_acts(e, b['rec'])))
            body = ('<div class="cb" data-scrollnow><div class="sched">%s</div></div>'
                    % (inner or '<p class="cempty">Nothing scheduled.</p>'))
        else:
            rows = ''
            for it in items:
                title = esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', it['title']))
                if it['url']:
                    title = ('<a href="%s" target="_blank" rel="noopener">%s</a>'
                             % (esc(it['url']), title))
                rows += ('<li class="row%s"><button class="tick" data-key="%s">%s</button>'
                         '<span class="rowbody"><span class="rowtitle">%s</span>'
                         '<span class="rowmeta"><span class="tt" %s>%s</span></span>'
                         '</span></li>'
                         % (' crossed' if it['done'] else '', esc(it['key']),
                            '&#10003;' if it['done'] else '', title,
                            swatch(it['tag']), esc(it['tag'])))
            body = ('<div class="cb"><ul>%s</ul></div>' % rows if rows
                    else '<div class="cb"><p class="cempty">&mdash;</p></div>')
        cols.append('<div class="col%s%s">%s%s</div>'
                    % (' today' if d == today else '', ' past' if d < today else '',
                       head, body))

    tasks = [i for i in allitems if i['source'] == 'task'][:10]
    trows = ''
    for t in tasks:
        trows += ('<li class="row%s"><button class="tick" data-key="%s">%s</button>'
                  '<span class="rowbody"><span class="rowtitle">%s</span>'
                  '<span class="rowmeta">%s</span></span></li>'
                  % (' crossed' if t['done'] else '', esc(t['key']),
                     '&#10003;' if t['done'] else '',
                     esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', t['title'])),
                     ' &middot; '.join(x for x in [esc(t['tag']),
                                                   t['due'][5:] if t['due'] else ''] if x)))
    fl = flags()
    allit = [it for dd in wk['days'].values() for it in dd]
    openit = [it for it in allit if not it['done']]
    carry = ''
    if wk['carried']:
        carry = ('<div class="panel carry" style="margin:0 0 14px"><h2>Carried in &middot; %d</h2>'
                 '<ul>%s</ul></div>'
                 % (len(wk['carried']),
                    ''.join('<li class="row"><button class="tick" data-key="%s"></button>'
                            '<span class="rowbody"><span class="rowtitle">%s</span>'
                            '<span class="rowmeta od">was due %s &middot; %s</span></span></li>'
                            % (esc(it['key']), esc(it['title']), it['due'][5:], esc(it['tag']))
                            for it in wk['carried'])))

    body = ('<div class="wrap">%s<h1>Week of %s</h1>'
            '<div class="ribbon">%s</div>%s<div class="wk">%s</div>'
            '<p class="sum">%d due this week &middot; %d still open</p>'
            '<div class="tray"><div class="panel"><h2>Tasks</h2><ul>%s</ul></div>'
            '<div class="panel"><h2>Flags</h2>%s</div></div></div>'
            % (looknav(2), esc(mon.strftime('%d %B')), ''.join(ribbon), carry,
               ''.join(cols), len(allit), len(openit),
               trows or '<p class="cempty">No open tasks.</p>',
               ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)
               or '<p class="cempty">Clean.</p>'))
    return _page('Look 2 - Week-first', WEEK_CSS, body)


LOOKS[2] = ('Week-first', 'the seven-day grid is the page', look_week)


# ================================================================ 3. Console

CONSOLE_CSS = """
:root{--bg:#0c0d10;--fg:#e6e9ef;--dim:#7b8496;--line:#1e2128;--card:#131519;
  --accent:#5eead4;--hot:#fb7185;--warm:#fbbf24}
body{background:var(--bg);color:var(--fg);
  background-image:radial-gradient(900px 500px at 12% -8%,rgba(94,234,212,.07),transparent),
                   radial-gradient(800px 460px at 92% 4%,rgba(129,140,248,.07),transparent)}
.wrap{max-width:1300px;margin:0 auto;padding:16px 20px 44px}
.top{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
h1{font:600 18px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0;letter-spacing:-.01em}
.stat{display:flex;gap:8px;margin-left:auto;flex-wrap:wrap}
.st{border:1px solid var(--line);border-radius:8px;padding:4px 10px;background:var(--card);
  font:11px/1.4 ui-monospace,Menlo,monospace;color:var(--dim)}
.st b{color:var(--fg);font-size:15px;font-weight:600;display:block;font-variant-numeric:tabular-nums}
.st.hot b{color:var(--hot)}
.split{display:grid;grid-template-columns:minmax(0,420px) minmax(0,1fr);gap:14px;margin-top:14px}
@media(max-width:920px){.split{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.ph{padding:10px 13px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center;
  font:11px/1 ui-monospace,Menlo,monospace;letter-spacing:.09em;text-transform:uppercase;color:var(--dim)}
.ph .n{margin-left:auto;color:var(--accent)}
.tabs{display:flex;gap:4px;margin-left:auto}
.tab{font:11px/1 ui-monospace,Menlo,monospace;text-transform:uppercase;letter-spacing:.08em;
  background:none;border:1px solid var(--line);color:var(--dim);border-radius:99px;
  padding:5px 11px;cursor:pointer}
.tab.on{color:var(--bg);background:var(--accent);border-color:var(--accent)}
.tab:hover:not(.on){color:var(--fg)}
/* Fixed-height viewport. Both panes are the same height, so switching tabs
   never moves the page. */
.pane{height:520px;overflow-y:auto;padding:10px 13px 14px}
.daycol{height:520px;overflow-y:auto;position:relative;padding:0 13px}
.daybody{position:relative;height:1100px}
.hr{position:absolute;left:0;right:0;border-top:1px dashed var(--line)}
.hr span{position:absolute;top:-8px;left:0;font:10px/1 ui-monospace,Menlo,monospace;
  color:var(--dim);background:var(--card);padding-right:5px}
.nowline{position:absolute;left:40px;right:0;border-top:1px solid var(--hot);z-index:4;
  box-shadow:0 0 12px var(--hot)}
.nowline:after{content:'';position:absolute;left:-5px;top:-3.5px;width:7px;height:7px;
  border-radius:50%;background:var(--hot)}
.blk{position:absolute;border-radius:7px;padding:5px 9px;overflow:hidden;cursor:pointer;z-index:2;
  background:hsl(var(--hue),55%,55%,.14);border:1px solid hsl(var(--hue),60%,60%,.35);
  border-left:3px solid hsl(var(--hue),70%,62%);display:flex;flex-direction:column}
.blk.live{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 0 22px rgba(94,234,212,.18)}
.blk.past{opacity:.38}
.blk.open{height:auto!important;min-height:var(--h);z-index:9;overflow:visible;
  box-shadow:0 12px 34px rgba(0,0,0,.6)}
.bt{font:600 12.5px/1.25 -apple-system,BlinkMacSystemFont,sans-serif;flex:none}
.bm{font:10px/1.4 ui-monospace,Menlo,monospace;color:var(--dim);flex:none}
.why{font-size:11px;color:var(--dim);line-height:1.35;margin-top:3px;flex:1;min-height:0;overflow:hidden;
  -webkit-mask-image:linear-gradient(#000 calc(100% - 10px),transparent);
  mask-image:linear-gradient(#000 calc(100% - 10px),transparent)}
.blk.open .why{flex:none;overflow:visible;-webkit-mask-image:none;mask-image:none}
.why p{margin:0 0 4px}
.acts{display:none;gap:5px;flex-wrap:wrap;margin-top:6px;padding-top:6px;border-top:1px solid var(--line)}
.blk.open .acts{display:flex}
.act{font:10px/1.5 ui-monospace,Menlo,monospace;border:1px solid var(--line);border-radius:5px;
  padding:2px 7px;color:var(--accent);text-decoration:none}
.act:hover{border-color:var(--accent)}
li.row{display:flex;gap:9px;padding:8px 0;border-bottom:1px solid var(--line);align-items:flex-start}
li.row:last-child{border-bottom:0}
.rowmeta{font-family:ui-monospace,Menlo,monospace}
.tt{font:10px/1.5 ui-monospace,Menlo,monospace;padding:0 6px;border-radius:4px;white-space:nowrap;
  background:hsl(var(--hue),55%,55%,.16);color:hsl(var(--hue),70%,72%);
  border:1px solid hsl(var(--hue),55%,55%,.3)}
.od{color:var(--hot)}
.wrowgrid{display:grid;grid-template-columns:76px 1fr;gap:0 12px}
.wd{font:11px/1 ui-monospace,Menlo,monospace;text-transform:uppercase;letter-spacing:.08em;
  color:var(--dim);padding:10px 0 0;border-top:1px solid var(--line)}
.wd.on{color:var(--accent)}
.wdlist{border-top:1px solid var(--line);padding-bottom:2px}
.empty{color:var(--dim);font-size:12.5px;padding:8px 0;opacity:.6}
.flag{color:var(--warm);font-size:12.5px;padding:3px 0;font-family:ui-monospace,Menlo,monospace}
"""


def look_console(day=None):
    """3 -- Console. Same region, three tabs, one height.

    The redundancy is answered by putting the three lists in the *same* space
    and letting him choose which one is showing: **Due today**, **This week**
    and **Tasks** are tabs over one 520px pane, not three cards stacked down the
    page. The timeline beside it is a matching 520px viewport. Nothing on this
    page changes height, ever -- switching tabs, opening a block and crossing a
    row off all happen inside a fixed frame.

    Dark, monospaced and saturated on purpose: this is the least dull of the
    five, and the furthest from what is live now.
    """
    day = day or core.TODAY.isoformat()
    is_today = day == core.TODAY.isoformat()
    blocks, nowpct = _blocks(day, is_today)
    wk = week_canvas(day)
    mon = datetime.date(*map(int, wk['monday'].split('-')))
    top, allitems = ranked(limit=40)

    grid = ''.join('<div class="hr" style="top:%.2f%%"><span>%02d:00</span></div>'
                   % (p, m // 60) for m, p in _hours())
    if nowpct is not None:
        grid += '<div class="nowline" style="top:%.2f%%"></div>' % nowpct
    for b in blocks:
        e, rec = b['ev'], b['rec']
        why = ''
        if rec and rec.get('why'):
            why = '<div class="why">%s</div>' % ''.join(
                '<p>%s</p>' % esc(x) for x in (rec.get('why_all') or [rec['why']]))
        grid += ('<div class="blk%s%s" %s style="top:%.2f%%;--h:%.2f%%;height:%.2f%%;'
                 'left:calc(44px + %.3f%%);width:calc(%.3f%% - 48px)">'
                 '<div class="bt">%s</div><div class="bm">%s &middot; %s</div>%s'
                 '<div class="acts">%s</div></div>'
                 % (' past' if b['past'] else '', ' live' if b['live'] else '',
                    swatch(e['label']), b['top'], b['h'], b['h'],
                    b['lane'] * 100.0 / b['nlanes'], 100.0 / b['nlanes'],
                    esc(e['summary']), b['span'], core._dur(b['mins']), why,
                    _blk_acts(e, rec)))

    def row(it, showdue=True):
        title = esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', it['title']))
        if it['url']:
            title = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
        meta = []
        if showdue and it['due']:
            meta.append('<span class="%s">%s</span>'
                        % ('od' if it['overdue'] else '', it['due'][5:]))
        if it.get('at') and it['at'] not in ('', '23:59'):
            meta.append(esc(it['at']))
        if it.get('points'):
            meta.append('%spts' % esc(str(it['points'])))
        if it.get('elsewhere'):
            meta.append('on %s' % esc(it['elsewhere']))
        return ('<li class="row%s"><button class="tick" data-key="%s">%s</button>'
                '<span class="rowbody"><span class="rowtitle">%s</span>'
                '<span class="rowmeta">%s</span></span>'
                '<span class="tt" %s>%s</span></li>'
                % (' crossed' if it['done'] else '', esc(it['key']),
                   '&#10003;' if it['done'] else '', title, ' &middot; '.join(meta),
                   swatch(it['tag']), esc(it['tag'] or 'task')))

    today_iso = core.TODAY.isoformat()
    due = [i for i in allitems if i['source'] == 'canvas' and i['due'] == today_iso]
    tasks = [i for i in allitems if i['source'] == 'task']

    wkhtml = ['<div class="wrowgrid">']
    for i in range(7):
        d = (mon + datetime.timedelta(days=i)).isoformat()
        items = wk['days'][d]
        wkhtml.append('<div class="wd%s">%s %s</div><div class="wdlist">%s</div>'
                      % (' on' if d == today_iso else '', DOW[i], d[8:10],
                         ('<ul>%s</ul>' % ''.join(row(it, showdue=False) for it in items))
                         if items else '<p class="empty">&mdash;</p>'))
    wkhtml.append('</div>')

    fl = flags()
    try:
        sc, _det = metrics.compute_score()
    except Exception:
        sc = {}
    stats = ''.join(
        '<div class="st%s"><b>%s</b>%s</div>'
        % (' hot' if k in ('tasks_overdue', 'projects_drifting') and sc.get(k) else '',
           esc(str(sc.get(k, '-'))), k.replace('_', ' '))
        for k in ('stall_days_max', 'projects_drifting', 'tasks_open', 'tasks_overdue'))

    body = ('<div class="wrap">%s<div class="top"><h1>%s</h1>'
            '<div class="stat">%s</div></div><div class="split">'
            '<div class="panel"><div class="ph">timeline<span class="n">%d</span></div>'
            '<div class="daycol" data-scrollnow><div class="daybody">%s</div></div></div>'
            '<div class="panel" data-tabs><div class="ph">work'
            '<div class="tabs"><button class="tab on" data-tab="due">due today &middot; %d</button>'
            '<button class="tab" data-tab="week">this week &middot; %d</button>'
            '<button class="tab" data-tab="tasks">tasks &middot; %d</button>'
            '<button class="tab" data-tab="flags">flags &middot; %d</button></div></div>'
            '<div class="pane" data-pane="due"><ul>%s</ul></div>'
            '<div class="pane" data-pane="week" hidden>%s</div>'
            '<div class="pane" data-pane="tasks" hidden><ul>%s</ul></div>'
            '<div class="pane" data-pane="flags" hidden>%s</div>'
            '</div></div></div>'
            % (looknav(3), esc(core.TODAY.strftime('%a %d %b').lower()), stats,
               len(blocks), grid or '<p class="empty">Nothing scheduled.</p>',
               len(due), sum(len(v) for v in wk['days'].values()), len(tasks), len(fl),
               ''.join(row(i) for i in due) or '<p class="empty">Nothing due today.</p>',
               ''.join(wkhtml),
               ''.join(row(i) for i in tasks) or '<p class="empty">No open tasks.</p>',
               ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)
               or '<p class="empty">Clean.</p>'))
    return _page('Look 3 - Console', CONSOLE_CSS, body)


LOOKS[3] = ('Console', 'dark, tabbed, nothing ever changes height', look_console)


# ================================================================ 4. Stream

STREAM_CSS = """
:root{--bg:#fffdfa;--fg:#201c18;--dim:#7a7069;--line:#ece5dc;--card:#fff;--accent:#c2410c;
  --ok:#15803d}
@media(prefers-color-scheme:dark){:root{--bg:#141210;--fg:#f0eae3;--dim:#9b9289;
  --line:#2c2823;--card:#1c1a17;--accent:#fb923c;--ok:#4ade80}}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:900px;margin:0 auto;padding:24px 22px 56px}
h1{font-size:30px;margin:0 0 2px;font-weight:700;letter-spacing:-.025em}
.lede{color:var(--dim);font-size:14px;margin:0 0 20px}
.lede b{color:var(--fg)}
/* Seven bars, not seven lists: the week is a shape here, and the detail lives
   in the stream below. */
.spark{display:grid;grid-template-columns:repeat(7,1fr);gap:7px;margin:0 0 24px;
  align-items:end;height:74px}
.sp{display:flex;flex-direction:column;justify-content:flex-end;height:100%;text-decoration:none}
.sp .bar{border-radius:6px 6px 3px 3px;background:linear-gradient(180deg,
  hsl(18,85%,58%),hsl(18,85%,48%));min-height:4px;transition:opacity .15s}
.sp.none .bar{background:var(--line)}
.sp.on .bar{background:linear-gradient(180deg,hsl(150,58%,45%),hsl(150,58%,35%))}
.sp:hover .bar{opacity:.75}
.sp .lb{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);
  text-align:center;padding-top:6px}
.sp.on .lb{color:var(--fg);font-weight:600}
.sp .ct{font-size:11px;text-align:center;font-variant-numeric:tabular-nums;color:var(--dim);
  padding-bottom:4px}
h2{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--dim);
  margin:26px 0 10px;font-weight:600;display:flex;align-items:center;gap:8px}
h2:after{content:'';flex:1;border-top:1px solid var(--line)}
/* The stream: meetings and deadlines in one chronological list, so a 16:30
   meeting and a 17:00 deadline read as the neighbours they are. Each card is
   its own height and the page is a normal document -- no viewport to overflow,
   because there is no grid to stretch. */
.ev{display:grid;grid-template-columns:62px 1fr;gap:14px;padding:11px 0;
  border-bottom:1px solid var(--line);align-items:start}
.ev:last-child{border-bottom:0}
.ev .when{font-variant-numeric:tabular-nums;font-size:13px;color:var(--dim);padding-top:2px;
  text-align:right}
.ev .when small{display:block;font-size:10.5px;opacity:.7}
.ev .card{border-left:3px solid hsl(var(--hue),62%,52%);background:var(--card);
  border-radius:0 10px 10px 0;padding:9px 13px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.ev.now .card{box-shadow:0 0 0 1px var(--accent),0 6px 20px rgba(194,65,12,.15)}
.ev.past{opacity:.45}
.ev.past .ttl{text-decoration:line-through}
.ttl{font-size:15px;font-weight:600;line-height:1.3}
.sub2{font-size:12px;color:var(--dim);margin-top:2px}
.why{font-size:12.5px;color:var(--dim);line-height:1.45;margin-top:6px;
  padding-top:6px;border-top:1px solid var(--line)}
.why p{margin:0 0 5px}
.why p:last-child{margin:0}
.acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.act{font-size:11px;border:1px solid var(--line);border-radius:6px;padding:3px 9px;
  color:var(--accent);text-decoration:none}
.act:hover{border-color:var(--accent)}
.ev.task .card{border-left-color:var(--dim);display:flex;gap:10px;align-items:flex-start}
.badge{font-size:10px;text-transform:uppercase;letter-spacing:.06em;padding:1px 7px;
  border-radius:99px;background:hsl(var(--hue),62%,52%,.14);color:hsl(var(--hue),55%,34%);
  white-space:nowrap}
@media(prefers-color-scheme:dark){.badge{color:hsl(var(--hue),70%,74%)}}
li.row{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid var(--line);align-items:flex-start}
li.row:last-child{border-bottom:0}
.od{color:var(--accent);font-weight:600}
.empty{color:var(--dim);font-size:13.5px;padding:10px 0}
.flag{color:var(--accent);font-size:13.5px;padding:4px 0}
"""


def look_stream(day=None):
    """4 -- Stream. One chronological column; the week is a shape, not a list.

    This is the look that argues the two calendars are the same calendar. A day
    is one column of *moments* -- an 11:45 deadline sits between a 10:30 class
    and a 16:30 meeting, which is how the day is actually experienced and what
    neither of the current cards can show. Height stops being a problem by
    construction: there is no absolute-positioned grid to stretch, so duration
    is stated (`45m`) rather than drawn.

    The trade is real and worth saying out loud: he loses the at-a-glance
    *shape* of the day -- a two-hour block no longer looks like two hours. The
    week keeps its shape instead, as seven bars.
    """
    day = day or core.TODAY.isoformat()
    is_today = day == core.TODAY.isoformat()
    allday, timed = today_split(day)
    wk = week_canvas(day)
    mon = datetime.date(*map(int, wk['monday'].split('-')))
    today_iso = core.TODAY.isoformat()
    notes = events.event_note_map()
    now = datetime.datetime.now().strftime('%H:%M')

    counts = []
    for i in range(7):
        d = (mon + datetime.timedelta(days=i)).isoformat()
        counts.append((d, sum(1 for it in wk['days'][d] if not it['done'])))
    peak = max([c for _, c in counts] + [1])
    spark = ''.join(
        '<a class="sp%s%s" href="/look/4?day=%s"><span class="ct">%s</span>'
        '<span class="bar" style="height:%d%%"></span><span class="lb">%s</span></a>'
        % (' on' if d == day else '', ' none' if not c else '', d,
           c or '', max(6, int(c * 100.0 / peak)), DOW[i])
        for i, (d, c) in enumerate(counts))

    # Meetings and Canvas deadlines, merged and sorted by clock.
    stream = []
    for e in timed:
        st = core._dt(e['start'])
        en = core._dt(e.get('end') or '')
        mins = int((en - st).total_seconds() // 60) if (st and en and en > st) else 0
        rec = notes.get((e.get('uid', ''), core._fmt_dt(e['start'])))
        stream.append({'t': e['time'][:5], 'kind': 'event', 'e': e, 'rec': rec,
                       'mins': mins, 'past': bool(is_today and e['time'][:5] < now)})
    for it in wk['days'].get(day, []):
        at = it['at'] or '23:59'
        stream.append({'t': at, 'kind': 'due', 'it': it,
                       'past': bool(it['done'])})
    stream.sort(key=lambda r: r['t'])

    rows = []
    for r in stream:
        if r['kind'] == 'event':
            e, rec = r['e'], r['rec']
            live = is_today and not r['past'] and e['time'][:5] <= now
            why = ''
            if rec and rec.get('why'):
                why = '<div class="why">%s</div>' % ''.join(
                    '<p>%s</p>' % esc(x) for x in (rec.get('why_all') or [rec['why']]))
            acts = _blk_acts(e, rec)
            meta = ' &middot; '.join(x for x in [
                core._dur(r['mins']) if r['mins'] else '',
                esc(e['loc'][:40]) if e['loc'] and 'http' not in e['loc'] else '',
                esc(e['label'])] if x)
            rows.append('<div class="ev%s%s" %s><div class="when">%s<small>%s</small></div>'
                        '<div class="card"><div class="ttl">%s</div>'
                        '<div class="sub2">%s</div>%s%s</div></div>'
                        % (' past' if r['past'] else '', ' now' if live else '',
                           swatch(e['label']), r['t'],
                           core._dur(r['mins']) if r['mins'] else '',
                           esc(e['summary']), meta, why,
                           '<div class="acts">%s</div>' % acts if acts else ''))
        else:
            it = r['it']
            title = esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', it['title']))
            if it['url']:
                title = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
            rows.append('<div class="ev task%s" %s><div class="when">%s<small>due</small></div>'
                        '<div class="card"><button class="tick" data-key="%s">%s</button>'
                        '<span class="rowbody"><span class="ttl rowtitle">%s</span>'
                        '<span class="sub2">%s</span></span>'
                        '<span class="badge">%s</span></div></div>'
                        % (' past' if it['done'] else '', swatch(it['tag']),
                           '' if it['at'] in ('', '23:59') else r['t'], esc(it['key']),
                           '&#10003;' if it['done'] else '', title,
                           ' &middot; '.join(x for x in [
                               '%s pts' % it['points'] if it.get('points') else '',
                               'on %s' % esc(it['elsewhere']) if it.get('elsewhere') else ''] if x),
                           esc(it['tag'])))

    _, allitems = ranked()
    tasks = [i for i in allitems if i['source'] == 'task'][:8]
    trows = ''.join(
        '<li class="row%s"><button class="tick" data-key="%s">%s</button>'
        '<span class="rowbody"><span class="rowtitle">%s</span>'
        '<span class="rowmeta">%s</span></span></li>'
        % (' crossed' if t['done'] else '', esc(t['key']),
           '&#10003;' if t['done'] else '',
           esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', t['title'])),
           ' &middot; '.join(x for x in [
               esc(t['tag']),
               '<span class="od">%s</span>' % t['due'][5:] if t['overdue']
               else (t['due'][5:] if t['due'] else '')] if x))
        for t in tasks)

    fl = flags()
    nmeet = len(timed)
    ndue = sum(1 for it in wk['days'].get(day, []) if not it['done'])
    body = ('<div class="wrap">%s<h1>%s</h1>'
            '<p class="lede"><b>%d</b> meeting%s &middot; <b>%d</b> due &middot; '
            '<b>%d</b> open task%s this week</p>'
            '<div class="spark">%s</div>'
            '<h2>The day</h2>%s'
            '<h2>No deadline</h2><ul>%s</ul>'
            '%s</div>'
            % (looknav(4),
               esc(datetime.date(*map(int, day.split('-'))).strftime('%A %d %B')),
               nmeet, '' if nmeet == 1 else 's', ndue,
               len(tasks), '' if len(tasks) == 1 else 's', spark,
               ''.join(rows) or '<p class="empty">Nothing on the clock today.</p>',
               trows or '<li class="empty">No open tasks.</li>',
               ('<h2>Flags</h2>%s' % ''.join('<div class="flag">%s</div>' % esc(x)
                                             for x in fl)) if fl else ''))
    return _page('Look 4 - Stream', STREAM_CSS, body)


LOOKS[4] = ('Stream', 'one chronological column; week as a shape', look_stream)


# ================================================================ 5. Bento

BENTO_CSS = """
:root{--bg:#f4f5f7;--fg:#16181d;--dim:#6b7280;--line:#e5e7eb;--card:#fff;
  --accent:#7c3aed;--accent2:#ec4899;--ok:#059669;--warn:#ea580c}
@media(prefers-color-scheme:dark){:root{--bg:#0d0e12;--fg:#eceef3;--dim:#8b93a3;
  --line:#23262e;--card:#15171c;--accent:#a78bfa;--accent2:#f472b6;--ok:#34d399;--warn:#fb923c}}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:1320px;margin:0 auto;padding:18px 20px 46px}
.head{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px}
h1{font-size:22px;margin:0;font-weight:700;letter-spacing:-.02em;
  background:linear-gradient(92deg,var(--accent),var(--accent2));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.head .now{font-size:13px;color:var(--dim);font-variant-numeric:tabular-nums}
/* Every tile is a fixed size. The page is a grid of frames, and what changes is
   what is inside a frame -- never the frame. That is the whole idea here. */
.bento{display:grid;grid-template-columns:repeat(12,1fr);gap:12px;
  grid-auto-rows:minmax(0,auto)}
.tile{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:14px 16px;display:flex;flex-direction:column;min-width:0;overflow:hidden}
.tile h2{font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--dim);
  margin:0 0 10px;font-weight:700;display:flex;align-items:center;gap:8px;flex:none}
.tile h2 .n{margin-left:auto;font-variant-numeric:tabular-nums;
  background:linear-gradient(92deg,var(--accent),var(--accent2));
  -webkit-background-clip:text;background-clip:text;color:transparent;font-size:13px}
.t-sched{grid-column:span 5;height:500px}
.t-focus{grid-column:span 4;height:500px}
.t-week{grid-column:span 3;height:500px}
.t-tasks{grid-column:span 5}
.t-sig{grid-column:span 4}
.t-metrics{grid-column:span 3}
@media(max-width:1080px){.t-sched,.t-focus,.t-week,.t-tasks,.t-sig,.t-metrics{grid-column:span 12}
  .t-sched,.t-focus,.t-week{height:440px}}
.scroll{flex:1;min-height:0;overflow-y:auto;margin:0 -4px;padding:0 4px}
.daybody{position:relative;height:1060px}
.hr{position:absolute;left:0;right:0;border-top:1px solid var(--line)}
.hr span{position:absolute;top:-8px;left:0;font-size:10px;color:var(--dim);background:var(--card);
  padding-right:5px;font-variant-numeric:tabular-nums}
.nowline{position:absolute;left:40px;right:0;z-index:4;
  border-top:2px solid var(--accent2)}
.nowline:after{content:'';position:absolute;left:-5px;top:-4px;width:7px;height:7px;
  border-radius:50%;background:var(--accent2)}
.blk{position:absolute;border-radius:10px;padding:5px 9px;overflow:hidden;cursor:pointer;z-index:2;
  color:#fff;display:flex;flex-direction:column;
  background:linear-gradient(140deg,hsl(var(--hue),64%,58%),hsl(var(--hue),64%,46%));
  box-shadow:0 3px 10px hsl(var(--hue),50%,45%,.28)}
.blk.past{opacity:.35}
.blk.live{outline:2px solid var(--accent2);outline-offset:1px}
.blk.open{height:auto!important;min-height:var(--h);z-index:9;overflow:visible;
  box-shadow:0 14px 34px rgba(0,0,0,.3)}
.bt{font-size:12.5px;font-weight:650;line-height:1.2;flex:none}
.bm{font-size:10px;opacity:.85;font-variant-numeric:tabular-nums;flex:none}
.why{font-size:11px;opacity:.9;line-height:1.35;margin-top:3px;flex:1;min-height:0;overflow:hidden;
  -webkit-mask-image:linear-gradient(#000 calc(100% - 10px),transparent);
  mask-image:linear-gradient(#000 calc(100% - 10px),transparent)}
.blk.open .why{flex:none;overflow:visible;-webkit-mask-image:none;mask-image:none}
.why p{margin:0 0 4px}
.acts{display:none;gap:5px;flex-wrap:wrap;margin-top:6px;padding-top:6px;
  border-top:1px solid rgba(255,255,255,.28)}
.blk.open .acts{display:flex}
.act{font-size:10px;border:1px solid rgba(255,255,255,.45);border-radius:6px;padding:2px 7px;
  color:#fff;text-decoration:none}
.act:hover{background:rgba(255,255,255,.16)}
li.row{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid var(--line);align-items:flex-start}
li.row:last-child{border-bottom:0}
.rank{flex:none;width:20px;font-size:11px;font-weight:700;color:var(--dim);
  font-variant-numeric:tabular-nums;padding-top:2px}
.tt{font-size:10px;padding:1px 7px;border-radius:99px;white-space:nowrap;
  background:hsl(var(--hue),62%,56%,.15);color:hsl(var(--hue),52%,38%);font-weight:600}
@media(prefers-color-scheme:dark){.tt{color:hsl(var(--hue),70%,74%)}}
/* The week tile is counts and colour only -- seven rows, one bar each. No
   assignment name appears here, because it already appears in Focus. */
.wrow{display:grid;grid-template-columns:38px 1fr 26px;gap:8px;align-items:center;
  padding:6px 0;text-decoration:none}
.wrow:hover{opacity:.8}
.wrow .d{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
.wrow.on .d{color:var(--accent);font-weight:700}
.wrow .track{height:8px;border-radius:99px;background:var(--line);display:flex;gap:2px;
  overflow:hidden;padding:0}
.wrow .track i{flex:1;background:hsl(var(--hue),64%,56%)}
.wrow .c{font-size:11px;text-align:right;color:var(--dim);font-variant-numeric:tabular-nums}
.wrow.today{background:linear-gradient(92deg,hsla(280,70%,60%,.09),transparent);
  border-radius:8px;padding:6px 6px;margin:0 -6px}
.mets{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.met{border-radius:12px;padding:10px 12px;background:linear-gradient(140deg,
  rgba(124,58,237,.10),rgba(236,72,153,.08))}
.met b{display:block;font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1}
.met small{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
.met.hot b{color:var(--warn)}
.flag{color:var(--warn);font-size:13px;padding:4px 0;display:flex;gap:7px}
.flag:before{content:'';width:5px;height:5px;border-radius:50%;background:var(--warn);
  margin-top:8px;flex:none}
.empty{color:var(--dim);font-size:13px;padding:8px 0;opacity:.7}
.od{color:var(--warn);font-weight:600}
"""


def look_bento(day=None):
    """5 -- Bento. Fixed frames; only the contents move.

    Six tiles on a twelve-column grid, each with a height set in the
    stylesheet. The schedule is one of them, so a day booked 08:00-21:00 and a
    day with one class produce exactly the same page -- the difference is what
    you scroll to inside a 500px frame.

    Redundancy is cut by giving each tile a *different question*. **Focus** is
    the only place assignment titles appear (top eight, ranked, Canvas and
    tasks together). **Week** is deliberately wordless: seven rows of coloured
    bars answering "is Thursday heavy", which is the only thing the week view
    was ever better at. Deadlines are therefore stated once.
    """
    day = day or core.TODAY.isoformat()
    is_today = day == core.TODAY.isoformat()
    blocks, nowpct = _blocks(day, is_today)
    wk = week_canvas(day)
    mon = datetime.date(*map(int, wk['monday'].split('-')))
    today_iso = core.TODAY.isoformat()
    top, allitems = ranked(limit=8)

    grid = ''.join('<div class="hr" style="top:%.2f%%"><span>%02d:00</span></div>'
                   % (p, m // 60) for m, p in _hours())
    if nowpct is not None:
        grid += '<div class="nowline" style="top:%.2f%%"></div>' % nowpct
    for b in blocks:
        e, rec = b['ev'], b['rec']
        why = ''
        if rec and rec.get('why'):
            why = '<div class="why">%s</div>' % ''.join(
                '<p>%s</p>' % esc(x) for x in (rec.get('why_all') or [rec['why']]))
        grid += ('<div class="blk%s%s" %s style="top:%.2f%%;--h:%.2f%%;height:%.2f%%;'
                 'left:calc(44px + %.3f%%);width:calc(%.3f%% - 46px)">'
                 '<div class="bt">%s</div><div class="bm">%s</div>%s'
                 '<div class="acts">%s</div></div>'
                 % (' past' if b['past'] else '', ' live' if b['live'] else '',
                    swatch(e['label']), b['top'], b['h'], b['h'],
                    b['lane'] * 100.0 / b['nlanes'], 100.0 / b['nlanes'],
                    esc(e['summary']), b['span'], why, _blk_acts(e, rec)))

    focus = ''
    for n, it in enumerate(top, 1):
        title = esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', it['title']))
        if it['url']:
            title = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
        meta = []
        if it['due']:
            meta.append('<span class="%s">%s</span>'
                        % ('od' if it['overdue'] else '', it['due'][5:]))
        if it.get('points'):
            meta.append('%s pts' % esc(str(it['points'])))
        if it.get('elsewhere'):
            meta.append('on %s' % esc(it['elsewhere']))
        focus += ('<li class="row%s"><span class="rank">%d</span>'
                  '<button class="tick" data-key="%s">%s</button>'
                  '<span class="rowbody"><span class="rowtitle">%s</span>'
                  '<span class="rowmeta">%s</span></span>'
                  '<span class="tt" %s>%s</span></li>'
                  % (' crossed' if it['done'] else '', n, esc(it['key']),
                     '&#10003;' if it['done'] else '', title, ' &middot; '.join(meta),
                     swatch(it['tag']), esc(it['tag'] or 'task')))

    wrows = ''
    for i in range(7):
        d = (mon + datetime.timedelta(days=i)).isoformat()
        items = [it for it in wk['days'][d] if not it['done']]
        bars = ''.join('<i style="--hue:%d"></i>' % hue(it['tag']) for it in items[:8])
        wrows += ('<a class="wrow%s%s" href="/look/5?day=%s"><span class="d">%s</span>'
                  '<span class="track">%s</span><span class="c">%s</span></a>'
                  % (' today' if d == today_iso else '', ' on' if d == day else '',
                     d, DOW[i], bars, len(items) or '&middot;'))

    tasks = [i for i in allitems if i['source'] == 'task'][:7]
    trows = ''.join(
        '<li class="row%s"><button class="tick" data-key="%s">%s</button>'
        '<span class="rowbody"><span class="rowtitle">%s</span>'
        '<span class="rowmeta">%s</span></span></li>'
        % (' crossed' if t['done'] else '', esc(t['key']),
           '&#10003;' if t['done'] else '',
           esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', t['title'])),
           ' &middot; '.join(x for x in [esc(t['tag']),
                                         t['due'][5:] if t['due'] else ''] if x))
        for t in tasks)

    fl = flags()
    try:
        sc, _det = metrics.compute_score()
    except Exception:
        sc = {}
    mets = ''.join('<div class="met%s"><b>%s</b><small>%s</small></div>'
                   % (' hot' if k == 'tasks_overdue' and sc.get(k) else '',
                      esc(str(sc.get(k, '-'))), k.replace('_', ' '))
                   for k in ('stall_days_max', 'projects_drifting',
                             'tasks_open', 'tasks_overdue'))

    body = ('<div class="wrap">%s<div class="head"><h1>%s</h1>'
            '<span class="now">%s</span></div><div class="bento">'
            '<div class="tile t-sched"><h2>Schedule<span class="n">%d</span></h2>'
            '<div class="scroll" data-scrollnow><div class="daybody">%s</div></div></div>'
            '<div class="tile t-focus"><h2>Focus<span class="n">%d</span></h2>'
            '<div class="scroll"><ul>%s</ul></div></div>'
            '<div class="tile t-week"><h2>Load<span class="n">%d</span></h2>'
            '<div class="scroll">%s</div></div>'
            '<div class="tile t-tasks"><h2>Tasks<span class="n">%d</span></h2><ul>%s</ul></div>'
            '<div class="tile t-sig"><h2>Flags<span class="n">%d</span></h2>%s</div>'
            '<div class="tile t-metrics"><h2>Execution</h2><div class="mets">%s</div></div>'
            '</div></div>'
            % (looknav(5),
               esc(datetime.date(*map(int, day.split('-'))).strftime('%A %d %B')),
               esc(datetime.datetime.now().strftime('%H:%M')),
               len(blocks), grid or '<p class="empty">Nothing scheduled.</p>',
               len(top), focus or '<p class="empty">Nothing outstanding.</p>',
               sum(1 for dd in wk['days'].values() for it in dd if not it['done']),
               wrows, len(tasks), trows or '<li class="empty">No open tasks.</li>',
               len(fl), ''.join('<div class="flag">%s</div>' % esc(x) for x in fl)
               or '<p class="empty">Clean.</p>', mets))
    return _page('Look 5 - Bento', BENTO_CSS, body)


LOOKS[5] = ('Bento', 'fixed frames, each tile a different question', look_bento)


# ---------------------------------------------------------------- index

def index():
    cards = ''.join(
        '<a class="lk" href="/look/%d"><b>%d &middot; %s</b><span>%s</span>'
        '<em>%s</em></a>'
        % (n, n, esc(LOOKS[n][0]), esc(LOOKS[n][1]),
           esc((LOOKS[n][2].__doc__ or '').strip().split('\n\n')[1].replace('\n', ' ')
               if len((LOOKS[n][2].__doc__ or '').split('\n\n')) > 1 else ''))
        for n in sorted(LOOKS))
    css = """
:root{--bg:#f7f6f3;--fg:#191817;--dim:#6f6a62;--line:#e4e0d8;--card:#fff;--accent:#8c1d40}
@media(prefers-color-scheme:dark){:root{--bg:#131211;--fg:#eeeae3;--dim:#948d83;
  --line:#2b2825;--card:#1b1a18;--accent:#f0839f}}
body{background:var(--bg);color:var(--fg)}
.wrap{max-width:760px;margin:0 auto;padding:30px 22px 60px}
h1{font-size:24px;margin:0 0 6px}
p.lede{color:var(--dim);margin:0 0 22px;line-height:1.55}
.lk{display:block;background:var(--card);border:1px solid var(--line);border-radius:13px;
  padding:14px 17px;margin-bottom:11px;text-decoration:none}
.lk:hover{border-color:var(--accent)}
.lk b{display:block;font-size:16px;margin-bottom:2px}
.lk span{display:block;color:var(--accent);font-size:12.5px;margin-bottom:6px}
.lk em{display:block;color:var(--dim);font-size:13px;font-style:normal;line-height:1.5}
"""
    return _page('Dashboard contenders', css,
                 '<div class="wrap"><h1>Five dashboards</h1>'
                 '<p class="lede">All five fix the same three things: the day column is a '
                 'fixed-height viewport scrolled to now, no assignment is listed twice, and '
                 'courses carry colour. They disagree about everything else. '
                 'The live dashboard is untouched &mdash; '
                 '<a href="/">go back</a>.</p>%s</div>' % cards)
