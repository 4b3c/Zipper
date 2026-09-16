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

const api = globalThis.browser ?? globalThis.chrome;

const REFRESH_MS = 5 * 60 * 1000;

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
:host { all: initial; }
* { box-sizing: border-box; font-family: LatoWeb, Lato, system-ui, sans-serif; }
.wrap { margin: 0 0 1.5rem; color: #2d3b45; }
h2 { font-size: 1rem; font-weight: 700; margin: 0 0 .5rem;
     display: flex; align-items: baseline; gap: .5rem; }
h2 .src { font-size: .7rem; font-weight: 400; color: #6b7780; }
ul { list-style: none; margin: 0; padding: 0; border-top: 1px solid #e8eaec; }
li { display: flex; gap: .55rem; padding: .5rem .1rem;
     border-bottom: 1px solid #e8eaec; align-items: flex-start; }
li.done { opacity: .45; }
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
  for (const sel of SUPERSEDED) {
    for (const el of document.querySelectorAll(sel)) {
      el.style.display = 'none';
    }
  }
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
    due.textContent = whenText(it.due);
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

function draw(items, error) {
  const wrap = document.createElement('div');
  wrap.className = 'wrap';
  const h = document.createElement('h2');
  h.textContent = 'To Do';
  const src = document.createElement('span');
  src.className = 'src';
  src.textContent = 'zipper';
  h.appendChild(src);
  wrap.appendChild(h);

  if (error) {
    const p = document.createElement('div');
    p.className = 'err';
    p.textContent = 'Zipper unreachable — showing nothing rather than guessing.';
    wrap.appendChild(p);
  } else if (!items.length) {
    const p = document.createElement('div');
    p.className = 'empty';
    p.textContent = 'Nothing outstanding.';
    wrap.appendChild(p);
  } else {
    const ul = document.createElement('ul');
    for (const it of items) ul.appendChild(row(it));
    wrap.appendChild(ul);
  }
  root.lastChild.replaceWith(wrap);
}

async function refresh() {
  if (!root) return;
  const out = await zipper('/api/worklist');
  if (!out || out.ok === false) {
    draw([], true);
    return;
  }
  draw(out.items || []);
  hideNative();
}

/* The sidebar arrives late and can be replaced under us, so the panel is
 * re-mounted whenever it goes missing rather than placed once at load.
 */
function ensure() {
  if (!onDashboard()) return;
  const anchor = document.querySelector(ANCHOR);
  if (!anchor) return;
  if (host && anchor.contains(host)) {
    hideNative();          // React re-renders reinstate the widgets we hid
    return;
  }
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
