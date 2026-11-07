// A stateless edge gateway. Forecast computation runs on the configured VPS.
const route = '/v1/radar';
const marker = 'EXPERIMENTAL-RADAR-ECHO';
const json = (status, body) => Response.json(body, { status, headers: { 'Cache-Control': 'no-store' } });
function backend(env) {
  const u = new URL(env.VPS_ORIGIN);
  if (u.protocol !== 'https:' || u.username || u.password || u.search || u.hash || u.pathname !== '/' || /(^|\.)apple\.com$/i.test(u.hostname)) throw Error('backend');
  return u;
}
export function parseTile(url, now) {
  const q = url.searchParams, fields = ['z', 'x', 'y', 'scale', 'frameTime'];
  if ([...q.keys()].length !== fields.length || fields.some(k => q.getAll(k).length !== 1) || [...q.keys()].some(k => !fields.includes(k))) return null;
  if (!/^[0-7]$/.test(q.get('z')) || q.get('scale') !== '2') return null;
  const z = Number(q.get('z'));
  for (const k of ['x', 'y']) if (!/^(0|[1-9][0-9]*)$/.test(q.get(k)) || Number(q.get(k)) >= 2 ** z) return null;
  const text = q.get('frameTime');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::00(?:\.000)?)?Z$/.test(text)) return null;
  const time = Date.parse(text);
  if (!Number.isFinite(time) || new Date(time).toISOString().replace('.000Z', 'Z') !== text.replace('.000Z', 'Z').replace(/T(\d{2}:\d{2})Z$/, 'T$1:00Z') || time % 300000 || time < now - 1200000 || time > now + 3600000) return null;
  return time / 1000;
}
export function validHeaders(headers, time, now) {
  if (headers.get('X-WK3-Nowcast') !== marker || !/^image\/ktx(?:\s*;.*)?$/i.test(headers.get('Content-Type') || '') || !/(?:^|,)\s*no-store\s*(?:,|$)/i.test(headers.get('Cache-Control') || '')) return false;
  const origin = headers.get('X-WK3-Origin'), valid = headers.get('X-WK3-Valid-Time'), source = headers.get('X-WK3-Source-Valid-Time');
  if (![origin, valid].every(v => /^[0-9]{1,12}$/.test(v || '')) || Number(valid) !== time || Number(origin) > now || now - Number(origin) > 1200 || time < Number(origin)) return false;
  const endText = headers.get('X-WK3-Forecast-End');
  const end = endText === null ? Number(origin) + 3600 : Number(endText);
  if ((endText !== null && !/^[0-9]{1,12}$/.test(endText)) || end < Number(origin) || end > Number(origin) + 4800 || time > end || time > now + 3600) return false;
  return source === null || (/^[0-9]{1,12}$/.test(source) && Number(source) >= Number(origin) && Number(source) <= end && Math.abs(Number(source) - time) <= 300);
}
export function validKTX(bytes) {
  if (bytes.length !== 65604) return false;
  if ([171,75,84,88,32,49,49,187,13,10,26,10].some((v, i) => bytes[i] !== v)) return false;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return [0x04030201,0,1,0,0x93b7,0x1908,512,512,0,0,1,1,0,65536].every((v, i) => view.getUint32(12 + 4 * i, true) === v);
}
export async function handle(request, env, fetchBackend = fetch) {
  const url = new URL(request.url);
  if (request.method !== 'GET') return new Response(null, { status: 405, headers: { Allow: 'GET', 'Cache-Control': 'no-store' } });
  if (url.pathname === '/health') {
    let configured = false;
    try { backend(env); configured = true; } catch {}
    return json(configured ? 200 : 503, { service: 'wk3-radar', configured, compute: 'vps', mode: 'experimental-echo', endpoint: route });
  }
  if (url.pathname !== route) return json(404, { error: 'not-found' });
  const time = parseTile(url, Date.now());
  if (time === null) return json(400, { error: 'invalid-tile' });
  let target;
  try { target = backend(env); } catch { return json(503, { error: 'vps-not-configured' }); }
  target.pathname = route;
  // Only the five validated tile fields cross the boundary; no Apple credentials.
  for (const key of ['z', 'x', 'y', 'scale', 'frameTime']) target.searchParams.set(key, url.searchParams.get(key));
  const headers = new Headers({ Accept: 'image/ktx' });
  if (env.VPS_TOKEN) headers.set('Authorization', `Bearer ${env.VPS_TOKEN}`);
  try {
    const response = await fetchBackend(target.toString(), { method: 'GET', headers, redirect: 'manual', signal: AbortSignal.timeout(7000) });
    if (response.status === 202) {
      await response.body?.cancel();
      return json(202, { status: 'preparing', retryAfter: 10 });
    }
    if ([204, 400, 404, 409, 422, 429, 503].includes(response.status)) {
      await response.body?.cancel();
      return new Response(null, { status: response.status, headers: { 'Cache-Control': 'no-store' } });
    }
    if (response.status !== 200 || !validHeaders(response.headers, time, Date.now() / 1000)) {
      await response.body?.cancel();
      return json(502, { error: 'invalid-upstream' });
    }
    // Read at most one tile, even if a misconfigured VPS streams an arbitrary body.
    const reader = response.body?.getReader();
    if (!reader) return json(502, { error: 'invalid-upstream' });
    const bytes = new Uint8Array(65604);
    let length = 0;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      if (length + value.length > bytes.length) { await reader.cancel(); return json(502, { error: 'invalid-upstream' }); }
      bytes.set(value, length); length += value.length;
    }
    if (length !== bytes.length || !validKTX(bytes)) return json(502, { error: 'invalid-upstream' });
    const output = new Headers({ 'Content-Type': 'image/ktx', 'Cache-Control': 'no-store' });
    for (const name of ['X-WK3-Nowcast', 'X-WK3-Origin', 'X-WK3-Valid-Time', 'X-WK3-Source-Valid-Time', 'X-WK3-Forecast-Start', 'X-WK3-Forecast-End', 'X-WK3-Max-Lead-Minutes', 'X-WK3-Display', 'X-WK3-Attribution']) {
      const value = response.headers.get(name); if (value !== null) output.set(name, value);
    }
    return new Response(bytes, { headers: output });
  } catch { return json(502, { error: 'vps-unavailable' }); }
}
export default { fetch(request, env) { return handle(request, env); } };
