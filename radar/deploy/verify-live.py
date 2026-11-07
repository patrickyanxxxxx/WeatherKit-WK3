"""Run as root on deployed VPS; token is read once, never printed or saved in results."""
import datetime,json,os,pwd,random,sys,time,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,'/opt/radar-demo')
import numpy as np
from global_source import PublicSource,xyz

from urllib.parse import urlsplit
BASE=os.environ.get('VPS_ORIGIN','').rstrip('/')
u=urlsplit(BASE)
if u.scheme!='https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
    raise SystemExit('Set VPS_ORIGIN to your own VPS HTTPS origin before running this script')
token=Path('/etc/radar-demo/token.env').read_text().strip().split('=',1)[1]
account=pwd.getpwnam('radar-demo');os.setgid(account.pw_gid);os.setuid(account.pw_uid)
result_path=Path('/var/lib/radar-demo/deployment-result.json')
result={'domain':u.hostname,'url':BASE,'service':'radar-demo.service','token_env_file':'/etc/radar-demo/token.env','token_variable':'VPS_TOKEN','public_bearer_required':True,'sample':{},'real_tile':{'attempts':[]},'limitations':['Echo occurrence only, no intensity calibration; chain verification does not establish one-hour forecast skill.']}
def save():
    tmp=result_path.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2));tmp.replace(result_path)
def request(path,auth=True):
    req=urllib.request.Request(BASE+path,headers={'Authorization':'Bearer '+token} if auth else {})
    try:r=urllib.request.urlopen(req,timeout=30)
    except urllib.error.HTTPError as exc:r=exc
    with r:
        body=r.read();headers={k:v for k,v in r.headers.items() if k.lower() in ('content-type','content-length','cache-control','retry-after','www-authenticate') or k.lower().startswith('x-wk3-')}
        return r.code,headers,body

code,headers,body=request('/status');result['status']={'authenticated':code,'body':json.loads(body)}
result['status']['unauthenticated']=request('/status',False)[0]
s=PublicSource('/var/lib/radar-demo/source');m=s.metadata();frame=m['radar']['past'][-1]
cities=[('哈尔滨',45.75,126.65),('北京',39.90,116.40),('济南',36.67,117.00),('南京',32.06,118.79),('上海',31.23,121.47),('武汉',30.59,114.30),('成都',30.67,104.07),('重庆',29.56,106.55),('西安',34.34,108.94),('广州',23.13,113.26),('昆明',25.04,102.71),('南宁',22.82,108.37),('兰州',36.06,103.83),('乌鲁木齐',43.82,87.62),('沈阳',41.80,123.43),('长沙',28.23,112.94),('福州',26.08,119.30),('郑州',34.75,113.62)]
chosen=random.SystemRandom().sample(cities,8)
result['sample']={'method':'Uniform random without replacement, 8 of 18 declared mainland cities; every draw retained. First sampled eligible echo tile used for transport validation only.','population':[c[0] for c in cities],'sampled_at':int(time.time()),'source_time':frame['time'],'results':[{'city':name,'tile':list(xyz(lat,lon,7))} for name,lat,lon in chosen]}
save()
for row in result['sample']['results']:
    try:
        image=np.asarray(s.tile(m,frame,*row['tile']));coverage=np.asarray(s.coverage(*row['tile']))[:,:,3]==0
        row.update(visible_echo_pixels=int((image[:,:,3]>0).sum()),static_coverage_pixels=int(coverage.sum()),visible_echo_in_static_coverage=int(((image[:,:,3]>0)&coverage).sum()),pixels=int(coverage.size))
    except Exception as exc:row['error']=type(exc).__name__
    save();print(json.dumps(row,ensure_ascii=False),flush=True)
from urllib.parse import urlencode
for row in result['sample']['results']:
    if row.get('visible_echo_in_static_coverage',0)<64:continue
    tile=row['tile'];target=frame['time']+600
    query=urlencode(dict(z=tile[0],x=tile[1],y=tile[2],scale=2,frameTime=datetime.datetime.fromtimestamp(target,datetime.timezone.utc).isoformat()))
    path='/v1/radar?'+query
    attempt={'city':row['city'],'tile':tile,'requested_time':target,'responses':[]}
    result['real_tile']['attempts'].append(attempt)
    for check in range(15):
        code,headers,body=request(path)
        response={'at':int(time.time()),'status':code,'headers':headers,'bytes':len(body)}
        if code==200:response['ktx_valid']=len(body)==65604 and body[:12]==b'\xabKTX 11\xbb\r\n\x1a\n'
        attempt['responses'].append(response);save();print(json.dumps(response),flush=True)
        if code==200:
            result['real_tile']['success']=bool(response['ktx_valid']);save();break
        if code!=202:break
        time.sleep(15)
    if result['real_tile'].get('success'):break
    status=json.loads(request('/status')[2]);attempt['end_status']=status;save()
    # No bulk retries: stop if a build is still pending.
    if status['pending']:break
result['completed_at']=int(time.time());save()
print('verification complete',flush=True)
