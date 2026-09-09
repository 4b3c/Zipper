/* The reporter. Everything a collector gathers leaves through here.
 *
 * This file is deliberately the only part that knows Zipper exists. A collector
 * knows one website and returns JSON; adding a site is writing one content
 * script, never touching this. That split is the whole design -- the browser is
 * a place to *read* things a logged-out server cannot, and nothing more. No
 * parsing, no judgement, no vault knowledge. The backend concludes.
 *
 * Written for Chrome's service worker, which is the stricter of the two: it is
 * killed aggressively and keeps no globals between wake-ups. So there is no
 * module-level mutable state here -- every fact that has to outlive a message
 * lives in chrome.storage. Firefox runs the same file as an event page, where
 * that discipline is merely unnecessary rather than wrong. Code written the
 * other way round breaks on Chrome, which is why the constraint is honoured
 * even though half the targets do not need it.
 */

const api = globalThis.browser ?? globalThis.chrome;

// A collector that fires on every page load would POST a dozen times while he
// clicks around Canvas. The interesting quantity changes on the order of hours.
const MIN_INTERVAL_MS = 15 * 60 * 1000;

async function config() {
  const { endpoint } = await api.storage.sync.get('endpoint');
  return (endpoint || '').replace(/\/+$/, '');
}

/* Has this collector reported recently enough to skip?
 *
 * Kept per collector rather than globally: Canvas being fresh says nothing
 * about Onshape, and one shared timestamp would let a chatty site starve a
 * quiet one.
 */
async function throttled(name) {
  const key = 'lastSent:' + name;
  const store = await api.storage.local.get(key);
  const last = store[key] || 0;
  return Date.now() - last < MIN_INTERVAL_MS;
}

async function markSent(name) {
  await api.storage.local.set({ ['lastSent:' + name]: Date.now() });
}

/* POST to Zipper.
 *
 * This is the one cross-origin request in the extension, and it has to happen
 * here. A content script's fetch carries the *page's* origin, so posting to the
 * tailnet from inside canvas.asu.edu would be a genuine cross-origin request
 * and CORS would refuse it. From the background, host permissions apply
 * instead and the browser does not interpose. It is also why the endpoint is
 * an optional permission granted on the options page: the URL is not known at
 * build time and nobody should ship a personal tailnet address in a manifest.
 */
async function send(name, payload) {
  const endpoint = await config();
  if (!endpoint) {
    console.warn('[zipper] no endpoint configured; open the extension options');
    return { ok: false, error: 'no endpoint' };
  }
  // A collector hands over the body it wants posted; the reporter only stamps
  // where it came from. An array is wrapped for the older `{source, items}`
  // shape so a collector that has nothing but a list stays a one-liner.
  const body = Array.isArray(payload) ? { items: payload } : { ...payload };
  body.source = 'extension';

  const url = endpoint + '/api/' + name;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    return { ok: false, error: 'zipper returned ' + res.status };
  }
  return await res.json().catch(() => ({ ok: true }));
}

api.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (!msg || msg.type !== 'zipper:data') return false;

  // Not awaited inline: returning true keeps the message channel open, which is
  // the only way an async listener may reply in either browser.
  (async () => {
    try {
      if (msg.force !== true && (await throttled(msg.collector))) {
        respond({ ok: true, skipped: 'throttled' });
        return;
      }
      const out = await send(msg.collector, msg.payload);
      if (out.ok !== false) await markSent(msg.collector);
      respond(out);
    } catch (e) {
      respond({ ok: false, error: String(e) });
    }
  })();
  return true;
});
