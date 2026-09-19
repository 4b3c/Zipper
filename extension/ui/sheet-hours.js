/* The timesheet bridge, on the Google Sheet itself.
 *
 * WHY THIS IS NOT JUST TYPING INTO THE CELLS
 *
 * Google Sheets renders its grid to a <canvas>. There is no DOM node per cell
 * to read or write, so the obvious version of this -- find row 31, set its
 * cells -- cannot be built at all, however carefully. The two ways to actually
 * put a value in a cell are the Sheets API (OAuth) or an Apps Script bound to
 * the document; both are real options and neither is an extension typing.
 *
 * What an extension *can* do reliably is the clipboard. Sheets' own paste
 * handler parses TSV into cells, and a paste is exactly the gesture that
 * already works: click the first empty row, hit paste, done. So this panel
 * carries the rows to the clipboard and then records that they landed.
 *
 * It collects and it renders. It never decides what an hour is -- the cells
 * arrive already formatted by `zipper.hours`, including his 24-hour-only-
 * across-noon convention, because that rule belongs with the ledger.
 */
const api = globalThis.browser ?? globalThis.chrome;

const call = (path, method, body) =>
  api.runtime.sendMessage({ type: 'zipper:call', path, method, body });

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

let PENDING = [];

function tsv(rows) {
  // Sheets pastes a tab-separated block as cells. The trailing empty Submitted
  // column is deliberate: it keeps the paste six wide, so a row never lands
  // shifted if he pastes into column A.
  return rows.map((r) => r.cells.join('\t')).join('\n');
}

async function refresh(panel) {
  const res = await call('/api/hours', 'GET');
  PENDING = (res && res.pending) || [];
  render(panel);
}

function render(panel) {
  panel.textContent = '';
  const head = el('div', 'zh-head');
  head.append(el('strong', null, 'Zipper — hours'),
              el('span', 'zh-count',
                 PENDING.length ? `${PENDING.length} to add` : 'nothing pending'));
  panel.append(head);

  if (!PENDING.length) {
    panel.append(el('div', 'zh-empty', 'The sheet has everything Zipper knows about.'));
    return;
  }

  const list = el('div', 'zh-list');
  for (const r of PENDING) {
    const row = el('div', 'zh-row');
    row.append(el('span', 'zh-date', r.cells[0]),
               el('span', 'zh-time', r.cells[1] ? `${r.cells[1]}–${r.cells[2]}` : ''),
               el('span', 'zh-hrs', r.cells[3].replace(/:00$/, '')),
               el('span', 'zh-note', r.cells[4]));
    list.append(row);
  }
  panel.append(list);

  const copy = el('button', 'zh-btn', 'Copy rows');
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(tsv(PENDING));
      copy.textContent = 'Copied — click the first empty row and paste';
    } catch (e) {
      copy.textContent = 'Clipboard refused — click the page first';
    }
  });

  // Two buttons on purpose. Copying is not evidence that anything landed in
  // the sheet, and the whole point of the ledger is that an hour stays pending
  // until something says otherwise. He is that something, for now.
  const done = el('button', 'zh-btn zh-ok', 'I pasted them');
  done.addEventListener('click', async () => {
    done.disabled = true;
    const rows = PENDING.map((r) => ({
      date: isoOf(r.cells[0]), start: null, end: null,
      hours: hoursOf(r.cells[3]), note: r.cells[4], submitted: '',
    }));
    // No `complete` flag: this is a list of additions, not a reading of the
    // whole tab, and a snapshot that does not claim completeness can never
    // delete anything.
    const res = await call('/api/hours', 'POST', { rows, tab: tabName() });
    done.textContent = res && res.ok ? 'Recorded' : ('Failed: ' + (res && res.error));
    if (res && res.ok) setTimeout(() => refresh(panel), 600);
  });

  const acts = el('div', 'zh-acts');
  acts.append(copy, done);
  panel.append(acts);
}

function isoOf(mdy) {
  const [m, d, y] = mdy.split('/');
  return `${y}-${m}-${d}`;
}

function hoursOf(hms) {
  const [h, m] = hms.split(':').map(Number);
  return Math.round((h + m / 60) * 10000) / 10000;
}

function tabName() {
  const t = document.querySelector('.docs-sheet-active-tab .docs-sheet-tab-name');
  return t ? t.textContent.trim() : null;
}

function mount() {
  if (document.getElementById('zipper-hours')) return;
  const panel = el('div', null);
  panel.id = 'zipper-hours';
  const style = el('style');
  style.textContent = `
    #zipper-hours{position:fixed;right:14px;bottom:14px;width:320px;z-index:999;
      background:#fff;border:1px solid #dadce0;border-radius:10px;padding:10px 12px;
      font:12px/1.45 Roboto,Arial,sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.18)}
    #zipper-hours .zh-head{display:flex;justify-content:space-between;align-items:baseline;
      margin-bottom:6px}
    #zipper-hours .zh-count{color:#5f6368}
    #zipper-hours .zh-empty{color:#5f6368}
    #zipper-hours .zh-list{max-height:180px;overflow:auto;margin-bottom:8px}
    #zipper-hours .zh-row{display:grid;grid-template-columns:62px 68px 34px 1fr;gap:4px;
      padding:3px 0;border-top:1px solid #f1f3f4}
    #zipper-hours .zh-note{color:#5f6368;overflow:hidden;text-overflow:ellipsis;
      white-space:nowrap}
    #zipper-hours .zh-acts{display:flex;gap:6px}
    #zipper-hours .zh-btn{flex:1;padding:6px;border:1px solid #dadce0;border-radius:6px;
      background:#f8f9fa;cursor:pointer;font:inherit}
    #zipper-hours .zh-ok{background:#e8f0fe;border-color:#c6dafc}
  `;
  document.documentElement.append(style, panel);
  refresh(panel);
}

/* Which document this is allowed to touch is a setting, not a match pattern.
 *
 * The manifest lives in a public repo, so it matches spreadsheets in general
 * and names none of them: a document id is not a credential but it is a
 * pointer straight at a private file, and there is no reason for the id of his
 * timesheet to be readable by anyone who clones Zipper. The options page holds
 * the real one, and this refuses to mount anywhere else.
 */
async function wanted() {
  const here = location.pathname.match(/\/spreadsheets\/d\/([^/]+)/);
  if (!here) return false;
  const { sheetId } = await api.storage.sync.get('sheetId');
  return Boolean(sheetId) && sheetId === here[1];
}

// Sheets builds its chrome late and swaps tabs without a navigation, so mount
// once the document settles rather than at document_idle alone.
setTimeout(async () => { if (await wanted()) mount(); }, 1500);
