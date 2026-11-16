// Shared by the standalone route and the Surge bundle; no runtime dependencies.
const MARKER = 'EXPERIMENTAL-RADAR-LINEAR';
const LINEAR_DISPLAY = 'prototype-linear-uncalibrated';
const FROZEN_DISPLAY = 'prototype-frozen-linear';
const TILE_FIELDS = ['z', 'x', 'y', 'scale', 'frameTime'];
const ATTRIBUTIONS = {
  RainViewer: ['Weather data by RainViewer','Weather data by RainViewer; advection by this project'],
  Rainbow: ['Powered by Rainbow.ai'],
  'RainViewer+Rainbow': ['Weather data by RainViewer; Powered by Rainbow.ai']
};
function parseTile(url, now) {
  const q = url.searchParams;
  if ([...q.keys()].length !== 5 || TILE_FIELDS.some(k => q.getAll(k).length !== 1) || [...q.keys()].some(k => !TILE_FIELDS.includes(k))) return null;
  if (q.get('z') !== '7' || q.get('scale') !== '2') return null;
  for (const k of ['x', 'y']) if (!/^(0|[1-9][0-9]*)$/.test(q.get(k)) || Number(q.get(k)) >= 128) return null;
  const text = q.get('frameTime');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::00(?:\.000)?)?Z$/.test(text)) return null;
  const ms = Date.parse(text);
  if (!Number.isFinite(ms) || new Date(ms).toISOString().replace('.000Z', 'Z') !== text.replace('.000Z', 'Z').replace(/T(\d{2}:\d{2})Z$/, 'T$1:00Z') || ms % 300000) return null;
  const time = ms / 1000;
  if (time < now - 7200 || time > now + 3600) return null;
  return { z: 7, x: Number(q.get('x')), y: Number(q.get('y')), time, iso: new Date(ms).toISOString() };
}
function headersOf(value) {
  const out = {};
  const add = (k, v) => {
    k = k.toLowerCase();
    if (Object.prototype.hasOwnProperty.call(out, k)) throw Error('headers');
    const text = String(v);
    if (/[\x00-\x1f\x7f]/.test(text)) throw Error('headers');
    out[k] = text;
  };
  if (value && typeof value.forEach === 'function') value.forEach((v, k) => add(k, v));
  else for (const k of Object.keys(value || {})) add(k, value[k]);
  return out;
}
function validHeaders(headers, requested, now) {
  let h;
  try { h = headersOf(headers); } catch { return false; }
  if (h['x-wk3-nowcast'] !== MARKER || !/^image\/ktx(?:\s*;.*)?$/i.test(h['content-type'] || '') || !/(?:^|,)\s*no-store\s*(?:,|$)/i.test(h['cache-control'] || '')) return false;
  const sourceName = h['x-wk3-source'], attribution = h['x-wk3-attribution'];
  if (!Object.prototype.hasOwnProperty.call(ATTRIBUTIONS, sourceName) || !ATTRIBUTIONS[sourceName].includes(attribution)) return false;
  const keys = ['origin', 'valid-time', 'source-valid-time', 'latest-source'];
  if (!keys.every(k => /^[0-9]{1,12}$/.test(h['x-wk3-' + k] || ''))) return false;
  const [origin, valid, source, latest] = keys.map(k => Number(h['x-wk3-' + k]));
  if (valid !== requested || requested < now - 7200 || requested > now + 3600 || origin > source || latest > now || now - latest > 1800) return false;
  const kind = h['x-wk3-frame-kind'];
  if (h['x-wk3-model']==='advection' || h['x-wk3-display']==='prototype-advection-linear') {
    const velocity=['x','y'].map(axis=>h['x-wk3-velocity-'+axis]);
    return kind==='forecast' && sourceName==='RainViewer' && attribution==='Weather data by RainViewer; advection by this project' &&
      h['x-wk3-model']==='advection' && h['x-wk3-display']==='prototype-advection-linear' &&
      origin===latest && now-origin<1800 && source>=origin && source-origin<=4800 && source<=requested && requested-source<600 &&
      velocity.every(v=>/^-?\d+\.\d{6}$/.test(v||'') && Number.isFinite(Number(v)) && Math.abs(Number(v))<=10);
  }
  if (kind === 'persistence') return h['x-wk3-display'] === FROZEN_DISPLAY && source === origin && origin === latest && requested >= origin;
  if (sourceName === 'RainViewer+Rainbow') {
    const anchorText=h['x-wk3-blend-anchor'], weightText=h['x-wk3-observation-weight'];
    if(!/^[0-9]{1,12}$/.test(anchorText||'') || !/^(?:0|1)\.\d{6}$/.test(weightText||''))return false;
    const anchor=Number(anchorText),weight=Number(weightText),expected=1-(source-anchor)/1800;
    return kind==='forecast' && h['x-wk3-display']==='prototype-display-transition' &&
      anchor<=now && now-anchor<=1800 && anchor<=source && source-anchor<1800 &&
      weight>0 && weight<=1 && Math.abs(weight-expected)<0.000002 &&
      origin<=now && now-origin<=1800 && source-origin<=14400 &&
      source<=requested && requested-source<600 && requested>=anchor && requested>=origin;
  }
  if (!['observation', 'forecast'].includes(kind) || h['x-wk3-display'] !== LINEAR_DISPLAY || source > requested || requested - source >= 600) return false;
  if (kind === 'observation') return origin === source && source <= latest && requested <= now;
  return sourceName === 'Rainbow' && origin <= now && now - origin <= 1800 && source - origin <= 14400 && requested >= origin;
}
function bytesOf(body) {
  if (body instanceof ArrayBuffer) return new Uint8Array(body);
  if (ArrayBuffer.isView(body)) return new Uint8Array(body.buffer, body.byteOffset, body.byteLength);
  if (typeof body === 'string') {
    if (body.length !== 65604) throw Error('size');
    const bytes = new Uint8Array(body.length);
    for (let i = 0; i < body.length; i++) { const c = body.charCodeAt(i); if (c > 255) throw Error('binary'); bytes[i] = c; }
    return bytes;
  }
  throw Error('binary');
}
function validKTX(bytes) {
  if (bytes.length !== 65604) return false;
  if ([171,75,84,88,32,49,49,187,13,10,26,10].some((v, i) => bytes[i] !== v)) return false;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return [0x04030201,0,1,0,0x93b7,0x1908,512,512,0,0,1,1,0,65536].every((v, i) => view.getUint32(12 + 4 * i, true) === v);
}
function outputHeaders(headers) {
  const h = headersOf(headers);
  const out = { 'Content-Type': 'image/ktx', 'Cache-Control': 'no-store, no-transform' };
  for (const name of ['Nowcast', 'Frame-Kind', 'Latest-Source', 'Origin', 'Valid-Time', 'Source-Valid-Time', 'Display', 'Source', 'Attribution']) out['X-WK3-' + name] = h['x-wk3-' + name.toLowerCase()];
  for(const name of ['Blend-Anchor','Observation-Weight']) {
    const value=h['x-wk3-'+name.toLowerCase()];if(value!==undefined)out['X-WK3-'+name]=value;
  }
  if(['advection','persistence'].includes(h['x-wk3-model']))out['X-WK3-Model']=h['x-wk3-model'];
  for(const axis of ['X','Y'])if(/^-?\d+\.\d{6}$/.test(h['x-wk3-velocity-'+axis.toLowerCase()]||''))out['X-WK3-Velocity-'+axis]=h['x-wk3-velocity-'+axis.toLowerCase()];
  if(['hit','built'].includes(h['x-wk3-cache']))out['X-WK3-Cache']=h['x-wk3-cache'];
  if(/^select;dur=\d+(?:\.\d+)?, tile;dur=\d+(?:\.\d+)?$/.test(h['server-timing']||''))out['Server-Timing']=h['server-timing'];
  return out;
}

