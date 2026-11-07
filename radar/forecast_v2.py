"""RainViewer-derived experimental nowcast, not official RainViewer or Apple forecast."""
import argparse,json,time
from pathlib import Path
import numpy as np
from global_source import PublicSource,xyz
from backtest import utc
from mosaic import load_mosaic
from motion_v2 import estimate,predict
from evaluate_v2 import render,crop_frame,CachedOnly
ROOT=Path(__file__).resolve().parent

def build(tile,out,metadata=None,offline=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    source=CachedOnly() if offline else PublicSource()
    meta=metadata or source.metadata();fs=meta['radar']['past'][-3:];times=[f['time']for f in fs]
    if len(times)!=3 or any(b-a!=600 for a,b in zip(times,times[1:])):raise ValueError('Three consecutive recent frames required; missing data is not dry')
    if not offline and not -60 <= time.time()-times[-1] <= 1200:
        raise ValueError('Radar source is stale/future-dated; use recorded metadata with --offline for a historical replay')
    frames=[load_mosaic(source,meta,f,tile,radius=1)for f in fs];crop=frames[-1]['crop']
    velocity=estimate(frames,times);rows=[]
    for lead in range(0,61,10):
        if lead==0:central=crop_frame(frames[-1],crop);e,v,d=central['echo'],central['valid'],central['dbz']
        else:e,v,d=predict(frames[-1],velocity,lead,output_region=crop)
        name=f'frame-{lead:02d}';np.savez_compressed(out/(name+'.npz'),echo=e,valid=v,dbz=d)
        render(dict(echo=e,valid=v,dbz=d)).save(out/(name+'.png'))
        rows.append({'lead_minutes':lead,'valid_time':times[-1]+lead*60,'png':name+'.png','npz':name+'.npz','unknown_fraction':float((~v).mean()),'displayed_echo_pixels':int((e&v).sum()),'kind':'source_observation_display' if lead==0 else 'experimental_extrapolation'})
        print(json.dumps({'exported':lead,'unknown':float((~v).mean())}),flush=True)
    z,x,y=tile
    import math
    lat=lambda yy:math.degrees(math.atan(math.sinh(math.pi*(1-2*yy/2**z))))
    manifest={'attribution':'Weather data by RainViewer — https://www.rainviewer.com/','type':'experimental radar-echo extrapolation','tile':tile,'bounds_wsen':[x/2**z*360-180,lat(y+1),(x+1)/2**z*360-180,lat(y)],'source_frame_generation_times':times,'origin':times[-1],'built_at':time.time(),'source_age_at_build_seconds':time.time()-times[-1],'historical_replay':offline,'motion_status':velocity['status'],'motion':velocity['diagnostics'],'frames':rows,'known_dry':None,'units':'displayed echo and conditional dBZ; not rainfall mm/h','coverage':'Static declared coverage only; missing is not zero rain','calibrated_for_apple':False}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False))
    (out/'metadata-source.json').write_text(json.dumps(meta,indent=2))
    viewer=(ROOT/'viewer-template.html').read_text();viewer=viewer.replace('/*MANIFEST*/',json.dumps(manifest,ensure_ascii=False).replace('</','<\\/'))
    (out/'index.html').write_text(viewer)
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--lat',type=float);p.add_argument('--lon',type=float);p.add_argument('--xyz',type=int,nargs=3);p.add_argument('--zoom',type=int,default=6);p.add_argument('--out',type=Path,required=True);p.add_argument('--metadata',type=Path);p.add_argument('--offline',action='store_true')
    a=p.parse_args()
    if not a.xyz and(a.lat is None or a.lon is None):p.error('Supply --xyz or latitude/longitude')
    tile=tuple(a.xyz)if a.xyz else xyz(a.lat,a.lon,a.zoom)
    m=json.loads(a.metadata.read_text())if a.metadata else None
    if a.offline and m is None:p.error('--offline requires a recorded --metadata snapshot')
    r=build(tile,a.out,m,a.offline);print(json.dumps({'out':str(a.out),'origin':utc(r['origin']),'motion_status':r['motion_status'],'frames':len(r['frames'])}))
