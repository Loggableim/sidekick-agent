"""Execute frontend behavior with mocked transport; never contact live services."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend execution")
    result = subprocess.run(
        [node, "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_auth_boundaries_and_subpath_stream_urls():
    run_node(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
(async () => {
  for (const mount of ['/', '/sidekick/']) {
    const origin = 'https://dashboard.test';
    const calls = [];
    const context = {
      URL, URLSearchParams, Headers, Request,
      document: {baseURI: origin + mount},
      location: {origin, href: origin + mount + 'session/123'},
      localStorage: {getItem: () => 'space-a'},
      __SIDEKICK_SESSION_TOKEN__: 'test-token',
      fetch: async (input, init) => {
        calls.push({input, headers: new Headers(init?.headers || input.headers)});
      },
    };
    context.window = context;
    vm.createContext(context);
    vm.runInContext(fs.readFileSync('web/static/api-auth.js', 'utf8'), context);
    // Later scripts must not override the centrally enforced auth boundary.
    for (const file of ['workspace.js', 'messages.js']) {
      const code = fs.readFileSync('web/static/' + file, 'utf8');
      assert.doesNotMatch(code, /function\s+_(?:eventSourceUrl|shouldAttachWorkspaceHeader|headersWithWorkspace)\s*\(/);
    }
    await context.fetch(origin + mount + 'api/sessions');
    assert.equal(calls.at(-1).headers.get('X-Sidekick-Session-Token'), 'test-token');
    assert.equal(calls.at(-1).headers.get('X-Sidekick-Workspace'), 'space-a');
    const request = new Request(origin + mount + 'api/chat', {
      method: 'POST', body: '{}', headers: {'X-Sidekick-Workspace': 'owner', 'X-Custom': 'kept'},
    });
    await context.fetch(request);
    assert.equal(calls.at(-1).headers.get('X-Sidekick-Workspace'), 'owner');
    assert.equal(calls.at(-1).headers.get('X-Custom'), 'kept');
    assert.equal(await calls.at(-1).input.text(), '{}');
    for (const target of ['https://foreign.test/api/test', '//foreign.test/api/test', origin + mount + 'static/test']) {
      await context.fetch(target);
      assert.equal(calls.at(-1).headers.get('X-Sidekick-Session-Token'), null);
      assert.equal(calls.at(-1).headers.get('X-Sidekick-Workspace'), null);
      const stream = new URL(context._eventSourceUrl(target));
      assert.equal(stream.searchParams.get('token'), null);
      assert.equal(stream.searchParams.get('workspace'), null);
    }
    if (mount !== '/') {
      await context.fetch(origin + '/api/unrelated-app');
      assert.equal(calls.at(-1).headers.get('X-Sidekick-Session-Token'), null);
    }
    for (const path of ['api/chat/stream', '/api/chat/stream', origin + mount + 'api/chat/stream']) {
      const stream = new URL(context._eventSourceUrl(path + '?workspace=owner'));
      assert.equal(stream.pathname, mount + 'api/chat/stream');
      assert.equal(stream.searchParams.get('token'), 'test-token');
      assert.equal(stream.searchParams.get('workspace'), 'owner');
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_token_counter_handles_degraded_http_and_network_failures_and_recovers():
    run_node(r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/static/enhancements.js', 'utf8');
const start = source.indexOf('  function addTokenCounter()');
const end = source.indexOf('  function setupKeyboardShortcuts()', start);
let update, counter, response;
const context = {
  URL, document: {baseURI: 'https://dashboard.test/sidekick/', querySelector: selector => {
    if (selector === '.sidebar-footer') return null;
    assert.equal(selector, '.app-titlebar-spacer');
    return {appendChild: x => {counter = x;}};
  }},
  EL: () => ({textContent: '', innerHTML: ''}),
  setInterval: fn => {update = fn;},
  fetch: async url => {
    assert.equal(url, 'https://dashboard.test/sidekick/api/analytics/usage?days=1');
    if (response instanceof Error) throw response;
    return response;
  },
};
const healthy = {ok: true, json: async () => ({totals: {total_output: 2500, total_input: 1000}})};
(async () => {
  response = healthy;
  vm.runInNewContext(source.slice(start, end) + '\naddTokenCounter();', context);
  await new Promise(resolve => setImmediate(resolve));
  assert.match(counter.innerHTML, /2.5K/);
  for (const failure of [
    {ok: true, json: async () => ({degraded: true, totals: {total_output: 0}})},
    {ok: false}, new Error('offline'), {ok: true, json: async () => ({})},
  ]) {
    response = failure;
    await update();
    assert.equal(counter.textContent, 'Tokenstatistik nicht verfügbar');
  }
  response = healthy;
  counter.innerHTML = '';
  await update();
  assert.match(counter.innerHTML, /2.5K/);
})().catch(error => {console.error(error); process.exitCode = 1;});
""")
