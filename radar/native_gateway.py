"""Local experimental native radar bridge. Echo occurrence only, NOT rain intensity.
Default: loopback. Source attribution retained in response headers and /status.
No Apple authentication enters this service. Cold tiles return202 while preparing.
"""
import argparse,json,threading,time,math,os,secrets,queue
from pathlib import Path
from datetime import datetime
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
import numpy as np
from global_source import PublicSource,validate,RateLimitPending
from mosaic import load_mosaic
from evaluate_v2 import crop_frame
from motion_v2 import estimate,predict
from apple_texture import _encode_cells

MARKER='EXPERIMENTAL-RADAR-ECHO'

def encode_echo(echo,valid):
    """Use a constant display signal for echo presence, never calibrated intensity.
    64x64 area cells, majority occurrence. Any unknown pixel rejects this tile.
    """
    e,v=np.asarray(echo),np.asarray(valid)
    if e.shape!=(256,256)or v.shape!=e.shape or e.dtype!=bool or v.dtype!=bool:
        raise ValueError('Expected256x256 boolean occurrence and support')
    if not v.all():raise ValueError('Unknown pixels: retain original Apple response')
    cells=(e.reshape(64,4,64,4).mean(axis=(1,3))>=.5)
    return _encode_cells(np.where(cells,48,0).astype(np.uint8))

