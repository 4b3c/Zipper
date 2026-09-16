/* Zipper's work list, drawn over Canvas' own.
 *
 * Why replace it at all
 * --------------------
 * Canvas' right-hand sidebar answers a question nobody asked. "To Do" is
 * whatever the gradebook has an ungraded column for, so it carries closed
 * assignments, things submitted elsewhere, and ungraded participation credit,
 * while the work that actually matters this week sits below a fold with no
 * ordering worth the name. The list Zipper already computes is the one he
 * reads every morning; this puts it where he is when the question comes up.
 *
 * What this file is not
 * ---------------------
 * It is not a collector and it is not a ranker. It does not decide what is
 * pressing -- `/api/worklist` is `data.ranked()`, the same call behind the
 * dashboard's "What to work on" card, and crossing a row off here POSTs to
 * `/api/done`, the same endpoint the dashboard's checkbox uses. So the vault
 * stays the one store and there is exactly one opinion about ordering. A
 * second ranking computed in the browser would be a second system that thinks,
 * and the two would disagree on exactly the days it mattered.
 *
 * Rendering in a shadow root is not decoration. Canvas ships broad, specific,
 * !important-heavy CSS and this panel lives inside its sidebar; an open DOM
 * here would be restyled by whatever Canvas changes next, silently and
 * remotely.
 */
/* Wrapped, because every content script on a page shares one global scope.
 *
 * Chrome and Firefox give an extension a single isolated world per frame, not
 * one per file, so two content scripts that both say `const api = ...` at top
 * level are two declarations of the same binding -- and the second one does not
 * merely lose, it throws `Identifier 'api' has already been declared` and the
 * whole file never evaluates. It fails at line 1, before any of its own code
 * runs, so the symptom is a script that is plainly listed in the manifest and
 * plainly does nothing.
 *
 * `node --check` cannot see it: each file is valid alone and the collision only
 * exists once the browser has loaded both. So the fix is structural rather than
 * a rename -- a closure per file means the README's "adding a site is one file
 * plus a content_scripts entry" stays true no matter what the next file calls
 * its variables.
 */
