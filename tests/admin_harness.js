// Runs the admin page's inline script against a REAL admin server with a minimal fake DOM, and clicks through it.
// Usage: node tests/admin_harness.js <admin.html path> <base url> <api key used in the test>
// Prints "PASS name" / "FAIL name: detail"; exit code 1 if anything failed.
const fs = require('fs');
const vm = require('vm');

const [htmlPath, base, SECRET] = process.argv.slice(2);
const html = fs.readFileSync(htmlPath, 'utf8');
const script = /<script>([\s\S]*?)<\/script>/.exec(html)[1];

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this._text = ''; this.className = ''; this.hidden = false; this.disabled = false;
    this.value = ''; this.checked = false; this.type = ''; this.name = ''; this.href = ''; this.dataset = {}; this.handlers = {};
    this.scrollTop = 0; this.clientHeight = 0; this.scrollHeight = 0;
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  appendChild(c) {
    this.children.push(c);
    if (this.tagName === 'SELECT' && this.value === '' && c.tagName === 'OPTION') this.value = c.value;   // browsers select the first option
    return c;
  }
  addEventListener(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); }
  focus() {}
  walk(fn) { fn(this); this.children.forEach((c) => c.walk(fn)); }
}

const ids = {};
for (const m of html.matchAll(/<(\w+)([^>]*?)\bid="([^"]+)"([^>]*)>/g)) {
  const e = new El(m[1]); const attrs = m[2] + ' ' + m[4];
  const get = (n) => { const r = new RegExp('\\b' + n + '="([^"]*)"').exec(attrs); return r ? r[1] : null; };
  e.type = get('type') || ''; e.name = get('name') || ''; e.value = get('value') || '';
  e.hidden = /\bhidden\b/.test(attrs.replace(/id="[^"]*"/, '')); e.checked = /\bchecked\b/.test(attrs);
  ids[m[3]] = e;
}
const context = {
  console, Promise, JSON, Date, Math, Number, String, Object, Boolean, Array, encodeURIComponent, setTimeout, clearTimeout,
  document: { getElementById: (id) => ids[id], createElement: (t) => new El(t) },
  fetch: (path, options) => fetch(base + path, options),
};
vm.createContext(context);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(cond, label, ms = 20000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (cond()) return; await sleep(15); }
  throw new Error('timed out waiting for ' + label + ' | banner: ' + ids['run-banner'].textContent + ' | status: ' + ids['form-status'].textContent + ' | service: ' + ids['service-status'].textContent + ' / ' + ids['service-text'].textContent);
}
let failed = 0;
function check(name, ok, detail) { if (!ok) failed += 1; console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ': ' + detail}`); }
const find = (root, pred) => { let hit = null; root.walk((n) => { if (!hit && pred(n)) hit = n; }); return hit; };
const findAll = (root, pred) => { const out = []; root.walk((n) => { if (pred(n)) out.push(n); }); return out; };
const button = (root, text) => find(root, (n) => n.tagName === 'BUTTON' && n.textContent.startsWith(text));
const fire = (e, type, ev = {}) => (e.handlers[type] || []).forEach((h) => h({ preventDefault() {}, target: e, ...ev }));
function click(e) {
  if (e.type === 'radio') { Object.values(ids).forEach((o) => { if (o.type === 'radio' && o.name === e.name) o.checked = false; }); e.checked = true; fire(e, 'change'); }
  else if (e.type === 'checkbox') { e.checked = !e.checked; fire(e, 'change'); }
  else fire(e, 'click');
}
function type(e, value) { e.value = value; fire(e, 'input'); }
function allText() { let t = ''; Object.values(ids).forEach((e) => { t += e.textContent + (e.value || ''); }); return t; }
const banner = () => ids['run-banner'].textContent;
const runId = () => { const m = /(run-\d{8}-\d{6}-[0-9a-f]+)/.exec(banner()); return m ? m[1] : ''; };
const pending = () => findAll(ids.pending, (n) => n.className === 'pending-card');
const nodeBadges = () => findAll(ids.nodes, (n) => /^badge /.test(n.className)).map((n) => n.textContent);

(async () => {
  vm.runInContext(script, context);
  await until(() => ids.scenario.children.length === 3, 'configuration to load');
  check('scenarios and the default requirement load', ids.requirement.value.length > 10 && /\d+ \/ 20000 characters/.test(ids['req-count'].textContent));
  check('key chip says no key', ids['key-chip'].textContent === 'API key: not set' && ids['key-chip'].className === 'pill warn', ids['key-chip'].textContent);
  check('real options are hidden and Generate is enabled in mock mode', ids['real-box'].hidden === true && ids.start.disabled === false);

  // ---- mock <-> real toggle and the key
  click(ids['mode-real']);
  check('real mode shows the model options and blocks Generate without a key', ids['real-box'].hidden === false && ids.start.disabled === true
        && /needs an API key/.test(ids['start-hint'].textContent), ids['start-hint'].textContent);
  check('the call budget appears in the confirmation text', /at most 40 model calls/.test(ids['confirm-label'].textContent), ids['confirm-label'].textContent);
  type(ids['api-key'], 'too short');
  click(ids['save-key']);
  await until(() => /does not look like an API key/.test(ids['form-status'].textContent), 'a refusal of a nonsense key');
  type(ids['api-key'], SECRET);
  click(ids['save-key']);
  await until(() => ids['key-chip'].textContent === 'API key: pasted (memory only)', 'the key to be accepted');
  check('the pasted key is cleared from the box and never shown anywhere', ids['api-key'].value === '' && !allText().includes(SECRET));
  check('with a key, Generate still needs the confirmation', ids.start.disabled === true && /Tick the confirmation/.test(ids['start-hint'].textContent));
  click(ids['confirm-real']);
  check('confirming enables Generate', ids.start.disabled === false);
  click(ids['mode-mock']);
  check('switching back to mock hides the real options', ids['real-box'].hidden === true && ids.start.disabled === false);
  click(ids['mode-real']);
  click(ids['clear-key']);
  await until(() => ids['key-chip'].textContent === 'API key: not set', 'the key to be cleared');
  click(ids['mode-mock']);

  // ---- a complete mock run, auto-approved
  ids.scenario.value = 'slugify'; fire(ids.scenario, 'change');
  check('choosing a scenario loads its requirement', /slugify/.test(ids.requirement.value));
  type(ids.requirement, 'Write slugify exactly my way.');
  await sleep(10);
  check('a custom requirement is accepted as typed', ids.requirement.value === 'Write slugify exactly my way.');
  click(ids['auto-approve']);
  fire(ids['run-form'], 'submit');
  await until(() => /^Completed/.test(banner()), 'the mock run to complete');
  const badges = nodeBadges();
  check('every step shows as done', badges.length === 7 && badges.every((b) => b === 'DONE'), badges.join(','));
  check('the notice says mock agents ignore the wording', /ignore the requirement text/.test(ids.notes.textContent), ids.notes.textContent);
  check('the activity feed tells the story', /Run started/.test(ids.timeline.textContent) && /Run finished: COMPLETED/.test(ids.timeline.textContent));
  await until(() => !ids['results-section'].hidden && ids.artifacts.children.length > 3, 'the results to appear');
  const impl = button(ids.artifacts, 'implementation.py');
  click(impl);
  await until(() => !ids['artifact-view'].hidden && /def slugify/.test(ids['artifact-view'].textContent), 'the artifact to open');
  check('an artifact can be read on the page', true);
  check('the download link points at this run', /^\/api\/runs\/run-[^/]+\/download$/.test(ids.download.href), ids.download.href);
  click(ids['audit-check']);
  await until(() => /intact/.test(ids['audit-result'].textContent), 'the audit check');
  click(ids['show-report']);
  await until(() => /# Run report/.test(ids['artifact-view'].textContent), 'the report');
  check('the revise panel is not offered for a scenario without questions', ids.revise.hidden === true);
  check('no service panel is offered for a run that produced no service', ids['service-box'].hidden === true);
  await until(() => ids.history.children.length >= 1, 'history');
  check('the run appears in history', /slugify/.test(ids.history.textContent) && /completed/.test(ids.history.textContent));
  check('Stop is hidden when nothing is running', ids.stop.hidden === true);

  // ---- approvals made on the page
  click(ids['auto-approve']);                                  // now: ask me
  fire(ids['run-form'], 'submit');
  await until(() => pending().length === 1, 'the design approval');
  check('the banner says the run is waiting for you', /^Waiting for you/.test(banner()), banner());
  let card = pending()[0];
  check('the approval shows what is being approved', /Approve 'design'/.test(card.textContent) && find(card, (n) => n.tagName === 'PRE').textContent.length > 20);
  check('Generate is blocked while a run is active', ids.start.disabled === true && /in progress/.test(ids['start-hint'].textContent));
  click(button(card, 'Approve'));
  await until(() => pending().length === 1 && /Approve 'release'/.test(pending()[0].textContent), 'the release approval');
  click(button(pending()[0], 'Approve'));
  await until(() => /^Completed/.test(banner()), 'the run to complete after both approvals');
  check('approving twice completed the run', ids.start.disabled === false);

  // ---- stop
  fire(ids['run-form'], 'submit');
  await until(() => pending().length === 1, 'an approval to wait on');
  check('Stop is offered while running', ids.stop.hidden === false);
  click(ids.stop);
  await until(() => /^Stopped by you/.test(banner()), 'the run to stop');
  check('stopping releases the waiting decision and says why', pending().length === 0 && /stopped by the operator/.test(banner()), banner());

  // ---- size limits (change control)
  ids.scenario.value = 'shortener'; fire(ids.scenario, 'change');
  check('the size limits are offered with their defaults for the shortener',
        ids['limits-box'].hidden === false && ids['limit-code'].value === '900' && ids['limit-ui'].value === '700', ids['limit-code'].value + '/' + ids['limit-ui'].value);
  type(ids['limit-code'], '1200');
  check('raising a limit says it is recorded in the audit log', /Raised above the default for: code/.test(ids['limit-hint'].textContent) && /audit log/.test(ids['limit-hint'].textContent), ids['limit-hint'].textContent);
  type(ids['limit-code'], '80');
  if (!ids['auto-approve'].checked) click(ids['auto-approve']);
  let beforeLimit = runId();
  fire(ids['run-form'], 'submit');
  await until(() => runId() !== beforeLimit && /^Halted/.test(banner()), 'the run to halt on the size limit');
  check('a size limit stops the run and the banner says which limit', /limit is 80/.test(banner()), banner());
  check('the page tells the operator how to continue', /Size limits/.test(ids.notes.textContent) && /recorded in the audit log/.test(ids.notes.textContent), ids.notes.textContent);
  ids.scenario.value = 'slugify'; fire(ids.scenario, 'change');
  check('the toy scenario offers only a code limit', ids['limits-box'].hidden === false && ids['limit-ui-wrap'].hidden === true && ids['limit-code'].value === '200');
  ids.scenario.value = 'ambiguous'; fire(ids.scenario, 'change');
  check('the ambiguous scenario has fixed limits, so none are offered', ids['limits-box'].hidden === true);

  // ---- the ambiguous scenario: questions, then changing an answer
  ids.scenario.value = 'ambiguous'; fire(ids.scenario, 'change');
  if (!ids['auto-approve'].checked) click(ids['auto-approve']);   // auto-approve on: only the high-risk question needs us
  fire(ids['run-form'], 'submit');
  await until(() => pending().length === 1 && /Question \(high risk\)/.test(pending()[0].textContent), 'the high-risk question');
  card = pending()[0];
  const radios = findAll(card, (n) => n.type === 'radio');
  check('the question lists its options and pre-selects nothing for a high-risk question', radios.length === 3 && radios.every((r) => !r.checked));
  check('a high-risk question cannot be assumed', button(card, 'Use the proposed default') === null && /only you can answer/.test(card.textContent));
  click(button(card, 'Submit my answer'));
  await until(() => /Choose an answer first/.test(ids['form-status'].textContent), 'a prompt to choose');
  radios.find((r) => r.value === '90').checked = true;
  click(button(card, 'Submit my answer'));
  await until(() => /^Completed/.test(banner()) && ids.revise.hidden === false, 'completion and the revise panel');
  check('the revise panel lists the decisions and their options', ids['revise-decision'].children.length === 3 && ids['revise-value'].children.length === 3);
  ids['revise-decision'].value = 'default_ttl_days'; fire(ids['revise-decision'], 'change');
  ids['revise-decision'].onchange && ids['revise-decision'].onchange();
  ids['revise-value'].value = '180';
  click(ids['revise-apply']);
  await until(() => /Accepted\. Redoing:/.test(ids['revise-result'].textContent), 'the revision to be accepted');
  await until(() => /^Completed/.test(banner()) && /Run finished: COMPLETED/.test(ids.timeline.textContent) && /revised by human/.test(ids.timeline.textContent), 'the re-run to finish');
  click(button(ids.artifacts, 'implementation.py'));
  await until(() => /DEFAULT_TTL_S = 180 \* 24 \* 3600/.test(ids['artifact-view'].textContent), 'the new answer in the code');
  check('changing the answer rebuilt the code with the new value', true);
  ids['revise-value'].value = '180';
  click(ids['revise-apply']);
  await until(() => /nothing to redo/.test(ids['revise-result'].textContent), 'the no-op message');
  check('the same answer again does nothing', true);

  // ---- running the generated service: a mock run needs no extra confirmation
  ids.scenario.value = 'shortener'; fire(ids.scenario, 'change');
  if (!ids['auto-approve'].checked) click(ids['auto-approve']);
  let previous = runId();
  fire(ids['run-form'], 'submit');
  await until(() => runId() !== previous && /^Completed/.test(banner()) && ids['service-box'].hidden === false, 'the NEW shortener run and its service panel');
  check('a run that produced a service offers to start it', ids['start-service'].hidden === false && /^Not running/.test(ids['service-text'].textContent), ids['service-text'].textContent);
  check('mock code needs no extra confirmation', ids['service-confirm-row'].hidden === true && ids['start-service'].disabled === false);
  click(ids['start-service']);
  await until(() => ids['service-link'].hidden === false, 'the service to start');
  const url = ids['service-link'].href;
  check('the link points at this computer only', /^http:\/\/127\.0\.0\.1:\d+\/$/.test(url), url);
  const health = await fetch(url + 'healthz');
  const homepage = await (await fetch(url)).text();
  check('the started service answers and serves its own web page', health.status === 200 && /<title>Shortener/.test(homepage));
  check('Stop is offered while it runs and Start is not', ids['stop-service'].hidden === false && ids['start-service'].hidden === true);
  click(ids['stop-service']);
  await until(() => ids['service-link'].hidden === true && /^Not running/.test(ids['service-text'].textContent), 'the service to stop');
  let closed = false;
  try { await fetch(url + 'healthz'); } catch (e) { closed = true; }
  check('stopping really closes the port', closed);

  // ---- code from a REAL model needs an explicit confirmation before it can run
  type(ids['api-key'], SECRET);
  click(ids['save-key']);
  await until(() => ids['key-chip'].textContent === 'API key: pasted (memory only)', 'the key to be accepted');
  click(ids['mode-real']);
  if (!ids['confirm-real'].checked) click(ids['confirm-real']);          // consent must be given explicitly
  previous = runId();
  fire(ids['run-form'], 'submit');
  await until(() => runId() !== previous && /^Completed/.test(banner()) && /real model/.test(banner()) && ids['service-box'].hidden === false, 'the NEW real-model run and its service panel');
  check('code from a real model asks for confirmation and Start is blocked until it is ticked',
        ids['service-confirm-row'].hidden === false && ids['start-service'].disabled === true);
  click(ids['confirm-service']);
  check('ticking the confirmation enables Start', ids['start-service'].disabled === false);
  click(ids['start-service']);
  await until(() => ids['service-link'].hidden === false, 'the real-model service to start');
  const realUrl = ids['service-link'].href;
  check('it also listens on this computer only', /^http:\/\/127\.0\.0\.1:\d+\/$/.test(realUrl), realUrl);
  check('and it answers', (await fetch(realUrl + 'healthz')).status === 200);
  click(ids['stop-service']);
  await until(() => ids['service-link'].hidden === true, 'the real-model service to stop');
  click(ids['mode-mock']);
  click(ids['clear-key']);
  await until(() => ids['key-chip'].textContent === 'API key: not set', 'the key to be cleared');

  check('the API key never appeared anywhere on the page', !allText().includes(SECRET));
  process.exit(failed ? 1 : 0);
})().catch((err) => { console.log('FAIL harness crashed: ' + (err && err.stack || err)); process.exit(1); });