class Store:
    def __init__(self,root,source=None,clock=time.time):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.source=source or PublicSource();self.clock=clock
        self.lock=threading.Lock();self.pending=set();self.failed={};self.last_attempt={}
        self.jobs=queue.Queue(maxsize=16);self.worker=None;self.active=None
        self.requests=0;self.responses={};self.started_at=self.clock();self.observations={}
        self.recent_http=[]
    def http_event(self,stage,**fields):
        with self.lock:
            self.recent_http.append(dict(time=self.clock(),stage=stage,**fields))
            self.recent_http=self.recent_http[-12:]
    def record(self,status):
        with self.lock:self.responses[str(status)]=self.responses.get(str(status),0)+1
    def directory(self,tile):return self.root/('-'.join(map(str,tile)))
    def build(self,tile):
        m=self.source.metadata();fs=m['radar']['past'][-3:];times=[f['time']for f in fs]
        if len(times)!=3 or any(b-a!=600 for a,b in zip(times,times[1:])):
            raise ValueError('Missing consecutive source frames')
        if not -60<=self.clock()-times[-1]<=1200:raise ValueError('Stale source')
        previous=self.directory(tile)/'manifest.json'
        if previous.exists():
            cached=json.loads(previous.read_text())
            if (cached.get('origin')==times[-1] and cached.get('mode')=='echo_occurrence_only'
                    and len(cached.get('frames',[]))==7
                    and cached.get('window_end',0)>=math.floor(self.clock()/300)*300+3600
                    and cached.get('expires_at',0)>self.clock()):return cached
        frames=[load_mosaic(self.source,m,f,tile)for f in fs];crop=frames[-1]['crop']
        v=estimate(frames,times);origin=crop_frame(frames[-1],crop)
        if v['status']!='estimated':raise ValueError('Unknown motion; retain original radar')
        d=self.directory(tile);d.mkdir(exist_ok=True);items=[];payloads=[]
        start=max(times[-1],math.floor(self.clock()/300)*300)
        for valid_time in range(start,start+3601,600):
            lead=(valid_time-times[-1])/60
            e,valid=(origin['echo'],origin['valid'])if lead==0 else predict(frames[-1],v,lead,output_region=crop)[:2]
            try:b=encode_echo(e,valid)
            except ValueError:continue
            name=f'{times[-1]}-{valid_time}.ktx';payloads.append((name,b))
            items.append(dict(valid_time=valid_time,lead_minutes=lead,file=name))
        if len(items)!=7:raise ValueError('Incomplete current-to-60 minute forecast: retain original Apple response')
        if self.clock()>=min(times[-1]+1200,start+300):
            raise ValueError('Prediction expired during computation; retry current window')
        for name,b in payloads:
            temp=d/(name+'.tmp');temp.write_bytes(b);temp.replace(d/name)
        doc=dict(tile=list(tile),origin=times[-1],frames=items,source='RainViewer',
                 mode='echo_occurrence_only',gray_echo=48,gray_nondetection=0,
                 physical_intensity_calibrated=False,motion_status=v['status'],
                 historical_replay=False,generated_at=self.clock(),
                 window_start=start,window_end=start+3600,
                 expires_at=min(times[-1]+1200,start+300),
                 source_age_seconds=self.clock()-times[-1])
        temp=d/'manifest.tmp';temp.write_text(json.dumps(doc));temp.replace(d/'manifest.json')
        self.cleanup()
        return doc
    def cleanup(self):
        # Keep only published frames. Retire tiles and public-source frames after
        # two hours; static coverage remains cached for its existing daily TTL.
        cutoff=self.clock()-7200
        for d in self.root.iterdir():
            if not d.is_dir():continue
            path=d/'manifest.json'
            m=json.loads(path.read_text()) if path.exists() else {}
            keep={f['file'] for f in m.get('frames',[])} if m.get('generated_at',0)>=cutoff else set()
            for f in d.glob('*.ktx'):
                if f.name not in keep:f.unlink(missing_ok=True)
            if not keep:path.unlink(missing_ok=True)
        source_cache=getattr(self.source,'cache',None)
        if source_cache:
            import shutil
            for d in (source_cache/'frames').glob('*'):
                if d.is_dir() and d.stat().st_mtime<cutoff:shutil.rmtree(d)
    def _work(self):
        while True:
            tile=self.jobs.get()
            with self.lock:self.active=tile
            try:
                # Quota pressure pauses this worker rather than losing a tile.
                # PublicSource still enforces the shared 90 requests/min cap.
                for attempt in range(4):
                    try:self.build(tile);break
                    except RateLimitPending:
                        if attempt==3:raise
                        threading.Event().wait(61)
                with self.lock:self.failed.pop(tile,None)
            except Exception as e:
                with self.lock:self.failed[tile]=dict(tile=list(tile),time=self.clock(),error=type(e).__name__)
            finally:
                with self.lock:
                    self.pending.discard(tile);self.active=None
                self.jobs.task_done()
    def prepare(self,tile):
        with self.lock:
            if tile in self.pending or self.clock()-self.last_attempt.get(tile,-math.inf)<60:return False
            try:self.jobs.put_nowait(tile)
            except queue.Full:return False
            self.pending.add(tile);self.last_attempt[tile]=self.clock()
            if self.worker is None:
                self.worker=threading.Thread(target=self._work,daemon=True);self.worker.start()
        return True
    def lookup(self,tile,requested):
        path=self.directory(tile)/'manifest.json'
        if not path.exists():return None
        m=json.loads(path.read_text());origin=m['origin'];now=self.clock()
        if m.get('historical_replay')or m.get('mode')!='echo_occurrence_only' or not m.get('frames'):return None
        if not -60<=now-origin<=1200 or now>=m.get('expires_at',math.inf):return None
        if not origin<=requested or not m['frames'][0]['valid_time']-300<=requested<=m['frames'][-1]['valid_time']:return None
        # Source & Apple phase can differ by5 minutes. Explicit temporal nearest,
        # max5-minute offset; advertise source valid time separately.
        f=min(m['frames'],key=lambda f:abs(f['valid_time']-requested))
        if abs(f['valid_time']-requested)>300:return None
        b=(path.parent/f['file']).read_bytes()
        if len(b)!=65604 or b[:12]!=b'\xabKTX 11\xbb\r\n\x1a\n':return None
        return b,{'Content-Type':'image/ktx','Cache-Control':'no-store',
                  'X-WK3-Nowcast':MARKER,'X-WK3-Origin':str(origin),
                  'X-WK3-Valid-Time':str(requested),'X-WK3-Source-Valid-Time':str(f['valid_time']),
                  'X-WK3-Lead-Minutes':str((f['valid_time']-origin)/60),
                  'X-WK3-Forecast-Start':str(m['frames'][0]['valid_time']),
                  'X-WK3-Forecast-End':str(m['frames'][-1]['valid_time']),
                  'X-WK3-Max-Lead-Minutes':str((m['frames'][-1]['valid_time']-origin)/60),
                  'X-WK3-Source-Age':str(round(now-origin)),
                  'X-WK3-Generated-At':str(m.get('generated_at','')),
                  'X-WK3-Expires-At':str(m.get('expires_at','')),
                  'X-WK3-Display':'echo-occurrence-only-not-intensity',
                  'X-WK3-Attribution':'Weather data by RainViewer; extrapolation by this project'}


