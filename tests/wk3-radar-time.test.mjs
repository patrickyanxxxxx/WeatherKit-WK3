import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { validHeaders } from '../cloudflare/worker.mjs';
const now = 1794040000, origin = now - 600, target = now + 3600;
const ktx = new Uint8Array(65604);
ktx.set([171,75,84,88,32,49,49,187,13,10,26,10]);
[0x04030201,0,1,0,0x93b7,0x1908,512,512,0,0,1,1,0,65536].forEach((n,i)=>new DataView(ktx.buffer).setUint32(12+4*i,n,true));
const core = vm.runInNewContext(fs.readFileSync(new URL('../src/radar/core.js',import.meta.url),'utf8')+'\n({validate})', { URL, Uint8Array, ArrayBuffer, DataView });
const original = { 'Content-Type':'image/ktx', 'Cache-Control':'no-store', 'X-WK3-Nowcast':'EXPERIMENTAL-RADAR-ECHO', 'X-WK3-Origin':String(origin), 'X-WK3-Valid-Time':String(target), 'X-WK3-Source-Valid-Time':String(target) };
function check(headers, accepted) {
  assert.equal(validHeaders(new Headers(headers), target, now), accepted);
  const run=()=>core.validate({status:200,headers},ktx,{time:target},now);
  if (accepted) assert.equal(run().bytes.length,65604); else assert.throws(run);
}
test('delayed origin requires explicit computed end for now plus 60 minutes',()=>{
  check(original,false);
  check({...original,'X-WK3-Forecast-End':String(target)},true);
});
test('computed end cannot extend beyond 80 minutes or accept uncomputed target',()=>{
  for (const end of [target-1,origin+4801,'NaN']) check({...original,'X-WK3-Forecast-End':String(end)},false);
  check({...original,'X-WK3-Forecast-End':String(target),'X-WK3-Source-Valid-Time':String(target+300)},false);
});
