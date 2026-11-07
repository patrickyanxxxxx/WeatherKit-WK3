"""Weather data by RainViewer https://www.rainviewer.com/ -- local research only."""
import argparse,csv,json,math
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
from PIL import Image
from global_source import PublicSource,xyz,validate
ROOT=Path(__file__).resolve().parent

def utc(t): return datetime.fromtimestamp(t,timezone.utc).isoformat()

def palette(path):
    with open(path) as f: rows=list(csv.reader(f))[1:129]
    table={}
    for row in rows:
        key=tuple(bytes.fromhex(row[3].lstrip('#')))
        table.setdefault(key,[]).append(int(row[0]))
    return table

def decode(im,covered,table):
    a=np.asarray(im); h,w=a.shape[:2]
    colors,inv=np.unique(a.reshape(-1,4),axis=0,return_inverse=True)
    lo=np.full(len(colors),np.nan); hi=lo.copy()
    for i,c in enumerate(colors):
        values=table.get(tuple(c),[])
        if values and c[3]>0: lo[i],hi[i]=min(values),max(values)
    low,high=lo[inv].reshape(h,w),hi[inv].reshape(h,w)
    visible=a[:,:,3]>0
    normal=visible & np.isfinite(low) & (high<75)
    valid=covered & (~visible | normal)
    echo=normal & covered
    unique=echo & (low==high)
    dbz=np.where(unique,low,np.nan)
    # Reflectivity display index, NOT rain rate; transparent means nondetection only.
    signal=np.where(echo,np.minimum(np.nan_to_num(low),64)+11,0).astype(float)
    return dict(valid=valid,echo=echo,dbz=dbz,signal=signal,stats={
        'pixels':h*w,'static_coverage':int(covered.sum()),'alpha_zero':int((~visible).sum()),
        'visible':int(visible.sum()),'usable_echo':int(echo.sum()),
        'unknown_or_special_visible':int((visible & ~normal).sum()),
        'ambiguous_dbz_echo':int((echo & ~unique).sum()),'known_dry_pixels':None})

def motion(previous,current,max_shift=20,min_echo=128):
    a,b=previous['signal'],current['signal']; valid=previous['valid'] & current['valid']
    count=min(int((a[valid]>0).sum()),int((b[valid]>0).sum()))
    if count<min_echo: return 0.,0.,'insufficient_echo'
    a=np.where(valid,a,0);b=np.where(valid,b,0)
    shape=(a.shape[0]*2,a.shape[1]*2)
    # Zero padding makes linear correlation; no periodic image seams.
    corr=np.fft.irfft2(np.fft.rfft2(b,s=shape)*np.conj(np.fft.rfft2(a,s=shape)),s=shape)
    shifts=np.arange(-max_shift,max_shift+1)
    window=corr[np.ix_(shifts%shape[0],shifts%shape[1])]
    iy,ix=np.unravel_index(np.argmax(window),window.shape)
    dy,dx=int(shifts[iy]),int(shifts[ix])
    if abs(dx)==max_shift or abs(dy)==max_shift: return float(dx),float(dy),'search_boundary'
    return float(dx),float(dy),'estimated'

def advect(field,dx,dy):
    h,w=field.shape; yy,xx=np.indices((h,w),dtype=float);xx-=dx; yy-=dy
    x0=np.floor(xx).astype(int);y0=np.floor(yy).astype(int)
    fx,fy=xx-x0,yy-y0
    out=np.zeros((h,w),float); support=np.zeros((h,w),float)
    for ox,oy,weight in [(0,0,(1-fx)*(1-fy)),(1,0,fx*(1-fy)),(0,1,(1-fx)*fy),(1,1,fx*fy)]:
        x,y=x0+ox,y0+oy; inside=(x>=0)&(x<w)&(y>=0)&(y<h)
        out[inside]+=field[y[inside],x[inside]]*weight[inside]
        support[inside]+=weight[inside]
    return out,support>1-1e-9

def prediction(frame,dx,dy):
    occurrence,inside=advect(frame['echo'].astype(float),dx,dy)
    validity,_=advect(frame['valid'].astype(float),dx,dy)
    unique=np.isfinite(frame['dbz']);good,_=advect(unique.astype(float),dx,dy)
    values,_=advect(np.nan_to_num(frame['dbz']),dx,dy)
    return occurrence>=.5,inside & (validity>1-1e-9),np.where(inside & (good>1-1e-9),values,np.nan)

def evaluate(pred,truth,common):
    wet,valid,dbz=pred;mask=common & valid & truth['valid'];target=truth['echo']
    both=mask & np.isfinite(dbz) & np.isfinite(truth['dbz'])
    return {'n':int(mask.sum()),'tp':int((mask&wet&target).sum()),'fp':int((mask&wet&~target).sum()),
      'fn':int((mask&~wet&target).sum()),'tn':int((mask&~wet&~target).sum()),
      'dbz_n':int(both.sum()),'dbz_absolute_error_sum':float(np.abs(dbz[both]-truth['dbz'][both]).sum())}

