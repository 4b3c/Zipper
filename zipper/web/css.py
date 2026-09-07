"""The dashboard stylesheet.

Split out of serve.py 2026-09-07: it is a literal with no behaviour, and
leaving it inline meant every read of the server had 190 lines of CSS in the
middle of it.
"""

CSS = """
:root{--bg:#fbfaf8;--fg:#1c1a17;--dim:#6d675e;--line:#e2ded6;--card:#fff;--accent:#8c1d40;--warn:#b3541e;--ok:#3f6f4a}
@media(prefers-color-scheme:dark){:root{--bg:#171614;--fg:#ece8e1;--dim:#989186;--line:#2f2c28;--card:#1e1d1a;--accent:#e0708f;--warn:#e0965c;--ok:#7fb98b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px}
h1{font-size:22px;margin:0 0 2px}
h1 .btn{margin-top:4px}
.sub{color:var(--dim);font-size:13px}
.fresh{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 20px}
.chip{border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:12px;color:var(--dim);background:var(--card)}
.chip b{color:var(--fg);font-weight:600}
.chip.stale{border-color:var(--warn);color:var(--warn)}
.chip a{color:var(--accent);text-decoration:none;margin-left:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:18px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin:0 0 10px;font-weight:600}
.today{display:grid;grid-template-columns:1fr 1fr;gap:0}
.today>div{padding:0 16px}
.today>div:first-child{border-right:1px solid var(--line)}
@media(max-width:800px){.today{grid-template-columns:1fr}.today>div:first-child{border-right:0;border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:12px}}
ul{list-style:none;margin:0;padding:0}
li{padding:4px 0;font-size:14px;display:flex;gap:8px;align-items:baseline}
li.row{align-items:flex-start;gap:10px;padding:7px 0;border-bottom:1px solid var(--line)}
li.row:last-child{border-bottom:0}
.rowbody{display:flex;flex-direction:column;gap:2px;min-width:0}
.rowtitle{line-height:1.35}
.rowmeta{font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums}
li.crossed .rowtitle,li.crossed .rowtitle a{text-decoration:line-through;color:var(--dim)}
.t{color:var(--dim);font-variant-numeric:tabular-nums;font-size:12px;min-width:46px}
/* the schedule grid: one column of the day, height = duration */
.grid{position:relative;margin:2px 0 4px}
.hr{position:absolute;left:0;right:0;border-top:1px solid var(--line)}
.hr span{position:absolute;top:-7px;left:0;font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums;background:var(--card);padding-right:6px}
.nowline{position:absolute;left:44px;right:0;border-top:1px solid var(--accent);z-index:3}
.nowline:before{content:'';position:absolute;left:-4px;top:-3px;width:6px;height:6px;border-radius:50%;background:var(--accent)}
.blk{position:absolute;top:var(--top);height:var(--h);left:calc(var(--l) + 52px);width:calc(var(--w) - 56px);display:flex;flex-direction:column;overflow:hidden;background:var(--bg);border:1px solid var(--line);border-left:3px solid var(--dim);border-radius:5px;padding:3px 7px;z-index:2;cursor:pointer}
.blk:hover{border-color:var(--dim)}
.blk.open{height:auto;min-height:var(--h);overflow:visible;z-index:9;border-color:var(--accent);box-shadow:0 8px 28px rgba(0,0,0,.28)}
.blk.open .why{flex:none;overflow:visible;-webkit-mask-image:none;mask-image:none}
.bx{display:none;flex:none;margin-top:6px;padding-top:6px;border-top:1px solid var(--line)}
.blk.open .bx{display:block}
.acts{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.act{font:inherit;font-size:11px;border:1px solid var(--line);border-radius:5px;padding:2px 7px;color:var(--accent);text-decoration:none;background:none;cursor:pointer;white-space:nowrap}
.act:hover{border-color:var(--accent);background:var(--card)}
.act[disabled]{opacity:.5;cursor:default}
.actdim{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em;margin-left:auto}
.blk.noted{border-left-color:var(--accent)}
.blk.past{opacity:.5}
.blk.past .bt{text-decoration:line-through}
.bt{font-size:13px;line-height:1.2;font-weight:500;flex:none}
.bm{font-size:11px;line-height:1.3;color:var(--dim);font-variant-numeric:tabular-nums;flex:none}
.why{font-size:11px;color:var(--dim);line-height:1.3;margin-top:2px;flex:1 1 auto;min-height:0;overflow:hidden;-webkit-mask-image:linear-gradient(to bottom,#000 calc(100% - 9px),transparent);mask-image:linear-gradient(to bottom,#000 calc(100% - 9px),transparent)}
.why p{margin:0 0 4px}
.why p:last-child{margin-bottom:0}
.bt{overflow:hidden;text-overflow:ellipsis}
.pin{font-size:10px;color:var(--accent);white-space:nowrap}
.card h2.hdr{display:flex;align-items:center;gap:8px}
.vtable{width:100%;border-collapse:collapse;font-size:13px}
.vtable th{text-align:left;font-weight:600;color:var(--dim);font-size:11px;letter-spacing:.04em;text-transform:uppercase;padding:0 10px 6px 0;border-bottom:1px solid var(--line)}
.vtable td{padding:7px 10px 7px 0;border-bottom:1px solid var(--line);vertical-align:top}
.vtable tr:last-child td{border-bottom:0}
.vwrap{overflow-x:auto}
.vlink{color:var(--accent);text-decoration:none}
.vlink:hover{text-decoration:underline}
.vnone{color:var(--dim)}
.vnote{margin:0 0 10px}
.vmore{margin:8px 0 0}
.vnavbar{margin-bottom:16px}
.vnav{color:var(--dim);text-decoration:none;margin-right:10px}
.vnav.on{color:var(--fg)}
.vnav:hover{color:var(--fg)}
.daynav{margin-left:auto;display:flex;gap:4px;align-items:center}
.daynav #daytoday{margin-right:10px}
.daynav button{background:none;border:1px solid var(--line);border-radius:5px;color:var(--dim);cursor:pointer;font:inherit;font-size:13px;line-height:1;padding:3px 8px;text-transform:none;letter-spacing:0}
.daynav button:hover{color:var(--fg);border-color:var(--dim)}
.done,.past{text-decoration:line-through;color:var(--dim)}
.tag{font-size:11px;color:var(--dim);border:1px solid var(--line);border-radius:4px;padding:0 5px;white-space:nowrap}
.od{color:var(--warn);font-weight:600}
.pri{font-variant-numeric:tabular-nums;color:var(--accent)}
.elsewhere{opacity:.75;font-style:italic}
.src{font-size:10px;text-transform:uppercase;letter-spacing:.04em;border-radius:3px;padding:0 4px;border:1px solid var(--line);color:var(--dim)}
.src.canvas{border-color:var(--accent);color:var(--accent)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:800px){.cols{grid-template-columns:1fr}}
.flag{color:var(--warn);font-size:13px;padding:3px 0}
.big{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums}
.metrics{display:flex;gap:22px;flex-wrap:wrap}
.metric small{display:block;color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
footer{margin-top:24px;border-top:1px solid var(--line);padding-top:14px}
footer .fresh{margin:0}
code{background:var(--line);padding:1px 5px;border-radius:4px;font-size:12px}
.qrow{font:12px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace;border-bottom:1px solid var(--line);
      padding:2px 0;display:flex;gap:8px;align-items:flex-start}
.qrow:last-child{border-bottom:0}
.qx{white-space:pre-wrap;flex:1;min-width:0}
/* Fixed column so every row's text starts at the same x: feed rows stamp
   HH:MM:SS and note rows HH:MM, and ragged left edges read as two lists. */
.qt{color:var(--dim);min-width:62px;flex:none}
.qrow.crossed .qx,.qrow.crossed .qt{text-decoration:line-through;color:var(--dim)}
/* No `.qrow.crossed .tick` accent: queue ticks are all ghosts now, and that rule
   outranked .tick.ghost on specificity, so a crossed row drew an empty
   accent-bordered box -- which reads as a checkbox that refuses to be clicked. */
.qfresh{font-weight:400;margin-left:8px}
#qrefetch{float:right}
/* Every queue row's tick is a ghost -- the card is read-only. The width is kept
   so the queue's text still lines up with the tasks in "What to work on", which
   do have real boxes. */
.tick.ghost{border-color:transparent;cursor:default}
.qfold{display:block;width:100%;text-align:left;background:none;border:0;cursor:pointer;
  font:12px/1.9 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim);padding:4px 0 0}
.qfold:hover{color:var(--accent)}
.btn{float:right;margin-left:10px;font:inherit;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
  background:none;border:1px solid var(--line);color:var(--dim);border-radius:5px;padding:1px 8px;cursor:pointer;text-decoration:none}
.btn:hover{border-color:var(--accent);color:var(--accent)}
#termwrap iframe{width:100%;height:600px;border:0;border-radius:8px;background:#171614;display:block}
#termbody{display:flex;gap:10px;align-items:stretch}
#toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(8px);
       background:#171614;color:#ece8e1;padding:7px 13px;border-radius:7px;font-size:12px;
       opacity:0;pointer-events:none;transition:opacity .15s,transform .15s;z-index:200}
#toast.on{opacity:.94;transform:translateX(-50%) translateY(0)}
.termdead{height:600px;display:flex;flex-direction:column;align-items:center;justify-content:center;
          gap:10px;background:#171614;border-radius:8px;color:#ece8e1}
#termcard.full .termdead{height:100%}
#termwrap{flex:1;min-width:0}
/* The side column is the fixed-width thing; the list inside it scrolls and the
   meters sit under it, so a long list never pushes them off the card. */
#chatside{width:186px;flex:0 0 186px;display:flex;flex-direction:column;gap:8px;max-height:600px}
#chatlist{display:flex;flex-direction:column;gap:4px;overflow-y:auto;min-height:0;flex:1}
#termcard.full #termbody{flex:1;min-height:0}
#termcard.full #chatside{max-height:none}
#usemeters{flex:none;border-top:1px solid var(--line);padding-top:7px;
  display:flex;flex-direction:column;gap:6px}
#usemeters:empty{display:none}
/* One line: bar, then the number, then when it resets. The bar takes what is
   left so both readouts stay on the same right edge across the two rows. */
.use{font:11px/1.35 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim);
  display:flex;align-items:center;gap:6px}
.use .pct{flex:none;width:29px;text-align:right;font-variant-numeric:tabular-nums}
.use .rst{flex:none;opacity:.7;white-space:nowrap}
.use .bar{flex:1;min-width:22px;height:4px;border-radius:3px;background:rgba(127,127,127,.22);overflow:hidden}
.use .fill{display:block;height:100%;border-radius:3px;background:var(--accent);
  transition:width .3s ease}
.use.warn .fill{background:#e0a52b}
.use.hot .fill{background:#d2604a}
.use.hot .pct,.use.warn .pct{color:inherit}
#usemeters .err{font:11px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim);opacity:.7}
#usemeters.stale{opacity:.55}
.chat{text-align:left;background:none;border:1px solid transparent;border-radius:7px;padding:6px 8px;
      cursor:pointer;color:inherit;font:inherit;line-height:1.25;display:block;width:100%}
.chat:hover{background:rgba(127,127,127,.10)}
.chat.on{border-color:var(--accent);background:rgba(127,127,127,.07)}
.chat{display:flex;align-items:center;gap:7px}
.chat .ct{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chat .dot{flex:0 0 7px;width:7px;height:7px;border-radius:50%;background:#8a8a8a}
.chat.waiting .dot{background:#4caf72}
.chat.working .dot{background:#e0a52b;animation:chatpulse 1.4s ease-in-out infinite}
.chat.closed .dot{background:#8a8a8a}
.chat.closed .ct{opacity:.55}
@keyframes chatpulse{0%,100%{opacity:1}50%{opacity:.35}}
#termcard.full{position:fixed;inset:0;z-index:99;margin:0;border-radius:0;display:flex;flex-direction:column}
#termcard.full #termwrap{flex:1}
#termcard.full #termwrap iframe{height:100%}
.tick{flex:none;width:17px;height:17px;margin-top:1px;border:1.5px solid var(--line);border-radius:4px;
  background:none;color:var(--accent);cursor:pointer;font-size:11px;line-height:1;padding:0;
  display:flex;align-items:center;justify-content:center}
.tick:not(.ghost):hover{border-color:var(--accent)}
/* :not(.ghost) because this rule sits after .tick.ghost at equal specificity and
   would otherwise win: the read-only queue boxes lit up on hover and read as
   clickable things that then did nothing. */
li.crossed .tick{border-color:var(--accent)}
/* `el.hidden` sets an attribute, and the UA rule behind it is only [hidden]{display:none}
   -- which ANY author rule that sets display outranks. #termstart{display:flex} is an id
   selector, so hiding the start box set the attribute and changed nothing on screen: the
   buttons stayed up next to the running conversation through two rounds of "fixes" to the
   logic, which was correct the whole time. Make the attribute win everywhere. */
[hidden]{display:none!important}
#termstart{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;padding:26px 0 20px}
.startbtn{font:inherit;font-size:15px;background:none;border:1px solid var(--line);color:var(--fg);
  border-radius:9px;padding:13px 26px;cursor:pointer;transition:border-color .15s,color .15s}
.startbtn:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
.startbtn:disabled{opacity:.35;cursor:not-allowed}
.more{float:right;font-size:11px;color:var(--accent);text-decoration:none;text-transform:none;letter-spacing:0}
.more:hover{text-decoration:underline}
footer .fresh{margin:0 0 10px}
a.plain{color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}
a.plain:hover{border-bottom-color:var(--accent)}
"""
