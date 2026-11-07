import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { handle } from './worker.mjs';
const now = Math.floor(Date.now() / 600000) * 600;
const iso = new Date(now * 1000).toISOString();
const base = `https://edge.example/v1/radar?z=7&x=105&y=48&scale=2&frameTime=${iso}`;
const env = { VPS_ORIGIN: 'https://compute.example', VPS_TOKEN: 'test-only-token' };
function tile() {
  const bytes = new Uint8Array(65604);
  bytes.set([171,75,84,88,32,49,49,187,13,10,26,10]);
  const view = new DataView(bytes.buffer);
  [0x04030201,0,1,0,0x93b7,0x1908,512,512,0,0,1,1,0,65536].forEach((n, i) => view.setUint32(12 + i * 4, n, true));
  return bytes;
}
const headers = () => ({ 'Content-Type': 'image/ktx', 'Cache-Control': 'no-store', 'X-WK3-Nowcast': 'EXPERIMENTAL-RADAR-ECHO', 'X-WK3-Origin': `${now}`, 'X-WK3-Valid-Time': `${now}`, 'X-WK3-Attribution': 'Weather data by RainViewer; extrapolation by this project' });
test('actual Cloudflare entry ignores ctx as a fetch function', async () => {
  const response = await worker.fetch(new Request('https://edge.example/health'), env, {});
  assert.equal(response.status, 200); assert.equal((await response.json()).compute, 'vps');
});
test('valid binary roundtrip builds fresh upstream request and strips credentials', async () => {
  let calls = 0;
  const response = await handle(new Request(base, { headers: { Authorization: 'Apple secret', Cookie: 'device cookie', 'X-Apple-Test': 'private' } }), env, async (url, options) => {
    calls++; assert.equal(new URL(url).origin, env.VPS_ORIGIN);
    assert.equal(new URL(url).searchParams.size, 5);
    assert.deepEqual([...options.headers.keys()].sort(), ['accept', 'authorization']);
    assert.equal(options.headers.get('authorization'), 'Bearer test-only-token');
    assert.equal(options.redirect, 'manual');
    return new Response(tile(), { headers: headers() });
  });
  assert.equal(calls, 1); assert.equal(response.status, 200);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), tile());
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('x-wk3-attribution'), headers()['X-WK3-Attribution']);
});
test('each deployment uses only its own VPS and empty config never falls back', async () => {
  const unexpected = async () => assert.fail('unconfigured deployment must not fetch');
  const health = await handle(new Request('https://edge.example/health'), {}, unexpected);
  assert.equal(health.status, 503); assert.equal((await health.json()).configured, false);
  assert.equal((await handle(new Request(base), {}, unexpected)).status, 503);
  for (const origin of ['https://alice-vps.example', 'https://bob-vps.example']) {
    const result = await handle(new Request(base), { VPS_ORIGIN: origin, VPS_TOKEN: 'own-token' }, async (target, options) => {
      assert.equal(new URL(target).origin, origin);
      assert.equal(options.headers.get('Authorization'), 'Bearer own-token');
      return new Response(tile(), { headers: headers() });
    });
    assert.equal(result.status, 200);
  }
});
test('cold and unavailable tiles remain non-weather responses', async () => {
  for (const status of [202, 204, 400, 404, 409, 422, 429, 503]) {
    const response = await handle(new Request(base), env, async () => new Response(null, { status }));
    assert.equal(response.status, status); assert.equal(response.headers.get('x-wk3-nowcast'), null);
  }
});
test('redirect responses are rejected without following or forwarding a secret', async () => {
  let calls = 0;
  const response = await handle(new Request(base), env, async (url, options) => {
    calls++; assert.equal(options.redirect, 'manual');
    return new Response(null, { status: 302, headers: { Location: 'https://other.example' } });
  });
  assert.equal(calls, 1); assert.equal(response.status, 502);
});
test('invalid query and arbitrary routes never reach VPS', async () => {
  const unexpected = async () => { assert.fail('must not fetch'); };
  for (const url of [base + '&url=https://evil.example', base + '&x=1', base.replace('z=7', 'z=8'), base.replace('x=105', 'x=128'), base.replace(iso, '2026-02-30T00:00:00Z'), base.replace('/v1/radar', '/status')]) {
    assert.ok([400, 404].includes((await handle(new Request(url), env, unexpected)).status));
  }
  assert.equal((await handle(new Request(base, { method: 'POST' }), env, unexpected)).status, 405);
});
test('backend must be configured as HTTPS origin', async () => {
  for (const origin of ['', 'http://compute.example', 'https://apple.com', 'https://compute.example/path', 'https://u:p@compute.example']) {
    assert.equal((await handle(new Request(base), { VPS_ORIGIN: origin }, async () => assert.fail())).status, 503);
  }
});
test('bad source headers, stale origins and malformed binary are rejected', async () => {
  const cases = [
    { h: { ...headers(), 'X-WK3-Nowcast': 'other' }, b: tile() },
    { h: { ...headers(), 'X-WK3-Origin': `${now - 1800}` }, b: tile() },
    { h: { ...headers(), 'X-WK3-Valid-Time': `${now + 600}` }, b: tile() },
    { h: headers(), b: new Uint8Array(65604) },
    { h: headers(), b: new Uint8Array(65605) },
    { h: headers(), b: tile().subarray(0, 60) }
  ];
  for (const { h, b } of cases) assert.equal((await handle(new Request(base), env, async () => new Response(b, { headers: h }))).status, 502);
  assert.equal((await handle(new Request(base), env, async () => { throw Error('network'); })).status, 502);
});