def aggregate(rows):
    result={}
    for lead in [10,20,30,60]:
      result[str(lead)]={}
      for method in ['persistence','advection']:
        selected=[r[method] for r in rows if r['lead_minutes']==lead]
        totals={k:sum(r[k] for r in selected) for k in ['n','tp','fp','fn','tn','dbz_n','dbz_absolute_error_sum']}
        tp,fp,fn=totals['tp'],totals['fp'],totals['fn']
        totals.update(pairs=len(selected),csi=tp/(tp+fp+fn) if tp+fp+fn else None,
          pod=tp/(tp+fn) if tp+fn else None,far=fp/(tp+fp) if tp+fp else None,
          conditional_dbz_mae=totals['dbz_absolute_error_sum']/totals['dbz_n'] if totals['dbz_n'] else None)
        result[str(lead)][method]=totals
    return result

def run_region(source,m,tile,name,table,out):
    covered=np.asarray(source.coverage(*tile))[:,:,3]==0
    frames=[decode(source.tile(m,f,*tile),covered,table) for f in m['radar']['past']]
    times=[f['time'] for f in m['radar']['past']]; rows=[];motions=[]
    for i in range(1,len(frames)):
        dx,dy,status=motion(frames[i-1],frames[i]); dt=(times[i]-times[i-1])/60
        motions.append(dict(origin=utc(times[i]),dx=dx,dy=dy,interval_minutes=dt,status=status))
        if status!='estimated': continue
        for lead in [10,20,30,60]:
            if times[i]+lead*60 not in times: continue
            j=times.index(times[i]+lead*60)
            a=prediction(frames[i],dx*lead/dt,dy*lead/dt);p=prediction(frames[i],0,0)
            common=a[1]&p[1]&frames[j]['valid']
            rows.append(dict(origin=utc(times[i]),target=utc(times[j]),lead_minutes=lead,
                advection=evaluate(a,frames[j],common),persistence=evaluate(p,frames[j],common)))
    latest=frames[-1];dx,dy,status=motion(frames[-2],latest)
    if status=='estimated':
        for lead in [10,20,30,60]:
            e,v,d=prediction(latest,dx*lead/((times[-1]-times[-2])/60),dy*lead/((times[-1]-times[-2])/60))
            np.savez_compressed(out/f'{name}-estimate-{lead}min.npz',echo=e,valid=v,dbz=d)
    result={'name':name,'tile':tile,'frame_generation_times':[utc(t) for t in times],
       'frame_stats':[f['stats'] for f in frames],'motion':motions,'aggregate':aggregate(rows),'pairs':rows,
       'latest_forecast_status':status,'valid_skill_test':bool(rows) and any(r['advection']['tp']+r['advection']['fn'] for r in rows),
       'interpretation':'Static coverage is not live quality. Transparent = no displayed echo, not proven dry. dBZ MAE only where both unique echoes; not rain-rate error.'}
    (out/f'{name}.json').write_text(json.dumps(result,indent=2,allow_nan=False));return result

def synthetic():
    y,x=np.indices((96,96));a=np.exp(-((x-35)**2+(y-40)**2)/45)*40
    b,_=advect(a,5,-3)
    def f(s): return dict(signal=s,valid=np.ones(s.shape,bool))
    dx,dy,status=motion(f(a),f(b),min_echo=20)
    assert (dx,dy)==(5.,-3.),(dx,dy,status)
    moved,valid=advect(a,5,-3);assert np.max(np.abs(moved-b))<1e-9
    edge=np.zeros((32,32));edge[:,31]=1;shifted,v=advect(edge,2,0)
    assert shifted.sum()==0 and not v[:,:2].any()
    half,_=advect(np.eye(8),.5,0);assert abs(half[3,3]-.5)<1e-9
    print(json.dumps({'synthetic':'PASS','translation':[dx,dy],'zero_padded_correlation':True,'no_wraparound':True,'bilinear':True}))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lat',type=float);p.add_argument('--lon',type=float);p.add_argument('--xyz',nargs=3,type=int)
    p.add_argument('--zoom',type=int,default=4);p.add_argument('--name',default='region');p.add_argument('--suite',action='store_true')
    p.add_argument('--synthetic',action='store_true');p.add_argument('--metadata',type=Path)
    p.add_argument('--palette',type=Path,default=ROOT/'palette.csv');p.add_argument('--out',type=Path,default=ROOT/'results')
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    if a.synthetic: synthetic();return
    if not a.suite and not a.xyz and (a.lat is None or a.lon is None):p.error('Supply --suite, --xyz Z X Y, or both --lat and --lon')
    source=PublicSource();m=json.loads(a.metadata.read_text()) if a.metadata else source.metadata()
    times=[f['time'] for f in m.get('radar',{}).get('past',[])]
    if len(times)<2 or times!=sorted(set(times)):raise ValueError('At least two strictly time-ordered unique historical frames required')
    (a.out/'metadata.json').write_text(json.dumps(m,indent=2));table=palette(a.palette)
    regions=[('benxi-regional',(4,13,5)),('us',(4,4,6)),('europe',(4,8,5)),('japan',(4,14,6))] if a.suite else [(a.name,tuple(a.xyz) if a.xyz else xyz(a.lat,a.lon,a.zoom))]
    summaries=[]
    for name,tile in regions:
        validate(*tile);r=run_region(source,m,tile,name,table,a.out)
        summary={k:r[k] for k in ['name','tile','valid_skill_test','latest_forecast_status','aggregate']}
        summary['visible_pixels']=[s['visible'] for s in r['frame_stats']]
        summary['static_coverage']=r['frame_stats'][0]['static_coverage'];summaries.append(summary)
        print(json.dumps(summary),flush=True)
    (a.out/'summary.json').write_text(json.dumps(summaries,indent=2))

if __name__=='__main__':main()