def parse_request(path):
    u=urlsplit(path)
    if u.path!='/v1/radar':raise ValueError('Unknown route')
    q=parse_qs(u.query,keep_blank_values=True)
    if set(q)!=set(['z','x','y','scale','frameTime'])or any(len(v)!=1 for v in q.values()):raise ValueError('Invalid query')
    tile=tuple(int(q[k][0])for k in ['z','x','y']);validate(*tile)
    if q['scale']!=['2']:raise ValueError('Unsupported scale')
    t=datetime.fromisoformat(q['frameTime'][0].replace('Z','+00:00'))
    if t.tzinfo is None:raise ValueError('UTC offset required')
    requested=t.timestamp()
    if requested%300:raise ValueError('Five-minute frame required')
    return tile,int(requested)

def handler(store,allow_prepare=True,token=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if token is not None and not secrets.compare_digest(
                    self.headers.get('Authorization','').encode(),('Bearer '+token).encode()):
                self.send_response(401);self.send_header('WWW-Authenticate','Bearer');self.send_header('Cache-Control','no-store');self.send_header('Content-Length','0');self.end_headers();return
            if urlsplit(self.path).path=='/status':
                with store.lock:doc={'mode':'experimental echo occurrence; no intensity calibration','attribution':'Weather data by RainViewer https://www.rainviewer.com/','pending':list(store.pending),'active':store.active,'queued':store.jobs.qsize(),'queue_capacity':16,'failures':list(store.failed.values()),'started_at':store.started_at,'radar_requests':store.requests,'radar_responses':dict(store.responses),'recent_http':list(store.recent_http)}
                b=json.dumps(doc).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
            stage='parse'
            store.http_event('received')
            try:
                with store.lock:store.requests+=1
                tile,t=parse_request(self.path)
                store.http_event('parsed',tile=list(tile),valid_time=t)
                stage='lookup'
                now=store.clock()
                if not now-1200<=t<=now+3600:
                    store.record(204);self.send_response(204);self.send_header('Content-Length','0');self.end_headers();return
                r=store.lookup(tile,t)
                if r is None:
                    if allow_prepare:store.prepare(tile)
                    store.record(202);self.send_response(202);self.send_header('Retry-After','30');self.send_header('Content-Length','0');self.end_headers();return
                b,h=r
                if allow_prepare and now-int(h['X-WK3-Origin'])>=600:store.prepare(tile)
                store.record(200);self.send_response(200)
                for k,v in h.items():self.send_header(k,v)
                self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
            except (ValueError,FileNotFoundError,KeyError,TypeError) as error:
                store.http_event('rejected',at=stage,error=type(error).__name__)
                store.record(400);self.send_response(400);self.send_header('Content-Length','0');self.end_headers()
        def log_message(self,*args):pass # never log request query/auth metadata
    return Handler

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cache',type=Path,default=Path(__file__).parent/'native-cache');p.add_argument('--port',type=int,default=8768);p.add_argument('--prepare',type=int,nargs=3)
    p.add_argument('--host',default='127.0.0.1',help='Bind address (default: loopback)')
    p.add_argument('--source-cache',type=Path,help='Writable public-source cache directory')
    p.add_argument('--token-env',help='Require Bearer token from this environment variable; never a literal token')
    a=p.parse_args()
    token=None
    if a.token_env:
        token=os.environ.get(a.token_env)
        if not token or any(c.isspace() for c in token):p.error('Token environment variable is missing, empty or contains whitespace')
    store=Store(a.cache,source=PublicSource(a.source_cache) if a.source_cache else None)
    if a.prepare:
        tile=tuple(a.prepare);validate(*tile);print(json.dumps(store.build(tile)),flush=True)
    else:
        print(f'Radar bridge http://{a.host}:{a.port}/status ; experimental echo-only, no intensity',flush=True)
        ThreadingHTTPServer((a.host,a.port),handler(store,token=token)).serve_forever()
