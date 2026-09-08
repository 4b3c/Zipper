"""The dashboard's browser code, as literals.

`JS` is the page script; `TICKJS` is the click handling that used to sit
beside the tick boxes. Both are inserted into the page template as arguments,
never %-formatted themselves, so a stray %% in here is harmless.

Split out of serve.py 2026-09-07 -- 620 lines of JavaScript inside a Python
module made the whole file impossible to read a section of.
"""

JS = """
function fmt(t){
  if(t==null) return 'never';
  let s=Math.max(0,Math.floor(Date.now()/1000-t));
  if(s<60) return s+'s ago';
  if(s<3600) return Math.round(s/60)+'m ago';
  if(s<172800) return Math.round(s/3600)+'h ago';
  return Math.round(s/86400)+'d ago';
}
function drawFresh(){
  // With no fetch at launch, how old the data is stopped being obvious -- the
  // page used to be current by definition. The footer chips still break it down
  // per source; this is the one number worth reading without looking for it.
  const hd=document.getElementById('qfresh');
  if(hd){
    const ks=['calendars','github','canvas'].map(k=>window.__epochs[k]).filter(x=>x!=null);
    hd.textContent = ks.length ? 'fetched '+fmt(Math.min.apply(null,ks)) : 'never fetched';
  }
  const el=document.getElementById('fresh'); if(!el) return;
  el.innerHTML=['vault','calendars','github','canvas'].map(k=>{
    const x = k==='canvas' ? ' <a href="'+window.__canvashost+'" target="_blank" rel="noopener">open Canvas</a>' : '';
    return '<span class="chip"><b>'+k+'</b> '+fmt(window.__epochs[k])+x+'</span>';
  }).join('');
}
setInterval(drawFresh,1000);
// Read-only, like _qrow_html server-side: no queue row is crossed off by hand.
// A row clears when a pass commits, or when the session that did the reasoning
// runs --mark. See _qrow_html for why the box went away.
function qrowHTML(r){
  const t=(r.text||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
  return '<div class="qrow'+(r.done?' crossed':'')+'"><span class="tick ghost"></span>'
        +'<span class="qt">'+r.at+'</span><span class="qx">'+t+'</span></div>';
}
// Dealt-with rows leave the list. The card answers "what is left", and a run
// of 65 items where 60 are struck through answers it badly. They are folded
// rather than deleted so a closed pass can still be read back — what it
// accounted for, not just that it ended. Undo is no longer why the fold exists
// (there is nothing to mis-click now); `--mark` toggles, so the session that
// crossed a row too early is still the thing that puts it back.
function drawQueue(rows){
  if(rows) window.__feed=rows;
  const q=document.getElementById('queue'); if(!q) return;
  const feed=window.__feed||[], notes=window.__notes||[];
  const open=feed.filter(r=>!r.done), done=feed.filter(r=>r.done);
  document.getElementById('qcount').textContent=open.length+notes.length;
  if(!feed.length&&!notes.length){
    q.dataset.empty='1';
    q.innerHTML='<p class="sub">Waiting for this run\u2019s fetch\u2026</p>';
    return;
  }
  delete q.dataset.empty;
  let h = open.map(qrowHTML).join('') + notesHTML(notes);
  if(!open.length&&!notes.length) h = '<p class="sub">All clear \u2014 everything this run turned up is dealt with.</p>';
  if(done.length){
    h += '<button class="qfold" id="qfold">'+(window.__showdone?'\u25be':'\u25b8')+' '
       + done.length+' crossed off</button>';
    if(window.__showdone) h += done.map(qrowHTML).join('');
  }
  q.innerHTML=h;
}
// Note paths are filenames, so they really can carry & and quotes -- unlike
// the queue text, which qrowHTML escapes inline.
function esc(x){return String(x==null?'':x).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
// Note edits are queue rows like any other -- one list, not a section. The only
// thing that makes them different is how they clear, and the missing tick box
// says that on the row itself. See _qnotes_html for the same rendering server-side.
function notesHTML(rows){
  if(!rows.length) return '';
  return rows.map(r=>'<div class="qrow nrow"><span class="tick ghost"></span>'
      +'<span class="qt">'+esc((r.when||'').slice(11))+'</span>'
      +'<span class="qx">'+esc(r.text||(r.action+' '+r.path))+'</span></div>').join('')
    +'<p class="sub">Nothing here is crossed off by hand. The pass clears it \u2014 '
    +'<code>python3 -m zipper commit "msg"</code></p>';
}
function drawNotes(rows){
  window.__notes=rows||[];
  drawQueue();
}
function row(e){
  const f=window.__feed||[];
  if(e.key && f.some(r=>r.key===e.key)) return;
  f.push({key:e.key||'',at:e.at,text:e.text,done:e.done||null});
  drawQueue(f);
}
// Three states, and the card shows exactly one set of choices for each:
//   mounted  the conversation is on screen — no start buttons at all, just the
//            header's `new conversation`, which is the only thing left to want
//   live     a conversation is running but this page isn't showing it (the app
//            was closed and reopened) — resume, or resume and hand it the queue.
//            Both land in the SAME conversation; one arrives with an instruction
//   cold     nothing running — start one, blank or primed with the queue
// `new conversation` lives in the header and is visible whenever there is a
// conversation to replace, so starting over never depends on finding the box.
function drawTerm(){
  const box=document.getElementById('termstart'); if(!box) return;
  // `on` means the iframe is showing a conversation, so there is nothing to
  // resume and no start buttons. Every ttyd belongs to a conversation, and
  // focusRecent() mounts the most recent one at load.
  const on=window.__mounted, live=window.__session, ready=window.__queueready;
  const qd = ready ? '' : ' disabled title="nothing in this run&#39;s queue to consume"';
  // `closedview` is the third thing that can occupy the terminal slot: the panel
  // offering to resume a closed conversation. It lives in #termwrap, which
  // #termstart hides while it is showing -- so from a cold card, clicking a
  // closed row rendered the panel underneath an invisible element and looked
  // like nothing had happened. It is not a mounted conversation, though, so it
  // gets no fullscreen or pop-out.
  box.hidden = !!(on||window.__closedview);
  if(!on) box.innerHTML = live
    ? '<button class="startbtn" data-mode="resume">resume conversation</button>'
     +'<button class="startbtn" data-mode="catchup"'+qd+'>resume and clear queue</button>'
    : '<button class="startbtn" data-mode="blank">start blank session</button>'
     +'<button class="startbtn" data-mode="queue"'+qd+'>start session to clear queue</button>';
  document.getElementById('termnew').hidden = !(live||on||window.__closedview);
  ['termfull','termpop'].forEach(i=>{document.getElementById(i).hidden=!on;});
  const st=document.getElementById('termstate');
  if(st && !on) st.textContent = window.__closedview ? 'closed'
    : live ? 'running — not attached here' : 'not started';
  if(st && on && !st.dataset.said) st.textContent='';
}
function shiftDay(iso,n){const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()+n);
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function drawDay(){
  const t=window.__today, d=window.__day;
  const lbl=document.getElementById('daylabel'); if(!lbl) return;
  const off=Math.round((new Date(d+'T12:00:00')-new Date(t+'T12:00:00'))/86400000);
  lbl.textContent = off===0 ? 'Today' : off===1 ? 'Tomorrow' : off===-1 ? 'Yesterday'
    : new Date(d+'T12:00:00').toLocaleDateString(undefined,{weekday:'short',day:'2-digit',month:'short'});
  // The masthead names the day being read, not the day it is: walking the arrows
  // and leaving today's date at the top of the page reads as a stuck page.
  const pd=document.getElementById('pagedate'), D=new Date(d+'T12:00:00');
  if(pd) pd.textContent = D.toLocaleDateString('en-US',{weekday:'long'})+' '
    +String(D.getDate()).padStart(2,'0')+' '+D.toLocaleDateString('en-US',{month:'long'})
    +' '+D.getFullYear();   // matches the server's '%A %d %B %Y' so the first paint doesn't shift
  const b=document.getElementById('daytoday'); if(b) b.hidden = off===0;
}
async function goDay(n){
  window.__day = n===0 ? window.__today : shiftDay(window.__day,n);
  drawDay(); await panels();
}
async function panels(){
  const p=await fetch('/api/panels?day='+encodeURIComponent(window.__day||''))
    .then(r=>r.json()).catch(()=>null); if(!p) return;
  for(const k in p.html){const el=document.getElementById(k); if(el) el.innerHTML=p.html[k];}
  window.__epochs=p.epochs; drawFresh();
  window.__session=p.session||window.__mounted; window.__queueready=p.queue_ready;
  drawTerm();
}
const es=new EventSource('/events');
es.addEventListener('diff',e=>row(JSON.parse(e.data)));
es.addEventListener('feed',e=>drawQueue(JSON.parse(e.data).rows));
es.addEventListener('notes',e=>drawNotes(JSON.parse(e.data).rows));
document.addEventListener('click',async ev=>{
  const mk=ev.target.closest('.mknote');
  if(mk){
    ev.stopPropagation(); mk.disabled=true; mk.textContent='creating\u2026';
    const r=await fetch('/api/eventnote',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({summary:mk.dataset.summary,date:mk.dataset.date})})
      .then(r=>r.json()).catch(()=>({error:'failed'}));
    if(r.error){mk.disabled=false;mk.textContent=r.error;return;}
    await panels();
    return;
  }
  if(ev.target.closest('.blk a')) return;            // let the actions navigate
  const blk=ev.target.closest('.blk');
  document.querySelectorAll('.blk.open').forEach(b=>{if(b!==blk)b.classList.remove('open');});
  if(blk){ev.stopPropagation(); blk.classList.toggle('open');}
});
document.addEventListener('keydown',ev=>{
  if(ev.key==='Escape') document.querySelectorAll('.blk.open').forEach(b=>b.classList.remove('open'));
});
document.addEventListener('DOMContentLoaded',()=>{
  const pv=document.getElementById('dayprev'), nx=document.getElementById('daynext'),
        td=document.getElementById('daytoday');
  if(pv) pv.onclick=()=>goDay(-1);
  if(nx) nx.onclick=()=>goDay(1);
  if(td) td.onclick=()=>goDay(0);
  document.addEventListener('keydown',ev=>{
    if(ev.metaKey||ev.ctrlKey||ev.altKey) return;
    const tag=(ev.target.tagName||'').toLowerCase();
    if(tag==='input'||tag==='textarea'||ev.target.isContentEditable) return;
    if(ev.key==='ArrowLeft') goDay(-1); else if(ev.key==='ArrowRight') goDay(1);
  });
  drawDay();
});
es.addEventListener('source',()=>panels());
es.addEventListener('status',e=>{document.getElementById('status').textContent=JSON.parse(e.data).text;});
es.addEventListener('done',()=>{panels();document.getElementById('status').textContent='';});
// Same origin as the dashboard, proxied by nginx to the ttyd on that port.
// Pointing the iframe at host:port directly made every conversation its own
// origin, so the terminal asked to sign in again each time one was opened.
function termURL(port){return location.origin+'/t/'+port+'/';}
// ---- the chat list: one Claude per Discord thread, switched by clicking.
// Each conversation has its own ttyd on its own port, so switching is just
// pointing the iframe somewhere else -- nothing is torn down, and the
// conversation you were reading keeps running while you read another.
window.__chat=null;
// Thread titles are whatever was typed into Discord, so they are escaped here
// rather than trusted. `esc` on the server is Python's; this is the page's own.
function chatEsc(t){return String(t==null?'':t).replace(/[&<>"]/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function drawChats(rows){
  const el=document.getElementById('chatlist');
  if(!el) return;
  if(!rows||!rows.length){el.innerHTML='';sideVis();return;}
  el.innerHTML=rows.map(r=>{
    const on=(String(r.thread_id)===String(window.__chat))?' on':'';
    const st=r.state||(r.alive?'waiting':'closed');
    // yellow working, green waiting, grey closed. The dot carries the state on
    // its own -- the row is a name and a light, and a line of small print under
    // every one of them made the list harder to read, not easier.
    return '<button class="chat '+st+on+'" data-tid="'+r.thread_id+'" title="'+
           chatEsc(r.title)+' \u2014 '+st+'"><span class="dot"></span>'+
           '<span class="ct">'+chatEsc(r.title)+'</span></button>';
  }).join('');
  // Deliberately no scrollIntoView on the selected row: the page moving under
  // him on a 6s poll is worse than a selected row sitting out of sight.
  sideVis();
}
// The column carries two things now, so it is on if either has content --
// otherwise an empty 186px gutter sits beside the terminal.
function sideVis(){
  const side=document.getElementById('chatside');
  if(!side) return;
  const list=document.getElementById('chatlist'), m=document.getElementById('usemeters');
  side.hidden = !((list&&list.children.length)||(m&&m.children.length));
}
// ---- plan usage: the 5-hour session window and the 7-day one, as bars.
// The numbers are Anthropic's, not this box's -- see zipper/usage.py for why a
// local estimate was not good enough. Five minutes is the server's cache TTL,
// so polling faster would only re-serve the same answer.
// The stamps are UTC, like every other feed here. Rendered in the box's local
// time -- a bar that says it resets at 02:50 when he is reading it at 19:50 is
// worse than saying nothing.
function resetDate(s){ if(!s) return null; const d=new Date(s); return isNaN(d)?null:d; }
function resetShort(s){
  const d=resetDate(s); if(!d) return '';
  // 186px of column: the time alone if it lands today or tonight, a weekday in
  // front of it if it doesn't. Anything longer wraps and pushes the bar around.
  const t=d.toLocaleTimeString([], {hour:'numeric', minute:'2-digit'}).replace(' ','').toLowerCase();
  return d.toDateString()===new Date().toDateString()
    ? t : d.toLocaleDateString([], {weekday:'short'})+' '+t;
}
function resetLong(s){
  const d=resetDate(s); if(!d) return 'unknown';
  return d.toLocaleString([], {weekday:'short', hour:'numeric', minute:'2-digit'});
}
function drawUsage(d){
  const el=document.getElementById('usemeters');
  if(!el) return;
  const rows=(d&&d.meters)||[];
  el.classList.toggle('stale', !!(d&&d.stale));
  if(!rows.length){
    // A blank meter is honest; a bar drawn from a number nothing returned is not.
    el.innerHTML = d&&d.error ? '<p class="err">usage: '+chatEsc(d.error)+'</p>' : '';
    sideVis(); return;
  }
  el.innerHTML=rows.map(r=>{
    const p=Math.max(0,Math.min(100,Number(r.pct)||0));
    const cls=p>=90?' hot':(p>=70?' warn':'');
    // No label: the two bars are the session and the week, in that order, and
    // the reset time says which is which more usefully than the words did --
    // one resets tonight, the other on a weekday.
    return '<div class="use'+cls+'" title="'+chatEsc(r.label)+' — resets '+
           chatEsc(resetLong(r.resets))+'"><span class="bar">'+
           '<span class="fill" style="width:'+p+'%"></span></span>'+
           '<span class="pct">'+p.toFixed(0)+'%</span>'+
           '<span class="rst">'+chatEsc(resetShort(r.resets))+'</span></div>';
  }).join('');
  sideVis();
}
function loadUsage(){
  return fetch('/api/usage').then(r=>r.json()).then(d=>{drawUsage(d);return d;})
    .catch(()=>{});
}
function loadChats(){
  return fetch('/api/conversations').then(r=>r.json())
    .then(d=>{window.__chats=d.conversations||[];drawChats(window.__chats);
              checkShown(window.__chats);return window.__chats;})
    .catch(()=>[]);
}
// On a reload the page used to show whichever ttyd happened to be serving --
// usually the dashboard's own terminal, which is rarely the conversation he was
// last in. `/api/conversations` is already sorted newest-first, so the top live
// row is the one to land on.
//
// Only a row that is still alive is opened automatically. Resuming a *closed*
// conversation re-reads its whole transcript at full price, and a page refresh
// must never spend that on its own -- those keep the deliberate click.
function focusRecent(rows){
  if(window.__chat) return;                       // already showing something
  const row=(rows||[]).find(r=>r.state!=='closed');
  if(!row) return;
  window.__chat=row.thread_id;
  drawChats(rows);
  if(row.serving&&row.port){
    // Free: the ttyd is already up. Skip the remount if it is what the page is
    // showing anyway, so a reload doesn't reload the iframe twice.
    window.__mounted=true;
    if(row.port!==window.__port) mountTerm(row.port);
    drawTerm();
  } else {
    openChat(row.thread_id);                      // alive; just needs a ttyd
  }
}
// A conversation can die without the page doing anything -- Ctrl-C in the pane
// ends Claude and takes the tmux session with it. The iframe then shows a
// terminal that is either frozen or, worse, a fresh shell wearing the old
// conversation's name. Swap it for a button that resumes the real one.
function checkShown(rows){
  if(!window.__chat) return;
  const row=(rows||[]).find(r=>String(r.thread_id)===String(window.__chat));
  const wrap=document.getElementById('termwrap');
  if(!wrap||!row) return;
  if(row.state==='closed'){
    if(!wrap.dataset.closed) showClosed(row);
  } else if(wrap.dataset.closed && row.serving){
    // It came back by some other route (a Discord message, say). Only then is
    // remounting free -- never resume one just because the page is looking.
    wrap.dataset.closed='';
    mountTerm(row.port);
  }
}
function openChat(tid){
  const el=document.getElementById('chatlist');
  if(el) el.querySelectorAll('.chat').forEach(b=>b.disabled=true);
  return fetch('/api/conversation',{method:'POST',headers:{'Content-Type':'application/json'},
                                    body:JSON.stringify({thread_id:tid})})
    .then(r=>r.json()).then(d=>{
      if(d.ok&&d.port){window.__chat=tid;window.__mounted=true;mountTerm(d.port);drawTerm();}
      return loadChats();
    }).catch(()=>loadChats());
}
document.addEventListener('click',ev=>{
  const b=ev.target.closest?ev.target.closest('.chat'):null;
  if(!b||!b.dataset.tid) return;
  const tid=b.dataset.tid;
  const row=(window.__chats||[]).find(r=>String(r.thread_id)===String(tid));
  // Selecting a closed conversation costs nothing; *resuming* one re-reads the
  // whole transcript at full price, because its prompt cache has expired. That
  // is a decision, not a side effect of clicking a name to see what it was.
  if(row&&row.state==='closed'){ window.__chat=tid; drawChats(window.__chats); showClosed(row); return; }
  openChat(tid);
});
function showClosed(row){
  const wrap=document.getElementById('termwrap');
  if(!wrap) return;
  wrap.dataset.closed='1';
  window.__closedview=true; drawTerm();
  wrap.innerHTML='<div class="termdead"><p><b>'+chatEsc(row.title)+'</b> is closed.</p>'+
    '<button class="btn" id="termreload">reload conversation</button>'+
    '<p class="sub">Resuming re-reads the whole conversation \u2014 its prompt cache has '+
    'expired, so this one costs full price.</p></div>';
  const b=document.getElementById('termreload');
  if(b) b.onclick=()=>{b.disabled=true;b.textContent='resuming\u2026';
                       wrap.dataset.closed='';openChat(row.thread_id);};
}
function mountTerm(port){
  const u=termURL(port);
  window.__port=port; window.__closedview=false;
  document.getElementById('termwrap').innerHTML='<iframe src="'+u+'" allow="clipboard-read; clipboard-write"></iframe>';
  document.getElementById('termpop').href=u;
  const f=document.querySelector('#termwrap iframe');
  if(f){
    // Both, because either can be missed: a cached iframe can finish loading
    // before the listener is attached, and hookTerm is idempotent by design.
    f.addEventListener('load',()=>hookTerm(f));
    setTimeout(()=>hookTerm(f),1500);
  }
}
// Selecting in the terminal should put the text on the real machine's clipboard,
// and an image on the clipboard should reach the conversation. Both are only
// possible because ttyd is served from this origin now: the iframe is
// same-origin, so its window -- and the xterm instance ttyd leaves on it as
// `term` -- can be reached from here.
function clipReport(o){try{fetch('/api/clipdebug',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});}catch(e){}}
function findTerm(win){
  // ttyd leaves the xterm instance on `window.term`, but do not depend on the
  // name: anything exposing getSelection + onSelectionChange is the terminal.
  if(win.term&&win.term.getSelection) return win.term;
  try{
    for(const k of Object.keys(win)){
      const v=win[k];
      if(v&&typeof v==='object'&&typeof v.getSelection==='function'
         &&typeof v.onSelectionChange==='function') return v;
    }
  }catch(e){}
  return null;
}
function hookTerm(f){
  let win;
  try{ win=f.contentWindow; }catch(e){ clipReport({stage:'cross-origin'}); return; }
  let tries=0;
  (function wait(){
    if(!win||!win.document) return;
    const term=findTerm(win);
    if(!term){ if(tries++<40){ setTimeout(wait,250); return; }
               clipReport({stage:'no-term-after-10s'}); return; }
    if(win.__zipperHooked) return;
    win.__zipperHooked=true;
    watchBuffer(win);

    // copy: xterm keeps its own selection (the canvas renderer means the page
    // has none), so read it from the terminal and write it from inside the
    // frame, where the click that just happened counts as the user gesture.
    const report=o=>{try{fetch('/api/clipdebug',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});}catch(e){}};
    // Must run *inside* the event, not in a setTimeout after it: execCommand
    // and the clipboard API both require an active user gesture, and a
    // continuation scheduled off the event no longer counts as one. That was
    // the bug -- the copy ran, silently did nothing, and left the old clipboard.
    const copySel=(why)=>{
      // Either source: xterm keeps its own selection with the canvas renderer,
      // but with the DOM renderer the selection is the document's and xterm may
      // report nothing. Whichever has text is the one the user made.
      let sel=''; try{ sel=term.getSelection()||''; }catch(e){}
      if(!sel){ try{ sel=String(win.getSelection()||'').trim(); }catch(e){} }
      if(!sel||sel===win.__lastSel) return;
      win.__lastSel=sel;
      const secure=!!win.isSecureContext, api=!!(win.navigator.clipboard&&win.navigator.clipboard.writeText);
      let how='none', ok=false;
      // execCommand first when the page is not a secure context: there the
      // async clipboard API does not exist at all, and asking for it first only
      // wastes the gesture.
      if(secure&&api){
        how='api';
        try{ win.navigator.clipboard.writeText(sel).then(()=>report({how:'api',ok:true,n:sel.length,why:why}),
                                                        e=>{const r=legacyCopy(win,sel);
                                                            report({how:'api->exec',ok:r,n:sel.length,err:String(e),why:why});});
             ok=true; }
        catch(e){ how='exec'; ok=legacyCopy(win,sel); }
      } else {
        how='exec'; ok=legacyCopy(win,sel);
      }
      if(how!=='api') report({how:how,ok:ok,n:sel.length,secure:secure,api:api,why:why,
                              proto:win.location.protocol});
      sendSel(win,sel,why);
    };
    win.document.addEventListener('mouseup',()=>copySel('mouseup'),true);

    // Belt and braces: xterm's own event. It fires without a DOM event when
    // selection is extended by keyboard or by a drag that ends outside the
    // frame, and it is the only signal if something swallows mouseup.
    try{ term.onSelectionChange(()=>{ if(!win.__selPending){ win.__selPending=1;
           setTimeout(()=>{ win.__selPending=0;
             let sel=''; try{ sel=term.getSelection(); }catch(e){}
             if(sel&&sel!==win.__lastSel){ win.__lastSel=sel; sendSel(win,sel,'selchange'); }
           },120); } }); }catch(e){}
    // Ctrl/Cmd+C on keydown, before xterm forwards it to the pty: on keyup the
    // gesture is spent and, worse, a bare Ctrl-C has already interrupted Claude.
    win.document.addEventListener('keydown',ev=>{
      if(!(ev.ctrlKey||ev.metaKey)||(ev.key!=='c'&&ev.key!=='C')) return;
      let sel=''; try{ sel=term.getSelection(); }catch(e){}
      if(!sel) return;                        // no selection: let Ctrl-C interrupt
      win.__lastSel=null; copySel('key');
      ev.preventDefault(); ev.stopPropagation();
    },true);

    // paste: text is ttyd's own business and already works. An image is not
    // text, so it is uploaded and what lands in the prompt is its path -- which
    // is what Claude Code opens.
    win.document.addEventListener('paste',ev=>{
      const items=(ev.clipboardData&&ev.clipboardData.items)||[];
      for(const it of items){
        if(it.kind!=='file'||it.type.indexOf('image/')!==0) continue;
        const blob=it.getAsFile(); if(!blob) return;
        ev.preventDefault(); ev.stopPropagation();
        fetch('/api/pasteimage',{method:'POST',headers:{'Content-Type':blob.type},body:blob})
          .then(r=>r.json())
          .then(d=>{ if(d.path){ try{ term.paste(d.path+' '); }catch(e){} }
                     else if(d.error){ alert('image paste failed: '+d.error); } })
          .catch(e=>alert('image paste failed: '+e));
        return;
      }
    },true);
  })();
}
// Claude Code selects with the mouse itself -- xterm never sees a selection --
// and copies what was highlighted into a tmux buffer on the box. So the text to
// put on the clipboard comes from there, not from the terminal widget. Poll for
// a new buffer and write it from inside the iframe, which is the focused
// document; a write from the parent is refused for exactly that reason.
function watchBuffer(win){
  if(win.__bufWatch) return;
  win.__bufWatch=1;
  let seen='', pending=null, reported=false;
  const flush=()=>{
    if(!pending) return;
    const text=pending;
    let p=null;
    try{ p=win.navigator.clipboard.writeText(text); }catch(e){ p=null; }
    if(p&&p.then){
      p.then(()=>{ pending=null; toast('copied '+text.length+' chars');
                   if(!reported){reported=true;clipReport({stage:'buffer-copy',ok:true,n:text.length});} },
             e=>{ if(legacyCopy(win,text)){ pending=null; toast('copied '+text.length+' chars'); }
                  if(!reported){reported=true;clipReport({stage:'buffer-copy',ok:false,err:String(e)});} });
    } else if(legacyCopy(win,text)){
      pending=null; toast('copied '+text.length+' chars');
    }
  };
  // Any interaction is a user gesture, which is what a refused write needs.
  ['mouseup','keydown','mousedown'].forEach(ev=>win.document.addEventListener(ev,flush,true));
  setInterval(()=>{
    fetch('/api/tmuxbuffer?seen='+encodeURIComponent(seen)).then(r=>r.json()).then(d=>{
      if(!d||!d.ok||d.unchanged||!d.text) return;
      seen=d.id; pending=d.text; flush();
    }).catch(()=>{});
  },800);
}

// The half that does not need a user gesture: tmux's buffer, and saying so.
function sendSel(win,sel,why){
  fetch('/api/copybuffer',{method:'POST',headers:{'Content-Type':'application/json'},
                           body:JSON.stringify({text:sel,thread_id:window.__chat||null,why:why})})
    .catch(()=>{});
  toast('copied '+sel.length+' chars');
}
let __toastT=null;
function toast(msg){
  let el=document.getElementById('toast');
  if(!el){ el=document.createElement('div'); el.id='toast'; document.body.appendChild(el); }
  el.textContent=msg; el.classList.add('on');
  clearTimeout(__toastT); __toastT=setTimeout(()=>el.classList.remove('on'),1600);
}
function legacyCopy(win,text){
  // returns true only if the browser says the copy actually happened
  // clipboard.writeText needs permission and a focused document, and refuses in
  // some browsers inside an iframe. This path asks for neither.
  try{
    const ta=win.document.createElement('textarea');
    ta.value=text; ta.style.position='fixed'; ta.style.opacity='0';
    win.document.body.appendChild(ta);
    ta.focus(); ta.select(); ta.setSelectionRange(0, text.length);
    const ok=win.document.execCommand('copy');
    ta.remove();
    return !!ok;
  }catch(e){ return false; }
}

document.addEventListener('DOMContentLoaded',()=>{
  drawFresh(); drawTerm(); drawQueue(window.__feed);
  // 6s, not 20: the dot is the only thing saying whether Claude is working,
  // and a light that lags twenty seconds behind is worse than none. Each poll
  // is a capture-pane per conversation, which is cheap.
  loadChats().then(focusRecent); setInterval(loadChats, 6000);
  loadUsage(); setInterval(loadUsage, 300000);
  // Attaching to whatever is already serving is focusRecent()'s job, called
  // above with the chat list -- the one place that does it.
  const rb=document.getElementById('dorefresh');
  if(rb) rb.onclick=async()=>{
    document.getElementById('status').textContent='refreshing…';
    await fetch('/api/refresh',{method:'POST'});
  };
  // Same action, beside the thing it affects. The queue is the card whose
  // contents go stale between hourly fetches, so the button belongs here too.
  const qb=document.getElementById('qrefetch');
  if(qb) qb.onclick=async()=>{
    qb.disabled=true; qb.textContent='fetching…';
    document.getElementById('status').textContent='refreshing…';
    await fetch('/api/refresh',{method:'POST'});
    setTimeout(()=>{qb.disabled=false; qb.textContent='refetch';},1500);
  };
  const box=document.getElementById('termstart');
  if(box) box.addEventListener('click',async ev=>{
    const btn=ev.target.closest('.startbtn'); if(!btn||btn.disabled) return;
    const mode=btn.dataset.mode;
    // Only `queue` discards a live conversation; resume and catchup join it.
    if(window.__session && mode==='queue'
       && !confirm('Start a new Claude conversation? The current one is closed.')) return;
    btn.disabled=true;
    document.getElementById('termstate').textContent='starting…';
    const r=await fetch('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},
                                        body:JSON.stringify({mode:mode})}).then(r=>r.json());
    btn.disabled=false;
    if(!r.ok){document.getElementById('termstate').textContent=r.error||'failed';return;}
    window.__session=true; window.__mounted=true;
    if(r.thread_id) window.__chat=r.thread_id;
    drawTerm();
    const st=document.getElementById('termstate');
    st.dataset.said='1';
    st.textContent = r.resumed ? (r.primed ? 'resumed, queue handed over' : 'resumed')
                               : (r.primed ? 'new conversation, queue injected' : 'new conversation');
    mountTerm(r.port);
  });
  const nb=document.getElementById('termnew');
  if(nb) nb.onclick=async()=>{
    // Adds a conversation; closes nothing. Several run at once now, so starting
    // one is no longer a decision about the one you were in -- it opens its own
    // Discord thread and joins the list beside the others.
    nb.disabled=true;
    const old=nb.textContent; nb.textContent='starting\u2026';
    try{
      const r=await fetch('/api/newconversation',{method:'POST'}).then(x=>x.json());
      if(r.ok&&r.port){
        window.__chat=r.thread_id; window.__session=true; window.__mounted=true;
        const st=document.getElementById('termstate'); st.dataset.said='1';
        st.textContent='new conversation';
        mountTerm(r.port); drawTerm();
      } else if(r.error){ alert('could not start a conversation: '+r.error); }
      await loadChats();
    } finally { nb.disabled=false; nb.textContent=old; }
  };
  const c=document.getElementById('termcard'), b=document.getElementById('termfull');
  if(b){b.onclick=()=>{c.classList.toggle('full'); b.textContent=c.classList.contains('full')?'exit':'fullscreen';};}
  document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&c&&c.classList.contains('full')){c.classList.remove('full');b.textContent='fullscreen';}});
});
"""


TICKJS = """
// Tasks in "What to work on" are still ticked by hand -- that box writes back to
// the markdown and the ledger sees the close, so it records a real completion.
// The queue card's boxes are gone; there is no .qtick left to skip past here.
document.addEventListener('click', async e=>{
  const b = e.target.closest('.tick'); if(!b || b.classList.contains('ghost')) return;
  const li = b.closest('li'); if(!li) return;
  li.classList.toggle('crossed');
  b.innerHTML = li.classList.contains('crossed') ? '\u2713' : '\u25a1';
  await fetch('/api/done', {method:'POST', headers:{'Content-Type':'application/json'},
                            body: JSON.stringify({key: b.dataset.key})});
});
document.addEventListener('click', e=>{
  if(!e.target.closest('#qfold')) return;
  window.__showdone = !window.__showdone;
  drawQueue(window.__feed);
});
"""
