import unittest,tempfile,json,threading,time
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
from hybrid_gateway import Hybrid,linear_ktx,parse,Unavailable
class HybridTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.now=1800000900;self.h=Hybrid(self.tmp.name,clock=lambda:self.now);self.tile=(7,104,52)
  self.m={'radar':{'past':[{'time':1800000000,'path':'a'},{'time':1800000600,'path':'b'}]}}
  self.h.metadata=lambda which:self.m if which=='rv'else 1800000000
  from detail.gray_astc import encode_gray256 as fixture_encoder
  self.encoder_patch=patch('hybrid_gateway.encode_gray256',fixture_encoder);self.encoder_patch.start()
 def tearDown(self):self.h.pool.shutdown(wait=True);self.h.source_pool.shutdown(wait=True);self.h.motion.close();self.encoder_patch.stop();self.tmp.cleanup()
 def test_motion_forecast_does_not_fade_or_call_rainbow(self):
  self.h.http_wait=True;calls=[]
  def predict(m,t,valid,callback):
   calls.append(valid);return np.full((256,256),30.),np.ones((256,256),bool),{'status':'available','model':'advection','dx':.2,'dy':-.1}
  self.h.motion.predict=predict
  self.h.rb_tile=Mock(side_effect=AssertionError('must not call Rainbow'))
  status,h,b=self.h.response(self.tile,1800001500,motion=True)
  self.assertEqual(status,200);self.assertEqual(h['X-WK3-Source'],'RainViewer');self.assertEqual(h['X-WK3-Model'],'advection');self.assertEqual(h['X-WK3-Display'],'prototype-advection-linear');self.assertEqual(h['X-WK3-Origin'],'1800000600');self.assertEqual(h['X-WK3-Source-Valid-Time'],'1800001200');self.assertNotIn('X-WK3-Observation-Weight',h)
  from detail.gray_astc import decode_reference
  self.assertEqual(int(decode_reference(b).max()),128);self.assertEqual(calls,[1800001200])
 def test_unknown_motion_is_labelled_frozen_not_advection(self):
  self.h.http_wait=True
  self.h.motion.predict=lambda *args:(np.full((256,256),30.),np.ones((256,256),bool),{'status':'available','model':'persistence','dx':None,'dy':None})
  status,h,b=self.h.response(self.tile,1800001500,motion=True)
  self.assertEqual(status,200);self.assertEqual(h['X-WK3-Frame-Kind'],'persistence');self.assertEqual(h['X-WK3-Model'],'persistence');self.assertEqual(h['X-WK3-Source-Valid-Time'],h['X-WK3-Origin']);self.assertNotIn('X-WK3-Velocity-X',h)
 def test_motion_missing_coverage_uses_model_without_fabricated_echo(self):
  self.h.http_wait=True
  self.h.motion.predict=lambda *args:(np.full((256,256),np.nan),np.zeros((256,256),bool),{'status':'unavailable','model':'persistence'})
  self.h.rb_tile=lambda *args:(np.full((256,256),10.),np.ones((256,256),bool))
  status,h,b=self.h.response(self.tile,1800001500,motion=True)
  self.assertEqual(status,200);self.assertEqual(h['X-WK3-Source'],'Rainbow');self.assertNotIn('X-WK3-Model',h)

 def test_decoded_anchor_reuse_and_expiry(self):
  count=[]
  def decode(*args):count.append(1);return np.full((256,256),20.),np.ones((256,256),bool)
  self.h._rv_frame=decode;frame=self.m['radar']['past'][-1]
  a,v=self.h.rv_frame(self.m,frame,self.tile);b,w=self.h.rv_frame(self.m,frame,self.tile)
  self.assertIs(a,b);self.assertIs(v,w);self.assertEqual(len(count),1);self.assertFalse(a.flags.writeable)
  self.now+=121;self.h.rv_frame(self.m,frame,self.tile);self.assertEqual(len(count),2)
 def test_blended_prefetch_reuses_anchor_identity(self):
  self.h.rv_meta=self.m;calls=[]
  self.h.build=lambda tile,selection,key:calls.append((selection,key))
  doc={'source':'RainViewer+Rainbow','kind':'forecast','origin':1800000000,'source_time':1800000600,'latest':1800000000,'anchor':1800000600}
  self.h.prefetch_next(self.tile,doc);self.h.pool.shutdown(wait=True)
  self.assertEqual(len(calls),1);self.assertEqual(calls[0][0]['anchor'],doc['anchor']);self.assertEqual(calls[0][0]['source_time'],1800001200)
 def test_display_transition_weights_and_coverage(self):
  from transition import transition_dbz,observation_weight
  a=np.full((256,256),25.);b=np.full((256,256),5.);v=np.ones((256,256),bool)
  for elapsed,weight in [(0,1),(600,2/3),(1200,1/3),(1800,0)]:
   out,w=transition_dbz(a,b,v,v,1000,1000+elapsed);self.assertAlmostEqual(w,weight);self.assertTrue(np.allclose(out,5+20*weight))
  with self.assertRaises(ValueError):transition_dbz(a,b,v,~v,1000,1600)
 def test_transition_cache_keeps_anchor_identity(self):
  a=self.h.choose(self.tile,1800000900);self.assertEqual(a['anchor'],1800000600)
  b=dict(a,anchor=1800000000);self.assertNotEqual(self.h.key(self.tile,a),self.h.key(self.tile,b))
 def test_transition_build_and_headers(self):
  self.h.http_wait=True;self.h.prefetch_next=lambda *args:None
  self.h.rv_frame=lambda *args:(np.full((256,256),25.),np.ones((256,256),bool))
  self.h.rb_tile=lambda *args:(np.full((256,256),5.),np.ones((256,256),bool))
  status,h,b=self.h.response(self.tile,1800000900,transition=True)
  self.assertEqual(status,200);self.assertEqual(h['X-WK3-Source'],'RainViewer+Rainbow');self.assertEqual(h['X-WK3-Display'],'prototype-display-transition');self.assertEqual(h['X-WK3-Origin'],'1800000000');self.assertEqual(h['X-WK3-Blend-Anchor'],'1800000600');self.assertEqual(h['X-WK3-Observation-Weight'],'1.000000');self.assertEqual(len(b),65604)
 def test_floor_history(self):
  s=self.h.choose(self.tile,1800000300);self.assertEqual(s['source_time'],1800000000);self.assertEqual(s['kind'],'observation')
 def test_floor_forecast_and_snapshot_delay(self):
  s=self.h.choose(self.tile,self.now+3300);self.assertEqual(s['source_time'],1800004200);self.assertEqual(s['origin'],1800000000)
 def test_no_future_observation(self):
  with self.assertRaises(Unavailable):self.h.choose(self.tile,1799999700)
 def test_frozen_source_age_preserved(self):
  def metadata(which):
   if which=='rb':raise Unavailable('offline')
   return self.m
  self.h.metadata=metadata;s=self.h.choose(self.tile,self.now+300);self.assertEqual(s['kind'],'persistence');self.assertEqual(s['origin'],1800000600)
 def test_unknown_not_dry(self):
  with self.assertRaises(Unavailable):linear_ktx(np.zeros((256,256)),np.zeros((256,256),bool))
 def test_gray_header_and_clipping(self):
  data=np.tile(np.linspace(-32,95,256),(256,1));b=linear_ktx(data,np.ones((256,256),bool));self.assertEqual(len(b),65604);self.assertEqual(b[:12],b'\xabKTX 11\xbb\r\n\x1a\n');from detail.gray_astc import decode_reference;decoded=decode_reference(b);self.assertEqual(decoded.shape,(512,512));self.assertEqual(int(decoded.max()),255);self.assertEqual(int(decoded.min()),0)
 def test_scope_and_bad_requests(self):
  prefix='/v2/radar?z=7&x=104&y=52&scale=2&frameTime=2027-01-15T08:15:00Z'
  # Parse using its actual timestamp; scope still rejects overseas and duplicated fields.
  import datetime
  now=int(datetime.datetime(2027,1,15,8,15,tzinfo=datetime.timezone.utc).timestamp())
  self.assertEqual(parse(prefix,now,{self.tile})[0],self.tile)
  with self.assertRaises(Unavailable):parse(prefix.replace('x=104','x=18'),now,{self.tile})
  with self.assertRaises(ValueError):parse(prefix+'&x=104',now,{self.tile})
  with self.assertRaises(ValueError):parse(prefix.replace('08:15','08:16'),now,{self.tile})
 def test_single_build_and_cache(self):
  calls=[];gate=threading.Event()
  def frame(*args):calls.append(1);gate.wait(2);return np.ones((256,256))*20,np.ones((256,256),bool)
  self.h.rv_frame=frame
  for _ in range(8):self.assertEqual(self.h.response(self.tile,1800000300)[0],202)
  gate.set();self.h.pool.shutdown(wait=True)
  status,headers,body=self.h.response(self.tile,1800000300);self.assertEqual(status,200);self.assertEqual(len(calls),1);self.assertEqual(len(body),65604);self.assertEqual(headers['X-WK3-Source-Valid-Time'],'1800000000')
 def test_rv_duplicate_palette_and_transparent(self):
  from PIL import Image
  color=next(k for k,v in self.h.colors.items() if len(v)>2 and max(v)<75 and k[3]>0)
  a=np.zeros((256,256,4),dtype='uint8');a[0,0]=color
  self.h.rv.tile=lambda *args:Image.fromarray(a)
  self.h.rv.coverage=lambda *args:Image.fromarray(np.zeros_like(a))
  d,v=self.h.rv_frame(self.m,self.m['radar']['past'][-1],self.tile)
  self.assertTrue(v.all());self.assertEqual(d[0,0],(min(self.h.colors[color])+max(self.h.colors[color]))/2);self.assertEqual(d[1,1],-32)
 def test_http_cold_wait_returns_ready_tile(self):
  self.h.http_wait=True;self.h.rv_frame=lambda *args:(np.ones((256,256))*20,np.ones((256,256),bool))
  status,h,b=self.h.response(self.tile,1800000300);self.assertEqual(status,200);self.assertEqual(len(b),65604)
 def test_next_forecast_prefetch_is_async_and_limited_to_one_frame(self):
  import threading
  started=threading.Event();gate=threading.Event();calls=[]
  def fake_build(tile,selection,key):calls.append(selection['source_time']);started.set();gate.wait(1);self.h.pending.discard(key)
  self.h.build=fake_build
  doc={'kind':'forecast','source':'Rainbow','origin':1799990000,'source_time':1799991200,'latest':1799991200}
  before=time.monotonic();self.h.prefetch_next(self.tile,doc);elapsed=time.monotonic()-before
  self.assertTrue(started.wait(.2));self.assertLess(elapsed,.05);self.assertEqual(calls,[1799991800]);gate.set();self.h.pool.shutdown(wait=True)
 def test_future_fetch_failure_frozen(self):
  self.h.rb_tile=Mock(side_effect=OSError());self.h.rv_frame=lambda *args:(np.zeros((256,256)),np.ones((256,256),bool))
  sel=self.h.choose(self.tile,self.now+300);key=self.h.key(self.tile,sel);self.h.build(self.tile,sel,key)
  status,h,b=self.h.response(self.tile,self.now+300);self.assertEqual(status,202);self.h.pool.shutdown(wait=True)
  status,h,b=self.h.response(self.tile,self.now+300);self.assertEqual(status,200);self.assertEqual(h['X-WK3-Frame-Kind'],'persistence');self.assertEqual(h['X-WK3-Origin'],'1800000600')
if __name__=='__main__':unittest.main()
