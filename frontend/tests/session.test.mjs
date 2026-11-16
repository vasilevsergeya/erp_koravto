import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

let serial = 0;
async function moduleFrom(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${serial++}`);
}
const user = { id: 'manager-1', csrf_token: 'csrf-test', role: 'admin', must_change_password: false };
const response = (status, data = {}) => new Response(JSON.stringify(data), { status });
async function setup() {
  globalThis.window = new EventTarget();
  const mod = await moduleFrom('../src/api.ts');
  mod.setSession(user);
  return mod;
}
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };

test('parallel 401 responses share one refresh and each request retries once', async () => {
  const mod = await setup();
  const gate = deferred();
  let refreshes = 0;
  const counts = new Map();
  globalThis.fetch = async path => {
    if (path === '/api/auth/refresh') { refreshes++; await gate.promise; return response(200, user); }
    counts.set(path, (counts.get(path) || 0) + 1);
    return response(counts.get(path) === 1 ? 401 : 200, { ok: true });
  };
  const requests = Array.from({length: 12}, (_, i) => mod.api(`/api/item/${i}`));
  await new Promise(r => setImmediate(r));
  assert.equal(refreshes, 1);
  gate.resolve();
  await Promise.all(requests);
  assert.ok([...counts.values()].every(v => v === 2));
  assert.equal(refreshes, 1);
});

test('late 401 from same burst reuses completed refresh', async () => {
  const mod = await setup(); const gate = deferred(); let refreshes = 0; let calls = 0;
  globalThis.fetch = async path => {
    if (path === '/api/auth/refresh') { refreshes++; return response(200, user); }
    if (++calls === 1) { await gate.promise; return response(401); }
    return response(calls === 2 ? 401 : 200);
  };
  const late = mod.api('/api/late');
  await mod.api('/api/first'); gate.resolve(); await late;
  assert.equal(refreshes, 1);
});

test('expired refresh causes one global transition and stops subsequent requests', async () => {
  const mod = await setup(); let notices = 0; let calls = 0;
  window.addEventListener('crm:session-expired', () => notices++);
  globalThis.fetch = async () => { calls++; return response(401); };
  await Promise.allSettled(Array.from({length: 8}, () => mod.api('/api/item')));
  assert.equal(notices, 1); const previous = calls;
  await assert.rejects(mod.api('/api/item'), mod.SessionExpiredError);
  assert.equal(calls, previous);
});

test('temporary refresh outage does not log out and login 401 never refreshes', async () => {
  const mod = await setup(); let notices = 0; const paths = [];
  window.addEventListener('crm:session-expired', () => notices++);
  globalThis.fetch = async path => { paths.push(path); return response(path.includes('refresh') ? 503 : 401); };
  await assert.rejects(mod.api('/api/item'), e => e.status === 503);
  assert.equal(notices, 0);
  paths.length = 0;
  await assert.rejects(mod.api('/api/auth/login', 'POST', {}), e => e.status === 401);
  assert.deepEqual(paths, ['/api/auth/login']);
});

test('a second 401 stops after one retry; POST body and idempotency key stay unchanged', async () => {
  const mod = await setup(); const bodies = []; let notices = 0;
  window.addEventListener('crm:session-expired', () => notices++);
  globalThis.fetch = async (path, options) => {
    if (path.includes('/auth/refresh')) return response(200, user);
    bodies.push(options.body); return response(401);
  };
  await assert.rejects(mod.api('/api/messages', 'POST', {client_message_id:'stable-test-id',text:'test'}));
  assert.equal(bodies.length, 2); assert.equal(bodies[0], bodies[1]); assert.equal(notices, 1);
});

test('stale CSRF in another tab is recovered only for the same user', async () => {
  const mod = await setup(); let refreshes = 0;
  globalThis.fetch = async (path, options) => {
    if (path.endsWith('/me')) return response(200, {...user, csrf_token:'new-test-csrf'});
    if (++refreshes === 1) return response(403);
    assert.equal(options.headers['X-CSRF-Token'], 'new-test-csrf');
    return response(200, {...user, csrf_token:'new-test-csrf'});
  };
  await mod.refreshSession(); assert.equal(refreshes, 2);
});

test('switching accounts cannot replay a draft request as another user', async () => {
  const mod = await setup(); let calls = 0;
  globalThis.fetch = async path => {
    calls++;
    return response(path.endsWith('/refresh') ? 200 : 401, {...user, id:'manager-2'});
  };
  await assert.rejects(mod.api('/api/messages', 'POST', {text:'private draft'}), mod.SessionExpiredError);
  assert.equal(calls, 2);
});

test('old refresh response cannot expire a new login', async () => {
  const mod = await setup(); const gate = deferred(); let notices = 0;
  window.addEventListener('crm:session-expired', () => notices++);
  globalThis.fetch = async () => { await gate.promise; return response(401); };
  const old = mod.refreshSession(); mod.setSession({...user, id:'manager-2'}); gate.resolve();
  await assert.rejects(old, mod.SessionExpiredError); assert.equal(notices, 0);
});

test('old protected request is not retried after a new login', async () => {
  const mod = await setup(); const gate = deferred(); let calls = 0;
  globalThis.fetch = async () => { calls++; await gate.promise; return response(401); };
  const old = mod.api('/api/messages', 'POST', {text:'private draft'});
  mod.setSession({...user, id:'manager-2'}); gate.resolve();
  await assert.rejects(old, mod.SessionExpiredError); assert.equal(calls, 1);
});

test('draft survives login and reload, keeps attachment in memory, and is isolated by user', async () => {
  const storage = new Map();
  globalThis.sessionStorage = {getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k)};
  const mod = await moduleFrom('../src/inbox-drafts.ts');
  const draft = {text:'unsent text',file:new File(['test'], 'test.txt'),attachment:null,note:false,quoted:null,sendId:'stable-id'};
  mod.saveDraft('one', 'dialog', draft);
  assert.equal(mod.loadDraft('one','dialog').file, draft.file);
  assert.equal(mod.loadDraft('two','dialog'), undefined);
  const reloaded = await moduleFrom('../src/inbox-drafts.ts');
  assert.equal(reloaded.loadDraft('one','dialog').text, draft.text);
  assert.equal(reloaded.loadDraft('one','dialog').sendId, draft.sendId);
  assert.equal(reloaded.loadDraft('one','dialog').file, null);
  mod.removeDraft('one','dialog'); assert.equal(mod.loadDraft('one','dialog'), undefined);
});