(() => {
  const api = globalThis.browser ?? globalThis.chrome;

  const REFRESH_MS = 5 * 60 * 1000;

  /* Loud, unlike the collector.
   *
   * A collector that fails quietly is correct -- nobody is watching Canvas for
   * evidence that a background read happened, and the data going stale is the
   * signal. A *panel* that fails quietly is not: he is looking straight at the
   * place it should be, and "nothing there" has half a dozen causes that look
   * identical from the sidebar. Every decision this file makes says so.
   */
  const log = (...a) => console.info('[zipper panel]', ...a);
  log('loaded on', location.pathname);

  // `ensure` runs on every animation frame the page mutates, so anything it
  // reports has to be said once or not at all. Declared up here with `log`
  // because callers appear long before the bottom of the file.
  const said = new Set();
  function once(key, ...msg) {
    if (said.has(key)) return;
    said.add(key);
    log(...msg);
  }

  // Canvas renders the sidebar client-side and re-renders it on navigation, so
  // the anchor is not there at document_idle and does not stay there once found.
  const ANCHOR = '#right-side';

  /* The native widgets this replaces.
   *
   * Hidden rather than removed: a removed node is gone when React re-renders and
   * reinstates it anyway, and hiding leaves the page intact for anyone who turns
   * the extension off. Nothing outside this list is touched -- the dashboard
   * option buttons and Recent Feedback are Canvas' to keep.
   */
  const SUPERSEDED = ['.Sidebar__TodoListContainer', '.todo-list',
                      '.coming_up', '.events_list'];

  function onDashboard() {
    return /^\/(dashboard)?\/?$/.test(location.pathname);
  }

  async function zipper(path, body) {
    try {
      return await api.runtime.sendMessage({ type: 'zipper:call', path, body });
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  }

  /* Dates the way he would say them.
   *
   * "2026-09-18" is a fact about a database; "Thursday" is a fact about his week.
   * Overdue counts up rather than down because how far past is the part that
   * changes what he does about it.
   */
  function whenText(due) {
    if (!due) return '';
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const d = new Date(due + 'T00:00:00');
    const days = Math.round((d - today) / 86400000);
    if (days < 0) return days === -1 ? '1 day late' : `${-days} days late`;
    if (days === 0) return 'today';
    if (days === 1) return 'tomorrow';
    if (days <= 6) return d.toLocaleDateString(undefined, { weekday: 'long' });
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  const CSS = `
  /* "all: initial" is what walls Canvas' cascade out, but it also resets
     display to inline, which collapses the panel. Put it back.
     (No backticks in here -- this whole block is a template literal.) */
  :host { all: initial; display: block; }
  * { box-sizing: border-box; font-family: LatoWeb, Lato, system-ui, sans-serif; }

  /* The panel sits at the very top of #right-side, which starts level with the
     ASU utility nav -- so with no padding the heading collides with it and the
     whole thing reads as cramped. This is the breathing room Canvas' own
     sidebar gets from its widget margins and ours had to ask for. */
  .wrap { padding: 1.5rem 0 1.25rem; color: #2d3b45; }

  header { display: flex; align-items: baseline; justify-content: space-between;
           gap: .5rem; margin-bottom: .9rem; }
  h2 { font-size: 1.05rem; font-weight: 700; margin: 0; letter-spacing: -.01em; }
  .span { font-size: .72rem; color: #6b7780; white-space: nowrap; }

  /* How much of the week is behind him, which is the one number the list
     itself cannot show -- a list of what is left says nothing about what is
     done. */
  .prog { margin-bottom: 1rem; }
  .bar { height: 8px; border-radius: 99px; background: #e8eaec; overflow: hidden; }
  .bar i { display: block; height: 100%; background: #0b874b;
           border-radius: 99px; transition: width .3s ease; }
  .num { display: flex; justify-content: space-between; align-items: baseline;
         margin-top: .4rem; font-size: .72rem; color: #6b7780; }
  .num b { color: #2d3b45; font-size: .82rem; }

  .tabs { display: flex; gap: .2rem; margin-bottom: .6rem;
          background: #f2f4f6; padding: .2rem; border-radius: 6px; }
  .tabs button { flex: 1; border: 0; background: transparent; font: inherit;
                 font-size: .78rem; font-weight: 700; color: #6b7780;
                 padding: .4rem .3rem; border-radius: 4px; cursor: pointer;
                 display: flex; justify-content: center; gap: .3rem; }
  .tabs button.on { background: #fff; color: #2d3b45;
                    box-shadow: 0 1px 2px rgba(0,0,0,.14); }
  .tabs .ct { font-weight: 400; opacity: .7; }

  ul { list-style: none; margin: 0; padding: 0; }
  li { display: flex; gap: .6rem; padding: .6rem .1rem;
       border-bottom: 1px solid #e8eaec; align-items: flex-start; }
  li:last-child { border-bottom: 0; }
  li.done { opacity: .5; }
  li.done .title { text-decoration: line-through; }
  input[type=checkbox] { margin: .2rem 0 0; flex: none; cursor: pointer; }
  .body { min-width: 0; flex: 1; }
  .title { display: block; font-size: .85rem; line-height: 1.3;
           color: #2d3b45; text-decoration: none; word-wrap: break-word; }
  a.title:hover { color: #0374b5; text-decoration: underline; }
  .meta { font-size: .72rem; color: #6b7780; margin-top: .15rem;
          display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; }
  .due.overdue { color: #e0061f; font-weight: 700; }
  .due.today { color: #c5731a; font-weight: 700; }
  .badge { background: #f2f4f6; border-radius: 3px; padding: 0 .3rem;
           font-size: .68rem; }
  .badge.elsewhere { background: #fdf3e0; color: #8c5a12; }
  .empty, .err { font-size: .8rem; color: #6b7780; padding: .5rem 0; }
  .err { color: #8c5a12; }
  `;

  let host = null;
  let root = null;

  function mount(anchor) {
    host = document.createElement('div');
    host.id = 'zipper-worklist';
    root = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = CSS;
    root.append(style, document.createElement('div'));
    anchor.prepend(host);
  }

  /* Canvas' list is hidden only once ours has something to show.
   *
   * If Zipper is unreachable -- off the tailnet, service down -- the right
   * outcome is the page he already had, not an empty box where his work used to
   * be. So this is called from the success path and nowhere else, and the panel
   * says what went wrong in the space it already occupies.
   */
  function hideNative() {
    let n = 0;
    for (const sel of SUPERSEDED) {
      for (const el of document.querySelectorAll(sel)) {
        el.style.display = 'none';
        n++;
      }
    }
    once('hid', n ? `hid ${n} native widget(s)`
         : 'matched none of ' + SUPERSEDED.join(', ') + ' — the panel is drawn '
           + 'but Canvas’ own list is still there. Inspect the sidebar and '
           + 'correct SUPERSEDED.');
  }

/* Grades back on the course cards.
 *
 * The one thing here that never touches Zipper. It is a number Canvas already
 * computed, read same-origin and printed verbatim onto Canvas' own card -- no
 * ranking, no storage, nothing entering the vault. Sending it to the backend
 * would be a different feature (grades as a tracked metric) and is not this
 * one; a course score is not a conclusion about anything, and the vault holds
 * conclusions.
 *
 * These badges are injected into Canvas' DOM rather than the shadow root,
 * because they belong to cards this file does not own. Hence inline styles:
 * a class would be at the mercy of Canvas' stylesheet.
 */
  let scores = null;

  async function grades() {
    try {
      const res = await fetch('/api/v1/courses?enrollment_state=active'
                              + '&include[]=total_scores&per_page=100',
        { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error('courses returned ' + res.status);
      let text = await res.text();
      if (text.startsWith('while(1);')) text = text.slice(9);
      const out = {};
      for (const c of JSON.parse(text)) {
        // A student enrollment specifically: he is an observer or TA nowhere,
        // but `enrollments[0]` would be a guess and this is not.
        const e = (c.enrollments || []).find((x) => x.type === 'student');
        if (e) out[String(c.id)] = e.computed_current_score;
      }
      return out;
    } catch (e) {
      log('could not read grades:', e);
      return {};
    }
  }

  const BADGE = 'position:absolute;top:8px;left:8px;z-index:3;pointer-events:none;'
    + 'background:rgba(255,255,255,.94);color:#2d3b45;border-radius:99px;'
    + 'padding:3px 8px;font:700 12px/1.2 LatoWeb,Lato,system-ui,sans-serif;'
    + 'box-shadow:0 1px 3px rgba(0,0,0,.25);';

  function decorateCards() {
    if (!scores) return;
    let added = 0, seen = 0;
    for (const card of document.querySelectorAll('.ic-DashboardCard')) {
      const a = card.querySelector('a[href*="/courses/"]');
      const m = a && a.getAttribute('href').match(/\/courses\/(\d+)/);
      if (!m) continue;
      seen++;
      // Canvas shows no score until something is graded, and "--%" is the
      // honest rendering of that. A 0% would be a different and wrong claim.
      const s = scores[m[1]];
      const text = (s === null || s === undefined) ? '--%' : s + '%';
      const have = card.querySelector('.zipper-grade');
      if (have) {
        if (have.textContent !== text) have.textContent = text;
        continue;
      }
      if (getComputedStyle(card).position === 'static') card.style.position = 'relative';
      const badge = document.createElement('div');
      badge.className = 'zipper-grade';
      badge.style.cssText = BADGE;
      badge.textContent = text;
      card.appendChild(badge);
      added++;
    }
    if (added) log('put grades on', added, 'of', seen, 'cards');
    else if (seen) once('cards', 'found', seen, 'course cards');
    else once('nocards', 'no .ic-DashboardCard on the page — grade badges '
              + 'need that selector corrected.');
  }

  function row(it) {
    const li = document.createElement('li');
    if (it.done) li.className = 'done';

    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !!it.done;
    box.addEventListener('change', async () => {
      box.disabled = true;
      // Optimistic, then reconciled by the refetch: the vault decides what the
      // new state is, and a failed write must not leave a box looking ticked.
      li.classList.toggle('done', box.checked);
      const out = await zipper('/api/done', { key: it.key });
      if (out.ok === false) {
        box.checked = !box.checked;
        li.classList.toggle('done', box.checked);
      }
      box.disabled = false;
      refresh();
    });

    const body = document.createElement('div');
    body.className = 'body';
    const title = document.createElement(it.url ? 'a' : 'span');
    title.className = 'title';
    title.textContent = it.title;
    if (it.url) { title.href = it.url; title.target = '_top'; }
    body.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'meta';
    if (it.tag) {
      const tag = document.createElement('span');
      tag.className = 'badge';
      tag.textContent = it.tag;
      meta.appendChild(tag);
    }
    if (it.due) {
      const due = document.createElement('span');
      due.className = 'due' + (it.overdue ? ' overdue'
                               : whenText(it.due) === 'today' ? ' today' : '');
      // Carried work is from an earlier week, so a weekday name would be a lie
      // by omission -- "Friday" reads as this coming Friday.
      due.textContent = it.carried
        ? new Date(it.due + 'T00:00:00')
            .toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
          + ' · still open'
        : whenText(it.due);
      meta.appendChild(due);
    }
    // The reason a Canvas row can say `submitted: false` forever. Worth a badge:
    // it is the difference between "not done" and "Canvas cannot see it".
    if (it.elsewhere) {
      const el = document.createElement('span');
      el.className = 'badge elsewhere';
      el.textContent = it.elsewhere;
      meta.appendChild(el);
    }
    if (meta.children.length) body.appendChild(meta);

    li.append(box, body);
    return li;
  }

  function spanOf(week) {
    if (!week || !week.monday) return '';
    const f = (s) => new Date(s + 'T00:00:00')
      .toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    return f(week.monday) + ' – ' + f(week.sunday);
  }

/* Which tab is showing, kept outside `draw` so a refresh does not throw him
 * back to To Do while he is reading Done.
 */
  let tab = 'todo';

  /* Two tabs rather than one list, because sinking done work was not enough.
   *
   * `week_worklist` orders by day and sinks finished work *within* a day, so a
   * Monday assignment handed in on Monday still sits above an open one due
   * Sunday. In a real week that meant ten struck-through Sprint 0 rows above
   * the two things he actually had to do -- the panel was technically correct
   * and useless. Splitting them means the default view is only what is left,
   * and the finished work is one click away rather than in the way.
   */
  function draw(items, week, error) {
    const wrap = document.createElement('div');
    wrap.className = 'wrap';

    const head = document.createElement('header');
    const h = document.createElement('h2');
    h.textContent = 'This week';
    const span = document.createElement('span');
    span.className = 'span';
    span.textContent = spanOf(week) || 'zipper';
    head.append(h, span);
    wrap.appendChild(head);

    if (error) {
      const p = document.createElement('div');
      p.className = 'err';
      p.textContent = 'Zipper unreachable — showing nothing rather than guessing.';
      wrap.appendChild(p);
      root.lastChild.replaceWith(wrap);
      return;
    }

    const done = items.filter((i) => i.done);
    const todo = items.filter((i) => !i.done);
    const pct = items.length ? Math.round((done.length / items.length) * 100) : 0;

    const prog = document.createElement('div');
    prog.className = 'prog';
    const bar = document.createElement('div');
    bar.className = 'bar';
    const fill = document.createElement('i');
    fill.style.width = pct + '%';
    bar.appendChild(fill);
    const num = document.createElement('div');
    num.className = 'num';
    const b = document.createElement('b');
    b.textContent = pct + '%';
    const frac = document.createElement('span');
    frac.textContent = `${done.length} / ${items.length} done`;
    num.append(b, frac);
    prog.append(bar, num);
    wrap.appendChild(prog);

    const tabs = document.createElement('nav');
    tabs.className = 'tabs';
    for (const [key, label, n] of [['todo', 'To Do', todo.length],
                                   ['done', 'Done', done.length]]) {
      const btn = document.createElement('button');
      btn.className = key === tab ? 'on' : '';
      btn.textContent = label;
      const ct = document.createElement('span');
      ct.className = 'ct';
      ct.textContent = n;
      btn.appendChild(ct);
      btn.addEventListener('click', () => { tab = key; draw(items, week); });
      tabs.appendChild(btn);
    }
    wrap.appendChild(tabs);

    const shown = tab === 'done' ? done : todo;
    if (!shown.length) {
      const p = document.createElement('div');
      p.className = 'empty';
      p.textContent = tab === 'done'
        ? 'Nothing finished yet this week.'
        : items.length ? 'All clear — everything this week is done.'
                       : 'Nothing due this week.';
      wrap.appendChild(p);
    } else {
      const ul = document.createElement('ul');
      for (const it of shown) ul.appendChild(row(it));
      wrap.appendChild(ul);
    }
    root.lastChild.replaceWith(wrap);
  }

  async function refresh() {
    if (!root) return;
    const out = await zipper('/api/worklist');
    if (!out || out.ok === false) {
      log('could not reach zipper:', (out && out.error) || 'no reply from the '
          + 'background — is the endpoint saved in the extension options?');
      draw([], null, true);
      return;
    }
    const items = out.items || [];
    log('got', items.length, 'items for', out.monday, '->', out.sunday);
    draw(items, out);
    hideNative();
    scores = await grades();
    decorateCards();
  }

  /* The sidebar arrives late and can be replaced under us, so the panel is
   * re-mounted whenever it goes missing rather than placed once at load.
   */
  function ensure() {
    if (!onDashboard()) {
      once('path', 'not the dashboard, standing down:', location.pathname);
      return;
    }
    const anchor = document.querySelector(ANCHOR);
    if (!anchor) {
      once('anchor', 'no', ANCHOR, 'on the page yet — waiting. If this never '
           + 'clears, the anchor selector is wrong for this Canvas.');
      return;
    }
    if (host && anchor.contains(host)) {
      hideNative();          // React re-renders reinstate the widgets we hid
      decorateCards();       // ...and drop the badges off the cards
      return;
    }
    once('mount', 'mounting into', ANCHOR);
    mount(anchor);
    refresh();
  }

  /* Coalesced: Canvas mutates its own DOM constantly and `ensure` would
   * otherwise run hundreds of times a second doing nothing. Only `childList` is
   * observed, which is also what keeps `hideNative`'s style writes from
   * retriggering the observer that called it.
   */
  let pending = false;
  function scheduleEnsure() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; ensure(); });
  }

  ensure();
  new MutationObserver(scheduleEnsure)
    .observe(document.body, { childList: true, subtree: true });
  setInterval(refresh, REFRESH_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
})();
