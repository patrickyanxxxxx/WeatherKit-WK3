// EXPERIMENTAL radar echo. Empty endpoint = Apple passthrough. No Apple headers leave device.
function config(argument, env) {
  let c = Object.assign({}, env || {});
  if (typeof argument === 'string' && argument.trim()) {
    const s = argument.trim();
    if (/^https?:\/\//i.test(s)) c.endpoint=s.replace(/^["']|["']$/g,'');
    else if (s[0] === '{') Object.assign(c, JSON.parse(s));
    else if (/^(?:endpoint|RADAR_ENDPOINT)\s*[:=]/i.test(s)) {
      const m=s.match(/^(?:endpoint|RADAR_ENDPOINT)\s*[:=]\s*["']?(https?:\/\/[^"'\s,}&]+)["']?$/i);
      if (m) c.endpoint=m[1];
      else if (/^(?:endpoint|RADAR_ENDPOINT)=/i.test(s)) {
        const i=s.indexOf('='); c.endpoint=decodeURIComponent(s.slice(i+1).replace(/\+/g,' '));
      } else throw Error('config');
    } else {
      const seen = new Set();
      for (const item of s.split('&')) {
        const i = item.indexOf('='); if (i < 0) throw Error('config');
        const k = decodeURIComponent(item.slice(0, i));
        let v = item.slice(i + 1); if (/^(["']).*\1$/.test(v)) v = v.slice(1, -1);
        if (seen.has(k)) throw Error('config'); seen.add(k);
        c[k] = decodeURIComponent(v.replace(/\+/g, ' '));
      }
    }
  } else if (argument && typeof argument === 'object' && !Array.isArray(argument)) Object.assign(c, argument);
  const value = c.endpoint ?? c.RADAR_ENDPOINT ?? '';
  if (typeof value !== 'string' || !value.trim()) return null;
  const u = new URL(value.trim());
  const loopback = u.hostname === '127.0.0.1' || u.hostname === '[::1]' || u.hostname === 'localhost';
  if (u.protocol === 'http:' && !loopback) throw Error('config');
  if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password || u.search || u.hash || u.pathname !== '/v1/radar' || u.hostname.endsWith('.apple.com') || u.hostname === 'apple.com') throw Error('config');
  return u;
}
function tile(request, now) {
  if (!request || request.method !== 'GET' || typeof request.url !== 'string') return null;
  const u = new URL(request.url);
  if (u.protocol !== 'https:' || u.hostname !== 'weather-map2.apple.com' || (u.port && u.port !== '443') || u.username || u.password || u.hash || u.pathname !== '/v1/mapOverlay/precipitationRadarMap') return null;
  const allow = ['z', 'x', 'y', 'scale', 'frameTime'];
  const q = u.searchParams;
  if ([...q.keys()].length !== 5 || [...q.keys()].some(k => !allow.includes(k)) || allow.some(k => q.getAll(k).length !== 1)) return null;
  if (!/^[0-7]$/.test(q.get('z')) || q.get('scale') !== '2') return null;
  const z = Number(q.get('z'));
  for (const k of ['x', 'y']) if (!/^(0|[1-9][0-9]*)$/.test(q.get(k)) || Number(q.get(k)) >= 2 ** z) return null;
  const s = q.get('frameTime');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::00(?:\.000)?)?Z$/.test(s)) return null;
  const ms = Date.parse(s);
  if (!Number.isFinite(ms) || new Date(ms).toISOString().replace('.000Z', 'Z') !== s.replace('.000Z', 'Z').replace(/T(\d{2}:\d{2})Z$/, 'T$1:00Z') || ms % 300000 !== 0) return null;
  const time = ms / 1000;
  if (time > now + 3600 || time < now - 1200) return null;
  return { z, x: Number(q.get('x')), y: Number(q.get('y')), time, iso: new Date(ms).toISOString() };
}
function headersOf(value) {
  const out = {};
  const add = (k, v) => { k = k.toLowerCase(); if (Object.hasOwnProperty.call(out, k)) throw Error('headers'); out[k] = String(v); };
  if (value && typeof value.forEach === 'function') value.forEach((v, k) => add(k, v));
  else for (const k of Object.keys(value || {})) add(k, value[k]);
  return out;
}
function bytesOf(body) {
  if (body instanceof ArrayBuffer) return new Uint8Array(body);
  if (ArrayBuffer.isView(body)) return new Uint8Array(body.buffer, body.byteOffset, body.byteLength);
  if (typeof body === 'string') {
    const b = new Uint8Array(body.length);
    for (let i = 0; i < body.length; i++) { const c = body.charCodeAt(i); if (c > 255) throw Error('binary'); b[i] = c; }
    return b;
  }
  throw Error('binary');
}
function validate(response, body, t, now) {
  if (Number(response.status ?? response.statusCode) !== 200) throw Error('status');
  const h = headersOf(response.headers);
  if (h['x-wk3-nowcast'] !== 'EXPERIMENTAL-RADAR-ECHO' || !/^image\/ktx(?:\s*;.*)?$/i.test(h['content-type'] || '') || !/(?:^|,)\s*no-store\s*(?:,|$)/i.test(h['cache-control'] || '')) throw Error('marker');
  const valid = h['x-wk3-valid-time'], origin = h['x-wk3-origin'];
  if (!/^[0-9]{1,12}$/.test(valid || '') || !/^[0-9]{1,12}$/.test(origin || '') || Number(valid) !== t.time || Number(origin) > now || now - Number(origin) > 1200 || t.time < Number(origin) || t.time > Number(origin) + 3600) throw Error('time');
  const source = h['x-wk3-source-valid-time'];
  if (source !== undefined && (!/^[0-9]{1,12}$/.test(source) || Math.abs(Number(source) - t.time) > 300)) throw Error('source-time');
  const b = bytesOf(body);
  if (b.length !== 65604) throw Error('size');
  const magic = [171,75,84,88,32,49,49,187,13,10,26,10];
  if (magic.some((v, i) => b[i] !== v)) throw Error('ktx');
  const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
  const fields = [0x04030201,0,1,0,0x93b7,0x1908,512,512,0,0,1,1,0,65536];
  if (fields.some((v, i) => view.getUint32(12 + 4 * i, true) !== v)) throw Error('ktx');
  return { bytes: b, headers: { 'Content-Type': 'image/ktx', 'Cache-Control': 'no-store', 'X-WK3-Nowcast': h['x-wk3-nowcast'], 'X-WK3-Valid-Time': valid, 'X-WK3-Origin': origin, ...(source === undefined ? {} : { 'X-WK3-Source-Valid-Time': source }) } };
}
function gatewayURL(endpoint, t) {
  // Construct afresh. No Apple URL, auth, query extras or request headers are copied.
  endpoint.search = 'z=' + t.z + '&x=' + t.x + '&y=' + t.y + '&frameTime=' + encodeURIComponent(t.iso) + '&scale=2';
  return endpoint.toString();
}
function binaryString(bytes) {
  let s = ''; for (let i = 0; i < bytes.length; i += 8192) s += String.fromCharCode(...bytes.subarray(i, i + 8192)); return s;
}
