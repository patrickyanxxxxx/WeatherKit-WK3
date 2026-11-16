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

const TILE_SCOPE=new Set(["7/92/48","7/92/49","7/93/46","7/93/47","7/93/48","7/93/49","7/93/50","7/93/51","7/94/46","7/94/47","7/94/48","7/94/49","7/94/50","7/94/51","7/94/52","7/95/45","7/95/46","7/95/47","7/95/48","7/95/49","7/95/50","7/95/51","7/95/52","7/96/47","7/96/48","7/96/49","7/96/50","7/96/51","7/96/52","7/97/47","7/97/48","7/97/49","7/97/50","7/97/51","7/97/52","7/98/48","7/98/49","7/98/50","7/98/51","7/98/52","7/99/48","7/99/49","7/99/50","7/99/51","7/99/52","7/100/48","7/100/49","7/100/50","7/100/51","7/100/52","7/100/53","7/100/54","7/101/48","7/101/49","7/101/50","7/101/51","7/101/52","7/101/53","7/101/54","7/102/48","7/102/49","7/102/50","7/102/51","7/102/52","7/102/53","7/102/54","7/103/48","7/103/49","7/103/50","7/103/51","7/103/52","7/103/53","7/103/54","7/104/47","7/104/48","7/104/49","7/104/50","7/104/51","7/104/52","7/104/53","7/104/54","7/105/46","7/105/47","7/105/50","7/105/51","7/105/52","7/105/53","7/106/46","7/106/47","7/107/42","7/107/43","7/107/44","7/107/45","7/107/46","7/108/43","7/108/44","7/108/45","7/108/46","7/109/44","7/109/45","7/109/46"]);
const ENDPOINT='__RADAR_ENDPOINT__';
function tile(request,now){
 if(!request||request.method!=='GET'||typeof request.url!=='string')return null;
 const u=new URL(request.url);
 if(u.protocol!=='https:'||u.hostname!=='weather-map2.apple.com'||(u.port&&u.port!=='443')||u.username||u.password||u.hash||u.pathname!=='/v1/mapOverlay/precipitationRadarMap')return null;
 const t=parseTile(u,now);return t&&TILE_SCOPE.has(t.z+'/'+t.x+'/'+t.y)?t:null;
}
function gatewayURL(t){const u=new URL(ENDPOINT);for(const[k,v]of Object.entries({z:t.z,x:t.x,y:t.y,scale:2,frameTime:t.iso}))u.searchParams.set(k,v);return u.toString();}
function validate(response,body,t,now){
 if(Number(response.status??response.statusCode)!==200)throw Error('status');
 if(!validHeaders(response.headers,t.time,now))throw Error('headers');
 const bytes=bytesOf(body);if(!validKTX(bytes))throw Error('ktx');
 return{bytes,headers:outputHeaders(response.headers)};
}
(async()=>{
 let finished=false,timer;
 const done=value=>{if(!finished){finished=true;$done(value);}};
 const started=Date.now();
 try{
  const t=tile($request,Math.floor(started/1000));if(!t)return done({});
  timer=setTimeout(()=>{console.log('[WK3 RAINBOW V4.5] timeout');done({});},8500);
  let raw;
  for(let attempt=0;attempt<2;attempt++){
   const remaining=8000-(Date.now()-started);if(remaining<500)return done({});
   raw=await new Promise((resolve,reject)=>$httpClient.get({url:gatewayURL(t),headers:{Accept:'image/ktx'},timeout:Math.min(6,remaining/1000),'binary-mode':true,'auto-redirect':false,'auto-cookie':false},(error,response,data)=>error?reject(Error('network')):resolve({response,data})));
   if(finished)return;
   const status=Number(raw.response?.status??raw.response?.statusCode??0);
   console.log('[WK3 RAINBOW V4.5] gateway-status='+status+' attempt='+(attempt+1)+' x='+t.x+' y='+t.y+' valid='+t.time);
   if(status===200)break;
   // 202 means the same shared job is preparing. No retry on quota/errors.
   if(status!==202||attempt===1||Date.now()-started>5000)return done({});
  }
  const good=validate(raw.response,raw.data,t,Math.floor(Date.now()/1000));
  console.log('[WK3 RAINBOW V4.5] '+good.headers['X-WK3-Frame-Kind']+' source='+good.headers['X-WK3-Source']+' model='+good.headers['X-WK3-Model']+' valid='+t.time+' KTX='+good.bytes.length+' total-ms='+(Date.now()-started));
  done({response:{status:200,headers:good.headers,body:good.bytes}});
 }catch{console.log('[WK3 RAINBOW V4.5] passthrough rejected-or-unavailable');done({});}
 finally{if(timer)clearTimeout(timer);}
})();
