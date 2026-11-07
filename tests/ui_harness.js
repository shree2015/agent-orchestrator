// Runs the shortener UI's inline script against a REAL server with a minimal fake DOM.
// Usage: node tests/ui_harness.js <ui.html path> <server base url>
// Prints "PASS name" / "FAIL name: detail" lines; exit code 1 if anything failed.
const fs = require('fs');
const vm = require('vm');

const [htmlPath, base] = process.argv.slice(2);
const html = fs.readFileSync(htmlPath, 'utf8');
const script = /<script>([\s\S]*?)<\/script>/.exec(html)[1];

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this._text = ''; this.className = '';
    this.hidden = false; this.disabled = false; this.value = ''; this.dataset = {}; this.handlers = {};
    this.attrs = {};
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); }
  focus() {}
  walk(fn) { fn(this); this.children.forEach((c) => c.walk(fn)); }
}

const ids = {};
for (const m of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)) {
  ids[m[2]] = new El(m[1]);
  if (/id="[^"]+"[^>]*\bhidden\b/.test(m[0])) ids[m[2]].hidden = true;
}
const created = [];
const storage = {};
const context = {
  console, Promise, JSON, Date, Math, Number, String, Object, Boolean, Array, encodeURIComponent, setTimeout,
  document: { getElementById: (id) => ids[id], createElement: (t) => { const e = new El(t); created.push(e); return e; } },
  localStorage: { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); } },
  navigator: {},
  fetch: (path, options) => fetch(base + path, { redirect: 'manual', ...options }),
};
vm.createContext(context);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(cond, label, ms = 3000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (cond()) return; await sleep(10); }
  throw new Error('timed out waiting for ' + label);
}
let failed = 0;
function check(name, ok, detail) {
  if (!ok) failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ': ' + detail}`);
}
async function submit(url, alias = '', ttl = '') {
  ids.url.value = url; ids.alias.value = alias; ids.ttl.value = ttl;
  ids.status.textContent = '';
  ids['shorten-form'].handlers.submit[0]({ preventDefault() {} });
  await until(() => !ids.submit.disabled && ids.status.textContent !== '' && ids.status.textContent !== 'Working...', 'submit to finish');
}
function items() { return ids.links.children; }
function findButton(item, action) { let found = null; item.walk((n) => { if (n.dataset.action === action) found = n; }); return found; }
function click(button) { ids.links.handlers.click[0]({ target: button }); }

(async () => {
  vm.runInContext(script, context);
  await until(() => /^service (online|offline)$/.test(ids.health.textContent), 'health check');
  check('health pill shows service online', ids.health.textContent === 'service online' && ids.health.className === 'pill online', ids.health.textContent);
  check('empty state is visible at first', ids.empty.hidden === false && items().length === 0);

  await submit('https://example.com/ui/one', 'ui-demo', '3600');
  check('success message is shown', ids.status.className === 'ok' && /created/i.test(ids.status.textContent), ids.status.textContent);
  check('result card is revealed with the short URL', ids.result.hidden === false && ids['result-link'].textContent === base + '/ui-demo', ids['result-link'].textContent);
  await until(() => items().length === 1, 'list render');
  check('link appears in the list with zero clicks', /Clicks: 0/.test(items()[0].textContent) && items()[0].textContent.includes('https://example.com/ui/one'), items()[0].textContent);
  check('expiry is displayed', /Expires/.test(items()[0].textContent), items()[0].textContent);
  check('link was saved to localStorage', JSON.parse(storage['shortener.links.v1']).length === 1);

  await fetch(base + '/ui-demo', { redirect: 'manual', headers: { Referer: 'https://ref.example/' } });
  click(findButton(items()[0], 'refresh'));
  await until(() => /Clicks: 1/.test(items()[0].textContent), 'refreshed click count');
  check('refresh shows the recorded click and referrer', /ref\.example/.test(items()[0].textContent), items()[0].textContent);

  await submit('https://example.com/ui/one', 'ui-demo', '3600');
  check('duplicate alias shows the server error', ids.status.className === 'error' && /already in use/.test(ids.status.textContent), ids.status.textContent);
  check('result card is hidden after an error', ids.result.hidden === true);

  await submit('http://localhost/admin');
  check('private URL is rejected with a readable message', ids.status.className === 'error' && /private or local/.test(ids.status.textContent), ids.status.textContent);

  await submit('https://example.com/?q=<b>bold</b>');
  check('markup in a URL is rendered as text, never parsed', items()[0].textContent.includes('<b>bold</b>') && !created.some((e) => e.tagName === 'B'));

  await submit('https://example.com/ui/same');
  await submit('https://example.com/ui/same');
  check('repeating a URL says the existing link is reused', /already had a short link/.test(ids.status.textContent), ids.status.textContent);

  ids.url.value = '   ';
  ids['shorten-form'].handlers.submit[0]({ preventDefault() {} });
  check('empty input is rejected locally', /Enter a URL/.test(ids.status.textContent), ids.status.textContent);

  const before = items().length;
  const demo = items().find((i) => i.textContent.includes('/ui-demo'));
  click(findButton(demo, 'delete'));
  await until(() => items().length === before - 1, 'delete to remove the item');
  const gone = await fetch(base + '/ui-demo', { redirect: 'manual' });
  check('delete removes the link on the server and in the list', gone.status === 404, String(gone.status));

  // last: the burst exhausts the write budget, so nothing that writes may follow it
  let limited = false;
  for (let i = 0; i < 12 && !limited; i += 1) {
    await submit('https://example.com/ui/burst' + i);
    limited = /Too many requests/.test(ids.status.textContent);
  }
  check('rate limiting is explained with a retry time', limited && /seconds/.test(ids.status.textContent), ids.status.textContent);

  process.exit(failed ? 1 : 0);
})().catch((err) => { console.log('FAIL harness crashed: ' + (err && err.stack || err)); process.exit(1); });
