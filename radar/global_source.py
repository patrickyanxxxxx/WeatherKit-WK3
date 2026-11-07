"""Public RainViewer source, arbitrary global XYZ. No API key required.
Weather data by RainViewer: https://www.rainviewer.com/
Coverage declaration is static, not a live per-frame quality guarantee.
"""
import argparse
import fcntl
import io
import json
import math
import os
from pathlib import Path
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent / 'source-cache'
META = 'https://api.rainviewer.com/public/weather-maps.json'

class RateLimitPending(RuntimeError):
    pass

class PublicSource:
    def __init__(self, cache=ROOT):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)

    def _get(self, url, relative, max_age=None):
        p = self.cache / relative
        if p.exists() and (max_age is None or time.time() - p.stat().st_mtime < max_age):
            return p.read_bytes()
        # One quota shared by processes using this cache. Do not busy-wait.
        lock = self.cache / 'requests.lock'
        with lock.open('a+') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            if p.exists() and (max_age is None or time.time()-p.stat().st_mtime < max_age):
                return p.read_bytes()
            ledger = self.cache / 'request-times.json'
            ticks = json.loads(ledger.read_text()) if ledger.exists() else []
            now = time.time()
            ticks = [x for x in ticks if now-x < 60]
            if len(ticks) >= 90:
                raise RateLimitPending(f'Retry after {math.ceil(60-(now-ticks[0]))} seconds; 90/min local cap')
            ticks.append(now)
            ledger.write_text(json.dumps(ticks))
            req = urllib.request.Request(url, headers={'User-Agent': 'RainviewerResearch/0.1 (personal offline evaluation)'})
            with urllib.request.urlopen(req, timeout=25) as response:
                raw = response.read()
            p.parent.mkdir(parents=True, exist_ok=True)
            temp = p.with_name(p.name + f'.{os.getpid()}.tmp')
            temp.write_bytes(raw)
            temp.replace(p)
            return raw

    def metadata(self):
        j = json.loads(self._get(META, 'metadata.json', 120))
        host = urlsplit(j.get('host', ''))
        if host.scheme != 'https' or host.hostname != 'tilecache.rainviewer.com':
            raise ValueError('Unexpected public tile host; inspect metadata before fetching')
        times = [f['time'] for f in j.get('radar', {}).get('past', [])]
        if not times or times != sorted(set(times)):
            raise ValueError('No valid ordered historical frame list')
        return j

    def tile(self, metadata, frame, z, x, y, size=256):
        validate(z, x, y, size)
        if frame not in metadata['radar']['past']:
            raise ValueError('Frame must come from this captured metadata, not a guessed future path')
        path = frame['path']
        parts = path.split('/')
        if len(parts) != 4 or parts[1:3] != ['v2', 'radar'] or not parts[3] or not all(c.isalnum() or c in '-_' for c in parts[3]):
            raise ValueError('Unexpected historical path')
        url = metadata['host'].rstrip('/') + path + f'/{size}/{z}/{x}/{y}/2/0_0.png'
        rel = f'frames/{parts[3]}/{size}/{z}/{x}/{y}.png'
        return self._png(self._get(url, rel))

    def coverage(self, z, x, y, size=256):
        validate(z, x, y, size)
        url = f'https://tilecache.rainviewer.com/v2/coverage/0/{size}/{z}/{x}/{y}/0/0_0.png'
        raw = self._get(url, f'coverage/{size}/{z}/{x}/{y}.png', 86400)
        return self._png(raw)

    @staticmethod
    def _png(raw):
        from PIL import Image
        image = Image.open(io.BytesIO(raw))
        if image.format != 'PNG' or image.size not in [(256,256),(512,512)]:
            raise ValueError('Unexpected tile format or dimensions')
        image.load()
        return image.convert('RGBA')

def validate(z, x, y, size=256):
    if type(z) is not int or not 0 <= z <= 7:
        raise ValueError('Public API supports integer zoom 0..7')
    if type(x) is not int or type(y) is not int or not 0 <= x < 2**z or not 0 <= y < 2**z:
        raise ValueError('XYZ outside global Web Mercator bounds')
    if size not in [256,512]:
        raise ValueError('Tile size must be256 or512')

def xyz(latitude, longitude, z=7):
    if not all(math.isfinite(v) for v in [latitude,longitude]):
        raise ValueError('Coordinates must be finite')
    if not -85.0511287798066 <= latitude <= 85.0511287798066 or not -180 <= longitude <= 180:
        raise ValueError('Coordinates outside Web Mercator; polar caps unavailable')
    n=2**z
    x=min(n-1,int((longitude+180)/360*n))
    y=min(n-1,max(0,int((1-math.asinh(math.tan(math.radians(latitude)))/math.pi)/2*n)))
    validate(z,x,y)
    return z,x,y

def neighborhood(z,x,y,radius=1):
    validate(z,x,y)
    if type(radius) is not int or not 0 <= radius <= 2:
        raise ValueError('Bounded on-demand radius must be0..2')
    return sorted(set((z,(x+dx) % (2**z),y+dy)
        for dx in range(-radius,radius+1) for dy in range(-radius,radius+1)
        if 0 <= y+dy < 2**z))

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lat',type=float,required=True);p.add_argument('--lon',type=float,required=True)
    p.add_argument('--zoom',type=int,default=7);p.add_argument('--radius',type=int,default=1)
    p.add_argument('--fetch-latest',action='store_true')
    a=p.parse_args();tile=xyz(a.lat,a.lon,a.zoom)
    out={'tile':tile,'needed_tiles':neighborhood(*tile,a.radius),'coverage':'Requires actual coverage and per-frame checks; never assume global radar availability','attribution':'Weather data by RainViewer https://www.rainviewer.com/'}
    if a.fetch_latest:
        import numpy as np
        s=PublicSource();m=s.metadata();f=m['radar']['past'][-1]
        data=np.asarray(s.tile(m,f,*tile));mask=np.asarray(s.coverage(*tile))
        out.update(frame_generation_time=f['time'],visible_echo_pixels=int((data[:,:,3]>0).sum()),static_coverage_pixels=int((mask[:,:,3]==0).sum()),pixels=int(data[:,:,3].size))
    print(json.dumps(out,indent=2))