// This is a named declaration file. build.mjs copies schema.js into the route
// handler, so new-handler.mjs is self-contained when integrated into a Worker.
const ROUTE = '/v4/radar';
const json = (status, body) => Response.json(body, { status, headers: { 'Cache-Control': 'no-store', 'X-WK3-Display': LINEAR_DISPLAY } });
function backend(env) {
  const u = new URL(env.VPS_ORIGIN);
  if (u.protocol !== 'https:' || u.username || u.password || u.search || u.hash || u.pathname !== '/' || /(^|\.)apple\.com$/i.test(u.hostname)) throw Error('backend');
  return u;
}
export async function handleNew(request, env, fetchBackend = fetch) {
  const url = new URL(request.url);
  if (url.pathname !== ROUTE) return json(404, { error: 'not-found' });
  if (request.method !== 'GET') return new Response(null, { status: 405, headers: { Allow: 'GET', 'Cache-Control': 'no-store', 'X-WK3-Display': LINEAR_DISPLAY } });
  const tile = parseTile(url, Date.now() / 1000);
  if (!tile) return json(400, { error: 'invalid-tile' });
  let target;
  try { target = backend(env); } catch { return json(503, { error: 'vps-not-configured' }); }
  target.pathname = ROUTE;
  for (const key of TILE_FIELDS) target.searchParams.set(key, url.searchParams.get(key));
  const headers = new Headers({ Accept: 'image/ktx' });
  if (env.VPS_TOKEN) headers.set('Authorization', `Bearer ${env.VPS_TOKEN}`);
  // One deadline covers fetch AND reading the bounded body, even if a fake or
  // unusual fetch implementation does not reject its reader on AbortSignal.
  const controller = new AbortController();
  let timer;
  const work = async () => {
    const response = await fetchBackend(target.toString(), { method: 'GET', headers, redirect: 'manual', signal: controller.signal });
    if (controller.signal.aborted) { await response.body?.cancel(); throw Error('timeout'); }
    if ([202, 204, 400, 404, 409, 422, 429, 503].includes(response.status)) {
      const diagnostic = new Headers({ 'Cache-Control': 'no-store', 'X-WK3-Display': LINEAR_DISPLAY });
      const retry = response.headers.get('Retry-After');
      if (/^[0-9]{1,3}$/.test(retry || '')) diagnostic.set('Retry-After', retry);
      const state = response.headers.get('X-WK3-State');
      if (['queued','computing','ready'].includes(state)) diagnostic.set('X-WK3-State', state);
      const position = response.headers.get('X-WK3-Queue-Position');
      if (/^[0-9]{1,2}$/.test(position || '')) diagnostic.set('X-WK3-Queue-Position', position);
      const reason = response.headers.get('X-WK3-Reason');
      if (/^[a-z][a-z-]{0,63}$/.test(reason || '')) diagnostic.set('X-WK3-Reason', reason);
      await response.body?.cancel();
      return new Response(null, { status: response.status, headers: diagnostic });
    }
    if (response.status !== 200 || !validHeaders(response.headers, tile.time, Date.now() / 1000)) {
      await response.body?.cancel(); return json(502, { error: 'invalid-upstream' });
    }
    const reader = response.body?.getReader();
    if (!reader) return json(502, { error: 'invalid-upstream' });
    const cancel = () => { reader.cancel().catch(() => {}); };
    controller.signal.addEventListener('abort', cancel, { once: true });
    try {
      const bytes = new Uint8Array(65604);
      let length = 0;
      for (;;) {
        const { value, done } = await reader.read();
        if (controller.signal.aborted) throw Error('timeout');
        if (done) break;
        if (length + value.length > bytes.length) { await reader.cancel(); return json(502, { error: 'invalid-upstream' }); }
        bytes.set(value, length); length += value.length;
      }
      if (length !== bytes.length || !validKTX(bytes)) return json(502, { error: 'invalid-upstream' });
      return new Response(bytes, { headers: outputHeaders(response.headers) });
    } finally { controller.signal.removeEventListener('abort', cancel); reader.releaseLock(); }
  };
  try {
    return await Promise.race([work(), new Promise((_, reject) => {
      timer = setTimeout(() => { controller.abort(); reject(Error('timeout')); }, 7000);
    })]);
  } catch { return json(502, { error: 'vps-unavailable' }); }
  finally { clearTimeout(timer); }
}
export { parseTile, validHeaders, validKTX, ATTRIBUTIONS };
export const handle = handleNew;
export default { fetch(request, env) { return handleNew(request, env); } };
