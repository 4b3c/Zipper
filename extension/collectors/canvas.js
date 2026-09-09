/* Canvas planner items -- the only source that knows submitted vs due.
 *
 * Why this is a content script and not a background fetch
 * ------------------------------------------------------
 * It has to run *in the page*. `canvas_session` is a SameSite cookie, so a
 * request issued from the extension's own background context is cross-site and
 * the browser would leave the cookie behind -- Canvas then answers with the SSO
 * login page, at status 200, and the caller happily parses a login form as an
 * empty planner. From here the request is same-origin and the session rides
 * along exactly as it does for Canvas' own JavaScript.
 *
 * That is the same trick `/bookmarklet` has always used, which is the point:
 * this is a proven request being made automatically instead of by hand. The
 * server endpoint it posts to is the one the bookmarklet already talks to.
 *
 * The cost of doing it this way is honest and worth stating: it only runs while
 * he has Canvas open. Nothing here can make the data fresher than his browsing.
 * Zipper is told when the reading was taken so it can say how old it is, rather
 * than implying a currency it does not have.
 */

const api = globalThis.browser ?? globalThis.chrome;

const WINDOW_BACK = 14;      // enough to still see something submitted last week
const WINDOW_FORWARD = 120;  // to the end of a semester
const REFRESH_MS = 30 * 60 * 1000;

function iso(offsetDays) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

/* Canvas caps per_page at 100 and paginates with a Link header. Ignoring
 * rel="next" silently truncates a busy month, and the failure looks like a
 * light workload rather than like an error.
 */
async function planner() {
  let url = `/api/v1/planner/items?start_date=${iso(-WINDOW_BACK)}`
          + `&end_date=${iso(WINDOW_FORWARD)}&per_page=100`;
  const all = [];
  while (url && all.length < 1000) {
    const res = await fetch(url, { credentials: 'same-origin',
                                   headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('canvas returned ' + res.status);

    // Canvas prefixes JSON with `while(1);` against JSON hijacking. It is not
    // valid JSON until that comes off.
    let text = await res.text();
    if (text.startsWith('while(1);')) text = text.slice(9);

    const page = JSON.parse(text);
    if (!Array.isArray(page)) break;
    all.push(...page);

    const link = res.headers.get('Link') || '';
    const next = link.match(/<([^>]+)>;\s*rel="next"/);
    url = next ? next[1] : null;
  }
  return all;
}

/* Assignment bodies, per course.
 *
 * Not a nicety. Some courses keep the actual work on PrairieLearn, Gradescope
 * or zyBooks and leave Canvas holding an empty shell with a grade column, so
 * `submitted: false` on those is not evidence of anything -- Canvas cannot see
 * a submission it never received. The backend spots that by reading the
 * description, which means the description has to arrive. Without this, every
 * externally-hosted assignment reads as outstanding forever.
 *
 * One request per course, and only for courses that actually appear in the
 * planner.
 */
async function descriptions(courseIds) {
  const out = {};
  for (const cid of courseIds) {
    try {
      let url = `/api/v1/courses/${cid}/assignments?per_page=100`;
      const rows = [];
      while (url && rows.length < 500) {
        const res = await fetch(url, { credentials: 'same-origin',
                                       headers: { Accept: 'application/json' } });
        if (!res.ok) break;
        let text = await res.text();
        if (text.startsWith('while(1);')) text = text.slice(9);
        const page = JSON.parse(text);
        if (!Array.isArray(page)) break;
        rows.push(...page);
        const next = (res.headers.get('Link') || '').match(/<([^>]+)>;\s*rel="next"/);
        url = next ? next[1] : null;
      }
      // Only the two fields the backend indexes on, and the body. Sending whole
      // assignment objects would be a lot of bytes for no extra meaning.
      out[cid] = rows.map((a) => ({ id: a.id, name: a.name, description: a.description }));
    } catch (e) {
      // A single unreadable course must not cost the whole reading.
      console.debug('[zipper] canvas descriptions, course', cid, e);
    }
  }
  return out;
}

async function run() {
  try {
    const items = await planner();
    if (!items.length) return;
    const courseIds = [...new Set(items.map((i) => i.course_id).filter(Boolean))];
    const assignments = await descriptions(courseIds);
    api.runtime.sendMessage({ type: 'zipper:data', collector: 'canvas',
                              payload: { items, assignments } });
  } catch (e) {
    // Never surface anything to the page. A failed read is Zipper's problem to
    // notice by the data going stale, not an alert over his coursework.
    console.debug('[zipper] canvas collector:', e);
  }
}

run();

// A tab left open all day should not freeze the reading at whenever it was
// opened. The interval lives here rather than in a background alarm because
// this context persists as long as the tab does, and the throttle in the
// background is what stops it becoming chatty.
setInterval(run, REFRESH_MS);
