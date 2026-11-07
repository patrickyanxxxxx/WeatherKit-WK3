import json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from native_gateway import Store
from global_source import RateLimitPending,PublicSource

class WindowTests(unittest.TestCase):
 def test_delayed_source_computes_real_extended_leads_and_expires(self):
  with tempfile.TemporaryDirectory() as tmp:
   now=1794039600;clock=[now+31];origin=now-600
   metadata={'radar':{'past':[{'time':origin-1200},{'time':origin-600},{'time':origin}]}}
   source=type('Source',(),{'metadata':lambda self:metadata})()
   store=Store(tmp,source=source,clock=lambda:clock[0]);tile=(7,105,48)
   frame=dict(crop=(0,256,0,256),echo=np.ones((256,256),bool),valid=np.ones((256,256),bool))
   with patch('native_gateway.load_mosaic',return_value=frame),patch('native_gateway.crop_frame',return_value=frame),patch('native_gateway.estimate',return_value={'status':'estimated'}),patch('native_gateway.predict',return_value=(frame['echo'],frame['valid'],None)) as prediction:
    doc=store.build(tile)
   self.assertEqual([x.args[2] for x in prediction.call_args_list],[10,20,30,40,50,60,70])
   self.assertEqual(len(doc['frames']),7);self.assertEqual(doc['window_end'],now+3600)
   for offset in range(0,3601,300):
    result=store.lookup(tile,now+offset);self.assertIsNotNone(result)
    self.assertLessEqual(abs(int(result[1]['X-WK3-Source-Valid-Time'])-(now+offset)),300)
   self.assertEqual(store.lookup(tile,now+3600)[1]['X-WK3-Lead-Minutes'],'70.0')
   self.assertIsNone(store.lookup(tile,now+3601))
   self.assertIsNone(store.lookup(tile,now+3900))
   self.assertEqual(store.lookup(tile,now+3600)[1]['X-WK3-Forecast-End'],str(now+3600))
   clock[0]=doc['expires_at'];self.assertIsNone(store.lookup(tile,now))
 def test_single_worker_queues_other_tiles_and_bounds_backlog(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(tmp);started=threading.Event();release=threading.Event();done=threading.Event();calls=[]
   def build(tile):
    calls.append(tile)
    if len(calls)==1:started.set();release.wait(5)
    if len(calls)==17:done.set()
   store.build=build
   self.assertTrue(store.prepare((7,0,0)));self.assertTrue(started.wait(2))
   self.assertFalse(store.prepare((7,0,0)))
   for i in range(1,17):self.assertTrue(store.prepare((7,i,0)))
   self.assertFalse(store.prepare((7,17,0)));self.assertEqual(store.jobs.qsize(),16)
   release.set();self.assertTrue(done.wait(3));store.jobs.join()
   self.assertEqual(calls,[(7,i,0) for i in range(17)]);self.assertFalse(store.pending)
 def test_rate_limit_retries_tile_instead_of_dropping_it(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(tmp)
   with patch.object(store,'build',side_effect=[RateLimitPending('quota'),{}]) as build,patch('native_gateway.threading.Event.wait',return_value=True):
    self.assertTrue(store.prepare((7,1,1)));store.jobs.join()
   self.assertEqual(build.call_count,2);self.assertFalse(store.failed)
 def test_cleanup_retains_published_and_removes_old_artifacts(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(tmp,clock=lambda:10000);tile=(7,1,1);d=store.directory(tile);d.mkdir()
   (d/'keep.ktx').write_bytes(b'kept');(d/'old.ktx').write_bytes(b'old')
   (d/'manifest.json').write_text(json.dumps(dict(generated_at=9999,frames=[dict(file='keep.ktx')])))
   store.cleanup();self.assertTrue((d/'keep.ktx').exists());self.assertFalse((d/'old.ktx').exists())
   store.clock=lambda:18000;store.cleanup();self.assertFalse((d/'keep.ktx').exists());self.assertFalse((d/'manifest.json').exists())
 def test_public_source_still_rejects_request_91_without_network(self):
  import time
  with tempfile.TemporaryDirectory() as tmp:
   source=PublicSource(tmp);(Path(tmp)/'request-times.json').write_text(json.dumps([time.time()]*90))
   with patch('urllib.request.urlopen') as network:
    with self.assertRaises(RateLimitPending):source._get('https://example.invalid','test.png')
   network.assert_not_called()
