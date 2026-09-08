/* One setting: where Zipper is.
 *
 * The address is a runtime permission rather than a manifest entry. A personal
 * tailnet host has no business in a manifest that lives in a public repo, and
 * asking for every-https-host up front would be a far broader grant than the
 * one host actually needed. So the manifest declares it optional and this asks
 * for exactly the origin he typed, at the moment he types it.
 *
 * (The wildcard pattern is spelled out in manifest.json, not here: a literal
 * star-slash inside a block comment ends the comment. It did, once.)
 */

const api = globalThis.browser ?? globalThis.chrome;

const $ = (id) => document.getElementById(id);

function say(text, cls) {
  const el = $('status');
  el.textContent = text;
  el.className = cls || '';
}

function originOf(value) {
  // A pattern, not a bare origin: host permissions are matched against URLs.
  return new URL(value).origin + '/*';
}

api.storage.sync.get('endpoint').then(({ endpoint }) => {
  if (endpoint) $('endpoint').value = endpoint;
});

$('save').addEventListener('click', async () => {
  const raw = $('endpoint').value.trim().replace(/\/+$/, '');
  if (!raw) return say('Enter an address first.', 'bad');

  let pattern;
  try {
    pattern = originOf(raw);
  } catch {
    return say('That is not a URL — include http:// or https://', 'bad');
  }

  // Must be called straight out of the click. Both browsers require a user
  // gesture for a permission prompt, and an await before this point spends it.
  let granted = false;
  try {
    granted = await api.permissions.request({ origins: [pattern] });
  } catch (e) {
    return say('Permission request failed: ' + e, 'bad');
  }
  if (!granted) return say('Not saved — without permission it cannot post there.', 'bad');

  await api.storage.sync.set({ endpoint: raw });
  say('Saved.', 'ok');
});

$('test').addEventListener('click', async () => {
  const { endpoint } = await api.storage.sync.get('endpoint');
  if (!endpoint) return say('Save an address first.', 'bad');
  say('Testing…');
  try {
    const res = await fetch(endpoint + '/api/state', { method: 'GET' });
    say(res.ok ? `Zipper answered ${res.status}. Good.`
               : `Reached it, but it answered ${res.status}.`,
        res.ok ? 'ok' : 'bad');
  } catch (e) {
    say('Could not reach it: ' + e.message
        + ' — is this machine on the tailnet?', 'bad');
  }
});
