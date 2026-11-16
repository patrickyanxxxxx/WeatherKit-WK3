"""Personal experimental RainViewer observation + Rainbow forecast gateway.
User-selected display: clip((dBZ-5)/50*255). Not Apple physical calibration.
"""
import io,json,math,os,threading,time,secrets,struct
from collections import OrderedDict
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit,parse_qs
from urllib.request import Request,urlopen
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image
from global_source import PublicSource,RateLimitPending
from backtest import palette
from quality.standard_astc import encode_gray256
from transition import transition_dbz,observation_weight,ATTRIBUTION as BLEND_ATTRIBUTION
from nowcast.engine import Engine as MotionEngine
ROOT=Path(__file__).resolve().parent
MARKER='EXPERIMENTAL-RADAR-LINEAR'
class Unavailable(Exception):
    def __init__(self,reason):self.reason=reason

def linear_ktx(dbz,valid):
    if dbz.shape!=(256,256) or valid.shape!=dbz.shape or not valid.all() or not np.isfinite(dbz).all():raise Unavailable('unknown-pixels')
    gray=np.clip((dbz-5)/50*255,0,255)
    pixels=np.rint(gray).astype('uint8')
    return encode_gray256(pixels)

class Hybrid:
    def __init__(self,cache,clock=time.time):
        self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)
        self.rv=PublicSource(self.cache/'rainviewer');self.clock=clock
        self.allowed={tuple(t)for t in json.loads((ROOT/'tiles.json').read_text())}
        self.colors=palette(ROOT/'palette.csv');self.metadata_locks={'rv':threading.Lock(),'rb':threading.Lock()};self.lock=threading.Lock()
        self.rv_meta=None;self.rb_snapshot=None;self.rv_checked=0;self.rb_checked=0
        self.pool=ThreadPoolExecutor(max_workers=12);self.pending=set();self.failures={};self.recent=[];self.requests=0
        self.fetch_locks={};self.semaphore=threading.Semaphore(8);self.futures={};self.source_pool=ThreadPoolExecutor(max_workers=16);self.prefetch_times={};self.decoded=OrderedDict();self.motion=MotionEngine(clock=clock)
    def event(self,**fields):
        with self.lock:self.recent=(self.recent+[dict(time=int(self.clock()),**fields)])[-30:]
    def raw_fetch(self,path):
        # Credentials only in header, never in URL/diagnostics.
        with self.semaphore:
            request=Request('https://api.rainbow.ai'+path,headers={'Ocp-Apim-Subscription-Key':os.environ['RAINBOW_API_KEY'],'User-Agent':'WeatherKit-Personal-Prototype/0.2'})
            with urlopen(request,timeout=12)as response:return response.read()
    def metadata(self,which):
        with self.metadata_locks[which]:
            now=self.clock()
            if which=='rv':
                if self.rv_meta is None or now-self.rv_checked>=90:
                    self.rv_meta=self.rv.metadata();self.rv_checked=now
                if not 0<=now-self.rv_meta['radar']['past'][-1]['time']<=1800:raise Unavailable('stale-rainviewer')
                return self.rv_meta
            if self.rb_snapshot is None or now-self.rb_checked>=90:
                raw=self.raw_fetch('/tiles/v1/snapshot?layer=precip-global');self.rb_snapshot=json.loads(raw)['snapshot'];self.rb_checked=now
            if type(self.rb_snapshot)is not int or self.rb_snapshot%600 or not 0<=now-self.rb_snapshot<=1800:raise Unavailable('stale-rainbow')
            return self.rb_snapshot
    def rb_tile(self,snapshot,lead,tile):
        z,x,y=tile;p=self.cache/'rainbow'/str(snapshot)/str(lead)/f'{z}-{x}-{y}.png';p.parent.mkdir(parents=True,exist_ok=True)
        with self.lock:lock=self.fetch_locks.setdefault(str(p),threading.Lock())
        with lock:
            if p.exists():raw=p.read_bytes()
            else:
                raw=self.raw_fetch(f'/tiles/v1/precip-global/{snapshot}/{lead}/{z}/{x}/{y}?color=dbz_u8&coverage=1')
                im=Image.open(io.BytesIO(raw));im.load()
                if im.format!='PNG' or im.size!=(256,256):raise Unavailable('bad-rainbow-image')
                temp=p.with_suffix('.tmp');temp.write_bytes(raw);temp.replace(p)
        a=np.asarray(Image.open(io.BytesIO(raw)).convert('RGBA'))
        return (a[:,:,0].astype('int16')&127).astype(float)-32,a[:,:,3]>0
    def rv_frame(self,m,frame,tile):
        key=(tuple(tile),frame['path']);now=self.clock()
        with self.lock:
            cached=self.decoded.get(key)
            if cached and now-cached[0]<120:
                self.decoded.move_to_end(key);return cached[1],cached[2]
            guard=self.fetch_locks.setdefault(('decoded',key),threading.Lock())
        with guard:
            with self.lock:
                cached=self.decoded.get(key)
                if cached and self.clock()-cached[0]<120:return cached[1],cached[2]
            d,v=self._rv_frame(m,frame,tile)
            d.flags.writeable=False;v.flags.writeable=False
            with self.lock:
                self.decoded[key]=(self.clock(),d,v);self.decoded.move_to_end(key)
                while len(self.decoded)>32:self.decoded.popitem(last=False)
            return d,v
    def _rv_frame(self,m,frame,tile):
        jobs=[self.source_pool.submit(self.rv.tile,m,frame,*tile),self.source_pool.submit(self.rv.coverage,*tile)]
        im,coverage=(future.result(timeout=15)for future in jobs);a=np.asarray(im);covered=np.asarray(coverage)[:,:,3]==0
        # RV alpha-zero within declared coverage is nondetection, not missing.
        colors,inv=np.unique(np.ascontiguousarray(a).view('<u4').reshape(-1),return_inverse=True)
        lo=np.full(len(colors),np.nan);hi=lo.copy()
        for i,c in enumerate(colors):
            key=tuple(int(v)for v in int(c).to_bytes(4,'little'))
            if key in self.colors:lo[i],hi[i]=min(self.colors[key]),max(self.colors[key])
        low=lo[inv].reshape(256,256);high=hi[inv].reshape(256,256);visible=a[:,:,3]>0
        normal=visible&np.isfinite(low)&(high<75);valid=covered&(~visible|normal)
        # Where RGBA maps to a range, use its midpoint for prototype display.
        d=np.where(normal,(low+high)/2,-32)
        return d,valid
    def choose(self,tile,requested):
        now=self.clock();m=None
        # Parallelize only a likely forecast window; historical frames need no RB call.
        rb_job=self.source_pool.submit(self.metadata,'rb') if requested>now-600 else None
        try:m=self.metadata('rv')
        except Exception:pass
        latest=m['radar']['past'][-1]['time'] if m else None
        if m and requested<=latest:
            frames=[f for f in m['radar']['past']if f['time']<=requested]
            if not frames or requested-frames[-1]['time']>=600:raise Unavailable('missing-history')
            f=frames[-1]
            return dict(source='RainViewer',kind='observation',origin=f['time'],source_time=f['time'],latest=latest,frame=f,metadata=m)
        if requested<now-1800:raise Unavailable('missing-history')
        try:
            snapshot=rb_job.result(timeout=13) if rb_job else self.metadata('rb');offset=math.floor((requested-snapshot)/600)*600;valid=snapshot+offset
            if not snapshot<=valid<=snapshot+14400:raise Unavailable('rainbow-window')
            selected=dict(source='Rainbow',kind='forecast',origin=snapshot,source_time=valid,latest=snapshot)
            if m and latest<=valid and observation_weight(latest,valid)>0:
                frames=[f for f in m['radar']['past']if f['time']<=latest]
                if frames:
                    selected.update(anchor=latest,anchor_frame=frames[-1],anchor_metadata=m)
            return selected
        except Exception:
            if m and requested>=latest and now-latest<=1800:
                return dict(source='RainViewer',kind='persistence',origin=latest,source_time=latest,latest=latest,frame=m['radar']['past'][-1],metadata=m)
            raise Unavailable('forecast-unavailable')
    def choose_motion(self,tile,requested):
        try:m=self.metadata('rv')
        except Exception:return self.choose(tile,requested)
        latest=m['radar']['past'][-1]['time']
        if requested<=latest:return self.choose(tile,requested)
        valid=requested//600*600
        if latest<=valid<=latest+4800:
            return dict(source='RainViewer',kind='forecast',origin=latest,source_time=valid,latest=latest,motion_metadata=m,model='advection-candidate')
        return self.choose(tile,requested)
    def key(self,tile,selection):return '-'.join(map(str,(*tile,selection['source'],selection['kind'],selection['origin'],selection['source_time'],selection.get('anchor',0),selection.get('model','smooth-astc-v3'))))
    def build(self,tile,selection,key):
        start=time.monotonic()
        try:
            kind=selection['kind']
            motion_diag=None
            if 'motion_metadata'in selection:
                d,v,motion_diag=self.motion.predict(selection['motion_metadata'],tile,selection['source_time'],self.rv_frame)
                if motion_diag['status']!='available' or not v.all():
                    snapshot=self.metadata('rb');valid=selection['source_time'];lead=valid-snapshot
                    if not 0<=lead<=14400:raise Unavailable('motion-and-model-unavailable')
                    d,v=self.rb_tile(snapshot,lead,tile)
                    selection=dict(source='Rainbow',kind='forecast',origin=snapshot,source_time=valid,latest=snapshot)
                    motion_diag=None
                elif motion_diag['model']=='persistence':
                    selection=dict(source='RainViewer',kind='persistence',origin=selection['origin'],source_time=selection['origin'],latest=selection['latest'])
            elif selection['source']=='RainViewer':
                d,v=self.rv_frame(selection['metadata'],selection['frame'],tile)
                if not v.all():
                    # Whole-tile fallback to a historical global snapshot, not pixel mixing.
                    snap=selection['source_time'];d,v=self.rb_tile(snap,0,tile)
                    selection=dict(source='Rainbow',kind='observation',origin=snap,source_time=snap,latest=self.metadata('rb'))
            else:
                try:d,v=self.rb_tile(selection['origin'],selection['source_time']-selection['origin'],tile)
                except Exception:
                    m=self.metadata('rv');latest=m['radar']['past'][-1]['time'];d,v=self.rv_frame(m,m['radar']['past'][-1],tile)
                    selection=dict(source='RainViewer',kind='persistence',origin=latest,source_time=latest,latest=latest)
            if selection['kind']=='forecast' and 'anchor'in selection:
                try:
                    observed,known=self.rv_frame(selection['anchor_metadata'],selection['anchor_frame'],tile)
                    if known.all() and v.all():
                        d,weight=transition_dbz(observed,d,known,v,selection['anchor'],selection['source_time'])
                        selection=dict(selection,source='RainViewer+Rainbow',blend_weight=weight)
                except Exception:pass
            body=linear_ktx(d,v);path=self.cache/'ktx'/key;path.parent.mkdir(exist_ok=True)
            temp=path.with_suffix('.tmp');temp.write_bytes(body);temp.replace(path)
            doc={k:selection[k]for k in ['source','kind','origin','source_time','latest']}
            if motion_diag is not None:doc.update(motion=motion_diag)
            if selection['source']=='RainViewer+Rainbow':doc.update(anchor=selection['anchor'],blend_weight=selection['blend_weight'])
            temp=path.with_suffix('.json.tmp');temp.write_text(json.dumps(doc));temp.replace(path.with_suffix('.json'))
            with self.lock:self.failures.pop(key,None)
            self.event(tile=list(tile),kind=doc['kind'],source=doc['source'],seconds=round(time.monotonic()-start,3),bytes=len(body))
        except Exception as error:
            reason=error.reason if isinstance(error,Unavailable)else 'source-unavailable'
            with self.lock:self.failures[key]=(self.clock(),reason)
            self.event(tile=list(tile),reason=reason,error_type=type(error).__name__)
        finally:
            with self.lock:self.pending.discard(key)
    def prefetch_next(self,tile,doc):
        if doc['kind']!='forecast' or doc['source'] not in ['Rainbow','RainViewer+Rainbow']:return
        if doc['source_time']+600>doc['origin']+14400:return
        now=self.clock();bucket=(tile,doc['origin'],doc['source_time'])
        with self.lock:
            self.prefetch_times={k:v for k,v in self.prefetch_times.items()if now-v<1800}
            if bucket in self.prefetch_times:return
            self.prefetch_times[bucket]=now
        selection={'source':'Rainbow','kind':'forecast','origin':doc['origin'],'source_time':doc['source_time']+600,'latest':doc['latest']}
        if doc['source']=='RainViewer+Rainbow' and observation_weight(doc['anchor'],selection['source_time'])>0:
            try:
                m=self.rv_meta
                if not m:return
                frame=next(f for f in m['radar']['past']if f['time']==doc['anchor'])
                selection.update(anchor=doc['anchor'],anchor_frame=frame,anchor_metadata=m)
            except Exception:return
        key=self.key(tile,selection);path=self.cache/'ktx'/key
        if path.exists()and path.with_suffix('.json').exists():return
        with self.lock:
            if key in self.pending or len(self.pending)>=20:return
            self.pending.add(key);self.futures[key]=self.pool.submit(self.build,tile,selection,key)
    def response(self,tile,requested,transition=False,motion=False):
        started=time.monotonic();selection=self.choose_motion(tile,requested) if motion else self.choose(tile,requested);selected_at=time.monotonic()
        if not transition:
            selection={k:v for k,v in selection.items()if not k.startswith('anchor')}
        key=self.key(tile,selection);p=self.cache/'ktx'/key
        cache_hit=p.exists() and p.with_suffix('.json').exists()
        if not cache_hit:
            with self.lock:
                failure=self.failures.get(key)
                if failure and self.clock()-failure[0]<60:return 503,{'X-WK3-Reason':failure[1]},b''
                if key not in self.pending:
                    if len(self.pending)>=32:return 503,{'X-WK3-Reason':'queue-full'},b''
                    self.pending.add(key);self.futures[key]=self.pool.submit(self.build,tile,selection,key)
            if not getattr(self,'http_wait',False):return 202,{'Retry-After':'2','X-WK3-State':'computing'},b''
            with self.lock:future=self.futures.get(key)
            try:future.result(timeout=2.5)
            except Exception:pass
            if not p.exists() or not p.with_suffix('.json').exists():return 202,{'Retry-After':'2','X-WK3-State':'computing'},b''
        doc=json.loads(p.with_suffix('.json').read_text());kind=doc['kind'];now=self.clock()
        if kind=='observation':
            doc['latest']=self.metadata('rv')['radar']['past'][-1]['time'] if doc['source']=='RainViewer'else self.metadata('rb')
        if not 0<=now-doc['latest']<=1800:return 503,{'X-WK3-Reason':'stale-source'},b''
        if kind=='persistence' and (requested<doc['origin'] or now-doc['origin']>1800):
            p.unlink(missing_ok=True);p.with_suffix('.json').unlink(missing_ok=True)
            return 503,{'X-WK3-Reason':'stale-frozen'},b''
        self.prefetch_next(tile,doc)
        source=doc['source'];display='prototype-display-transition' if source=='RainViewer+Rainbow' else ('prototype-frozen-linear' if kind=='persistence' else 'prototype-linear-uncalibrated')
        headers={'Content-Type':'image/ktx','X-WK3-Nowcast':MARKER,'X-WK3-Frame-Kind':kind,'X-WK3-Origin':str(doc['origin']),'X-WK3-Source-Valid-Time':str(doc['source_time']),'X-WK3-Latest-Source':str(doc['latest']),'X-WK3-Valid-Time':str(requested),'X-WK3-Source':source,'X-WK3-Display':display,'X-WK3-Attribution':BLEND_ATTRIBUTION if source=='RainViewer+Rainbow'else ('Powered by Rainbow.ai'if source=='Rainbow'else 'Weather data by RainViewer')}
        if source=='RainViewer+Rainbow':headers.update({'X-WK3-Blend-Anchor':str(doc['anchor']),'X-WK3-Observation-Weight':f"{doc['blend_weight']:.6f}"})
        if 'motion'in doc:
            diagnostic=doc['motion'];model=diagnostic['model']
            headers.update({'X-WK3-Model':model,'X-WK3-Display':'prototype-advection-linear' if model=='advection' else 'prototype-frozen-linear'})
            if model=='advection':
                headers.update({'X-WK3-Velocity-X':f"{diagnostic['dx']:.6f}",'X-WK3-Velocity-Y':f"{diagnostic['dy']:.6f}",'X-WK3-Attribution':'Weather data by RainViewer; advection by this project'})
        body=p.read_bytes();finished=time.monotonic()
        headers.update({'X-WK3-Cache':'hit' if cache_hit else 'built','Server-Timing':f"select;dur={(selected_at-started)*1000:.1f}, tile;dur={(finished-selected_at)*1000:.1f}"})
        return 200,headers,body

