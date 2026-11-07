"""Bounded global radar mosaics; preserve unknown polar/missing data. RainViewer attribution required."""
import argparse,json,sys
from pathlib import Path
import numpy as np
from PIL import Image
from global_source import PublicSource,RateLimitPending,xyz,validate
from backtest import decode,palette
ROOT=Path(__file__).resolve().parent

def mosaic_plan(tile,radius=1,size=256):
    z,x,y=map(int,tile);validate(z,x,y,size)
    if not 0<=radius<=2:raise ValueError('radius must be0..2')
    width=(2*radius+1)*size
    cells=[]
    for dy in range(-radius,radius+1):
        for dx in range(-radius,radius+1):
            cells.append({'tile':(z,(x+dx)%(2**z),y+dy) if 0<=y+dy<2**z else None,'offset':((dy+radius)*size,(dx+radius)*size)})
    return dict(shape=(width,width),cells=cells,crop=(radius*size,(radius+1)*size,radius*size,(radius+1)*size))

def load_mosaic(source,meta,frame,tile,radius=1,size=256):
    plan=mosaic_plan(tile,radius,size);shape=plan['shape'];out={k:np.zeros(shape,dtype=bool if k in ['valid','echo'] else float)for k in ['signal','valid','echo','dbz']};out['dbz'][:]=np.nan
    lut=palette(ROOT/'palette.csv');missing=[]
    for cell in plan['cells']:
        if cell['tile'] is None:continue
        t=cell['tile'];y,x=cell['offset'];cover=np.asarray(source.coverage(*t,size=size))[:,:,3]==0
        f=decode(source.tile(meta,frame,*t,size=size),cover,lut)
        for k in ['signal','valid','echo','dbz']:out[k][y:y+size,x:x+size]=f[k]
    out.update(crop=plan['crop'],diagnostics={'tile':list(tile),'radius':radius,'size':size,'frame_generation_time':frame['time'],'static_coverage_not_live_quality':True})
    return out

def acquire(meta,regions,source):
    # Rerunnable acquisition; existing cache is reused, failed images never made dry.
    done=0
    for region in regions:
        tiles=list(dict.fromkeys(c['tile']for c in mosaic_plan(region['tile'])['cells']if c['tile']is not None))
        for tile in tiles:
            source.coverage(*tile)
            for frame in meta['radar']['past']:
                source.tile(meta,frame,*tile);done+=1
            print(json.dumps({'region':region['name'],'tile':tile,'completed_frame_tiles':done}),flush=True)
    return done

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--init',action='store_true');p.add_argument('--acquire',action='store_true');p.add_argument('--out',type=Path,default=ROOT/'experiment-v2');a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    source=PublicSource();mp=a.out/'metadata.json';rp=a.out/'regions.json'
    if a.init:
        if mp.exists():raise ValueError('Experiment snapshot already exists; choose another outdir')
        meta=source.metadata();mp.write_text(json.dumps(meta,indent=2));regions=[dict(name='new-york',tile=xyz(40.713,-74.007,6)),dict(name='london',tile=xyz(51.507,-.128,6))];rp.write_text(json.dumps(regions,indent=2));print('saved new snapshot',meta['generated'],flush=True)
    if a.acquire:
        try:acquire(json.loads(mp.read_text()),json.loads(rp.read_text()),source)
        except RateLimitPending as e:print(str(e),flush=True);sys.exit(75)
