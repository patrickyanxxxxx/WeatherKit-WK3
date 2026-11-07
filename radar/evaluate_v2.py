"""Fixed-centre hindcast: new targets only, cached public inputs, no Apple integration."""
import argparse,json,time
from pathlib import Path
import numpy as np
from PIL import Image
from global_source import PublicSource
from backtest import motion,prediction as predict_v1,utc
from mosaic import load_mosaic
from motion_v2 import estimate,predict
ROOT=Path(__file__).resolve().parent

class CachedOnly(PublicSource):
    def _get(self,url,relative,max_age=None):
        p=self.cache/relative
        if not p.exists():raise FileNotFoundError('Missing cached '+relative)
        return p.read_bytes()

def crop_frame(f,crop):
    a,b,c,d=crop
    return {k:v[a:b,c:d] for k,v in f.items() if k in ['signal','valid','echo','dbz']}

def counts(p,t,domain):
    wet,valid,dbz=p;echo=t['echo'];known=domain&valid;unknown=domain&~valid
    tp=int((known&wet&echo).sum());fp=int((known&wet&~echo).sum());fn=int((known&~wet&echo).sum())
    both=known&np.isfinite(dbz)&np.isfinite(t['dbz'])
    return dict(n=int(domain.sum()),known=int(known.sum()),unknown=int(unknown.sum()),unknown_target_echo=int((unknown&echo).sum()),tp=tp,fp=fp,fn=fn,tn=int((known&~wet&~echo).sum()),dbz_n=int(both.sum()),dbz_abs=float(np.abs(dbz[both]-t['dbz'][both]).sum()))

def summarize(rows):
    out={}
    for lead in [10,20,30,60]:
        selected=[x for x in rows if x['lead']==lead];out[str(lead)]={}
        for method in ['persistence','integer_single_tile','subpixel_local_buffer']:
            rs=[x[method] for x in selected];keys=['n','known','unknown','unknown_target_echo','tp','fp','fn','tn','dbz_n','dbz_abs']
            c={k:sum(r[k] for r in rs) for k in keys};tp,fp,fn=c['tp'],c['fp'],c['fn']
            c.update(origins=len(rs),csi=tp/(tp+fp+fn) if tp+fp+fn else None,unknown_fraction=c['unknown']/c['n'] if c['n'] else None,pod=tp/(tp+fn) if tp+fn else None,far=fp/(tp+fp) if tp+fp else None,conditional_dbz_mae=c['dbz_abs']/c['dbz_n'] if c['dbz_n'] else None,csi_unknown_as_nondetection=tp/(tp+fp+fn+c['unknown_target_echo']) if tp+fp+fn+c['unknown_target_echo'] else None)
            out[str(lead)][method]=c
    return out

def render(frame,valid=None):
    mask=frame['valid'] if valid is None else valid
    out=np.zeros((*mask.shape,4),np.uint8);out[:]=[30,39,57,255];out[~mask]=[113,66,127,255]
    # Echo display only; no rain-rate calibration or proprietary Apple scale.
    v=np.nan_to_num(frame.get('dbz',np.zeros(mask.shape)),nan=0)
    echo=frame['echo']&mask;out[echo]=[35,180,240,255];out[echo&(v>=20)]=[55,110,245,255];out[echo&(v>=35)]=[160,65,230,255];out[echo&(v>=50)]=[245,215,55,255]
    return Image.fromarray(out,'RGBA')

def run_region(source,m,tile,name,out,cutoff):
    times=[x['time'] for x in m['radar']['past']]
    if len(times)<3 or times!=sorted(set(times)) or any(b-a!=600 for a,b in zip(times,times[1:])):raise ValueError('Need >=3 consecutive 10-minute frames')
    frames=[]
    for i,f in enumerate(m['radar']['past']):
        frames.append(load_mosaic(source,m,f,tuple(tile),radius=1,size=256));print(json.dumps({'region':name,'loaded':i+1,'of':len(times)}),flush=True)
    crop=frames[0]['crop'];a,b,c,d=crop;rows=[];diagnostics=[]
    for i in range(2,len(times)):
        targets=[(lead,times.index(times[i]+lead*60)) for lead in [10,20,30,60] if times[i]+lead*60 in times and times[i]+lead*60>cutoff]
        if not targets:continue
        velocity=estimate(frames[i-2:i+1],times[i-2:i+1]);central=crop_frame(frames[i],crop);previous=crop_frame(frames[i-1],crop)
        dx,dy,status=motion(previous,central);diagnostics.append({'origin':times[i],'integer_motion':[dx,dy,status],'v2_status':velocity['status'],'v2':velocity.get('diagnostics',{})})
        print(json.dumps({'region':name,'origin':utc(times[i]),'motion_status':velocity['status']}),flush=True)
        for lead,j in targets:
            truth=crop_frame(frames[j],crop)
            domain=truth['valid'] & central['valid']
            p=(central['echo'],central['valid'],central['dbz'])
            if status=='estimated':v1=predict_v1(central,dx*lead/10,dy*lead/10)
            else:v1=(np.zeros_like(central['echo']),np.zeros_like(central['valid']),np.full_like(central['dbz'],np.nan))
            v2=predict(frames[i],velocity,lead,output_region=crop)
            row=dict(origin=times[i],target=times[j],lead=lead,persistence=counts(p,truth,domain),integer_single_tile=counts(v1,truth,domain),subpixel_local_buffer=counts(v2,truth,domain));rows.append(row)
            if lead==60 and not (out/f'{name}-60-forecast.png').exists():
                for suffix,fr in [('origin',central),('actual',truth),('forecast',dict(echo=v2[0],valid=v2[1],dbz=v2[2]))]:render(fr).save(out/f'{name}-60-{suffix}.png')
                (out/f'{name}-60-example.json').write_text(json.dumps({'origin':times[i],'target':times[j],'tile':tile,'source':'RainViewer','type':'historical held-out hindcast'},indent=2))
    result=dict(name=name,tile=tile,source_times=times,new_target_after=cutoff,aggregate=summarize(rows),pairs=rows,motion=diagnostics,elapsed_semantics='Radar display echo vs nondetection; static mask only, not confirmed rain or live quality')
    (out/f'{name}.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--metadata',type=Path,default=ROOT/'experiment-v2/metadata.json');p.add_argument('--regions',type=Path,default=ROOT/'experiment-v2/regions.json');p.add_argument('--out',type=Path,default=ROOT/'experiment-v2/results');p.add_argument('--network',action='store_true');p.add_argument('--name');p.add_argument('--cutoff',type=int)
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    m=json.loads(args.metadata.read_text());regions=json.loads(args.regions.read_text())
    if isinstance(regions,dict):regions=regions.get('regions',regions)
    if args.name:regions=[x for x in regions if x['name']==args.name]
    cutoff=args.cutoff or max(f['time']for f in json.loads((ROOT/'metadata_initial.json').read_text())['radar']['past'])
    all_results=[]
    for region in regions:
        result=run_region(PublicSource() if args.network else CachedOnly(),m,region['tile'],region['name'],args.out,cutoff);all_results.append({k:result[k]for k in ['name','tile','aggregate']});print(json.dumps(all_results[-1]),flush=True)
    (args.out/'summary.json').write_text(json.dumps(all_results,indent=2))
