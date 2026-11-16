import test from 'node:test';import assert from 'node:assert/strict';import worker from './worker.mjs';
test('generated Worker serves own module and own script, not a shared personal backend',async()=>{
 for(const [p,fragment]of [['/test-assets/wk3-radar-motion-v4.surge.js','https://test-worker.example/v4/radar'],['/test-assets/WeatherKit-Radar-Motion-Test.sgmodule','https://test-worker.example/test-assets/wk3-radar-motion-v4.surge.js']]){
  const r=await worker.fetch(new Request('https://test-worker.example'+p),{});assert.equal(r.status,200);assert.equal(r.headers.get('Cache-Control'),'no-store');assert.ok((await r.text()).includes(fragment));
 }
 const r=await worker.fetch(new Request('https://test-worker.example/v4/radar?invalid=1'),{});assert.equal(r.status,400);
 assert.equal((await worker.fetch(new Request('https://test-worker.example/missing'),{})).status,404);
});