def parse(path,now,allowed):
    u=urlsplit(path);q=parse_qs(u.query,keep_blank_values=True)
    if u.path not in ['/v2/radar','/v3/radar','/v4/radar'] or set(q)!={'z','x','y','scale','frameTime'} or any(len(v)!=1 for v in q.values()):raise ValueError()
    if q['z']!=['7'] or q['scale']!=['2']:raise ValueError()
    x,y=q['x'][0],q['y'][0]
    if not x.isdecimal() or not y.isdecimal() or str(int(x))!=x or str(int(y))!=y:raise ValueError()
    tile=(7,int(x),int(y))
    if not all(0<=n<128 for n in tile[1:]):raise ValueError()
    text=q['frameTime'][0];dt=datetime.fromisoformat(text.replace('Z','+00:00'));t=int(dt.timestamp())
    if not text.endswith('Z') or dt.utcoffset().total_seconds()!=0 or t%300 or dt.microsecond or not now-7200<=t<=now+3600:raise ValueError()
    if tile not in allowed:raise Unavailable('outside-mainland-test')
    return tile,t

def handler(store,token):
 class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):self.send_response(401);self.end_headers();return
        try:
            if self.path=='/v2/status':
                with store.lock:doc={'service':'radar-rainbow-test','pending':len(store.pending),'recent':store.recent,'requests':store.requests,'mode':'prototype-linear-uncalibrated','scope_tiles':len(store.allowed)}
                status,headers,body=200,{'Content-Type':'application/json'},json.dumps(doc).encode()
            else:
                tile,t=parse(self.path,store.clock(),store.allowed)
                with store.lock:store.requests+=1
                status,headers,body=store.response(tile,t,transition=urlsplit(self.path).path=='/v3/radar',motion=urlsplit(self.path).path=='/v4/radar')
        except Unavailable as error:status,headers,body=422,{'X-WK3-Reason':error.reason},b''
        except ValueError:status,headers,body=400,{},b''
        except Exception:status,headers,body=503,{'X-WK3-Reason':'source-unavailable'},b''
        self.send_response(status);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
        for k,v in headers.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(body)
 return Handler
if __name__=='__main__':
    store=Hybrid(os.environ.get('RADAR_CACHE',str(ROOT/'cache')));store.http_wait=True
    ThreadingHTTPServer(('127.0.0.1',8769),handler(store,os.environ['VPS_TOKEN'])).serve_forever()
