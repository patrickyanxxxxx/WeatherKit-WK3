import unittest,tempfile,json,threading,urllib.request,urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
import numpy as np
from native_gateway import Store,encode_echo,parse_request,handler,MARKER
class Tests(unittest.TestCase):
 def test_echo_texture_no_false_intensity(self):
  e=np.zeros((256,256),bool);v=np.ones_like(e);e[:128]=True;b=encode_echo(e,v)
  self.assertEqual(len(b),65604)
  import struct
  self.assertEqual(struct.unpack_from('<H',b,76)[0],48*257)
  self.assertEqual(struct.unpack_from('<H',b,68+2048*16+8)[0],0)
  v[0,0]=False
  with self.assertRaises(ValueError):encode_echo(e,v)
 def test_request_strict_coordinates_and_time(self):
  q=dict(z=7,x=108,y=47,scale=2,frameTime='2026-11-06T16:35Z');path='/v1/radar?'+urlencode(q)
  t,ts=parse_request(path);self.assertEqual(t,(7,108,47));self.assertEqual(ts%300,0)
  for bad in [path+'&x=108',path+'&auth=secret',path.replace('z=7','z=8'),path.replace('scale=2','scale=1'),path.replace('16%3A35','16%3A34')]:
   with self.assertRaises(ValueError):parse_request(bad)
 def test_stale_historical_future_and_unknown_fallback(self):
  with tempfile.TemporaryDirectory()as tmp:
   now=1793983200;s=Store(tmp,clock=lambda:now);t=(7,108,47);d=s.directory(t);d.mkdir();b=encode_echo(np.ones((256,256),bool),np.ones((256,256),bool));(d/'f.ktx').write_bytes(b)
   m=dict(origin=now-300,frames=[dict(valid_time=now-300,file='f.ktx')],mode='echo_occurrence_only',historical_replay=False)
   p=d/'manifest.json';p.write_text(json.dumps(m));r=s.lookup(t,now-300);self.assertEqual(r[0],b);self.assertEqual(r[1]['X-WK3-Nowcast'],MARKER)
   self.assertIsNone(s.lookup(t,now));self.assertIsNone(s.lookup(t,now+301));self.assertIsNone(s.lookup(t,now-301))
   m['historical_replay']=True;p.write_text(json.dumps(m));self.assertIsNone(s.lookup(t,now-300))
   m.update(historical_replay=False,origin=now-1201);p.write_text(json.dumps(m));self.assertIsNone(s.lookup(t,now-1201))
 def test_actual_http_exact_body_and_missing_passthrough(self):
  with tempfile.TemporaryDirectory()as tmp:
   now=1793983200;s=Store(tmp,clock=lambda:now);t=(7,108,47);d=s.directory(t);d.mkdir();body=encode_echo(np.ones((256,256),bool),np.ones((256,256),bool));(d/'f.ktx').write_bytes(body)
   (d/'manifest.json').write_text(json.dumps(dict(origin=now,frames=[dict(valid_time=now,file='f.ktx')],mode='echo_occurrence_only',historical_replay=False)))
   server=ThreadingHTTPServer(('127.0.0.1',0),handler(s,allow_prepare=False));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   try:
    from datetime import datetime,timezone
    q=dict(z=7,x=108,y=47,scale=2,frameTime=datetime.fromtimestamp(now,timezone.utc).isoformat());url=f'http://127.0.0.1:{server.server_port}/v1/radar?'+urlencode(q)
    with urllib.request.urlopen(url)as r:self.assertEqual(r.status,200);self.assertEqual(r.read(),body);self.assertEqual(r.headers['X-WK3-Origin'],str(now))
    with urllib.request.urlopen(url.replace('x=108','x=107'))as r:self.assertEqual(r.status,202);self.assertEqual(r.read(),b'')
    with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/status')as r:status=json.load(r)
    self.assertEqual(status['radar_requests'],2)
    self.assertEqual(status['radar_responses'],{'200':1,'202':1})
   finally:server.shutdown();server.server_close();thread.join()
 def test_incomplete_forecast_does_not_publish_manifest(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp:
   now=1793983200; tile=(7,104,50);s=Store(tmp,source=type('Source',(),{'metadata':lambda self: metadata})(),clock=lambda:now)
   d=s.directory(tile);d.mkdir();old=dict(origin=now-600,mode='echo_occurrence_only',frames=[])
   (d/'manifest.json').write_text(json.dumps(old))
   frames=[dict(crop=(256,512,256,512),echo=np.ones((256,256),bool),valid=np.ones((256,256),bool)) for _ in range(3)]
   metadata={'radar':{'past':[{'time':now-1200},{'time':now-600},{'time':now}]}}
   unsupported=(np.zeros((256,256),bool),np.zeros((256,256),bool),None)
   with patch('native_gateway.load_mosaic',side_effect=frames), patch('native_gateway.crop_frame',return_value=frames[-1]), patch('native_gateway.estimate',return_value={'status':'estimated'}), patch('native_gateway.predict',return_value=unsupported):
    with self.assertRaisesRegex(ValueError,'Incomplete'):s.build(tile)
   self.assertEqual(json.loads((d/'manifest.json').read_text()),old)
   self.assertIsNone(s.lookup(tile,now))

