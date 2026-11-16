"""Observation-only RainViewer global translation. See README.md for contract."""
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import threading
import time
import numpy as np
from .primitives import _track, _combine, _bilinear


def _decode(value):
    dbz, valid = value
    dbz = np.array(dbz, dtype=np.float32, copy=True)
    valid = np.array(valid, dtype=bool, copy=True)
    if dbz.shape != (256, 256) or valid.shape != dbz.shape:
        raise ValueError('rv_frame must return two 256x256 arrays')
    valid &= np.isfinite(dbz)
    dbz[~valid] = np.nan
    dbz.flags.writeable = valid.flags.writeable = False
    return dbz, valid


def translate(dbz, valid, dx, dy, region=None):
    """Backward sample with exact source masks; positive dx/right, dy/down."""
    h, w = dbz.shape
    y0, y1, x0, x1 = region or (0, h, 0, w)
    yy, xx = np.indices((y1-y0, x1-x0), dtype=float)
    xx += x0-float(dx)
    yy += y0-float(dy)
    finite = valid & np.isfinite(dbz)
    known, _ = _bilinear(finite.astype(float), xx, yy)
    values, _ = _bilinear(np.where(finite, dbz, 0.), xx, yy)
    keep = ((xx >= 0) & (xx <= w-1) & (yy >= 0) & (yy <= h-1)
            & (known > 1-1e-8))
    return np.where(keep, values, np.nan).astype(np.float32), keep


def estimate(frames, times):
    """Two consecutive pairs, independent +10 minute holdout, z6 px/min."""
    info = dict(status='unknown', reason='insufficient_history', pairs=[],
                used_source_times=list(times), dxdy=None, units='z7_pixels/minute',
                holdout=None)
    if len(frames) != 3 or len(times) != 3:
        return info
    dt = np.diff(np.asarray(times, float))/60.
    if not np.isfinite(dt).all() or not 5 <= dt[0] <= 20 or dt[1] != 10:
        info['reason'] = 'requires_ten_minute_holdout'
        return info
    signals = [np.where(v, np.clip((d-5.)/50., 0., 1.), 0.) for d, v in frames]
    tracks = []
    for i in range(2):
        track, reason = _track(signals[i], frames[i][1], signals[i+1], frames[i+1][1], 24)
        info['pairs'].append(dict(times=[times[i], times[i+1]], status=reason,
                                  displacement=list(track[:2]) if track else None,
                                  quality=track[2] if track else None))
        if track is not None:
            tracks.append((track[0]/dt[i], track[1]/dt[i], track[2]))
    if len(tracks) != 2:
        info['reason'] = 'unreliable_pair'
        return info
    speed = max(np.hypot(*t[:2]) for t in tracks)
    disagreement = float(np.linalg.norm(np.asarray(tracks[0][:2])-tracks[1][:2]))
    info['temporal_disagreement_z6_px_min'] = disagreement
    if min(t[2] for t in tracks) < .2 or disagreement > max(.3, .5*speed):
        info['reason'] = 'inconsistent_motion'
        return info
    # Only the FIRST pair predicts the held-out latest frame. The second pair
    # participates in reliability/final motion, never in holdout prediction.
    predicted, mask = translate(signals[1], frames[1][1], tracks[0][0]*10, tracks[0][1]*10)
    common = mask & frames[1][1] & frames[2][1]
    active = common & ((signals[1] > .1) | (signals[2] > .1) | (predicted > .1))
    coverage = float(common.sum()/max(1, frames[2][1].sum()))
    if active.sum() < 32 or coverage < .7:
        info['reason'] = 'insufficient_holdout_support'
        return info
    actual = signals[2][active]
    moving = float(np.mean((predicted[active]-actual)**2))
    persistence = float(np.mean((signals[1][active]-actual)**2))
    skill = 1-moving/persistence if persistence > 1e-10 else None
    info['holdout'] = dict(lead_seconds=600, pixels=int(active.sum()),
                           common_fraction=coverage, advection_mse=moving,
                           persistence_mse=persistence, skill=skill)
    if skill is None or skill < .02:
        info['reason'] = 'holdout_not_better_than_persistence'
        return info
    vx, vy, quality = _combine(tracks)
    info.update(status='estimated', reason='two_pairs_and_holdout_passed',
                dxdy=[2*vx, 2*vy], quality=quality)
    return info


class _Cache:
    """Bounded completed AND in-flight entries; failures are safely deduped."""
    def __init__(self, limit=16, ttl=1200):
        self.limit, self.ttl = limit, ttl
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key, build):
        while True:
            owner = False
            with self.lock:
                now = time.monotonic()
                for k, (created, f) in list(self.entries.items()):
                    if f.done() and now-created >= self.ttl:
                        del self.entries[k]
                if key in self.entries:
                    _, future = self.entries[key]
                    self.entries.move_to_end(key)
                    break
                if len(self.entries) >= self.limit:
                    old = next((k for k, (_, f) in self.entries.items() if f.done()), None)
                    if old is not None:
                        del self.entries[old]
                    else:
                        wait = next(iter(self.entries.values()))[1]
                if len(self.entries) < self.limit:
                    future = Future()
                    self.entries[key] = (now, future)
                    owner = True
                    break
            # Do not hold the cache lock or use the fetch executor to build.
            try:
                wait.result()
            except Exception:
                pass
        if owner:
            try:
                future.set_result(build())
            except Exception as error:
                future.set_exception(error)
        return future.result()


def _fetch_error(error):
    # Never leak callback messages/URLs, which could contain credentials.
    name = type(error).__name__
    return 'rate_limited' if ('RateLimit' in name or getattr(error, 'code', None) == 429) else 'source_unavailable'


class Engine:
    """Keep ONE instance per callback/source. Fetch executor must be independent
    of request/build workers; callback must not synchronously submit to that
    same executor. Hybrid.rv_frame already uses its own source_pool internally.
    """
    def __init__(self, executor=None, clock=time.time):
        self.executor = executor if executor is not None else ThreadPoolExecutor(max_workers=12, thread_name_prefix='rv-nowcast-fetch')
        self._owns_executor = executor is None
        self.clock = clock
        self.motion_cache = _Cache()
        self.mosaic_cache = _Cache()

    def close(self):
        if self._owns_executor:
            self.executor.shutdown(wait=True)

    def predict(self, source_metadata, tile, valid_time, rv_frame):
        """Return (dbz_float32, valid_bool, diagnostics_dict).
        Unknown pixels are NaN/False. diagnostics.model is advection or
        persistence; status=unavailable means no usable forecast.
        """
        clock, executor = self.clock, self.executor
        tile = tuple(tile)
        if (len(tile) != 3 or any(type(t) is not int for t in tile)
                or tile[0] != 7 or not all(0 <= t < 128 for t in tile[1:])):
            raise ValueError('tile must be an in-bounds z7 XYZ tuple')
        if executor is None:
            raise ValueError('independent fetch executor required')
        past = source_metadata['radar']['past']
        times = [f['time'] for f in past]
        if not times or any(not np.isfinite(t) for t in times) or times != sorted(set(times)):
            raise ValueError('metadata must contain strictly ordered observed frames')
        latest = times[-1]
        target = float(valid_time)
        if not np.isfinite(target) or not latest <= target <= latest+4800:
            raise ValueError('target must be latest through latest+4800 seconds')
        now = float(clock())
        age = now-latest
        info = dict(status='unknown', reason='insufficient_history', pairs=[],
                    used_source_times=times[-3:], dxdy=None, units='z7_pixels/minute', holdout=None)
        def output(d=None, v=None, model='persistence', status='available', reason=None):
            if d is None:
                d, v = np.full((256, 256), np.nan, np.float32), np.zeros((256, 256), bool)
            diagnostics = deepcopy(info)
            diagnostics.update(status=status, motion_status=info['status'],
                               reason=reason or info['reason'], age_seconds=age,
                               lead_seconds=target-latest, latest_observed_source='RainViewer',
                               latest_observed_time=latest, experimental=True)
            diagnostics.update(model=model, source='RainViewer', source_time=latest,
                               valid_time=target, origin=latest, dx=diagnostics['dxdy'][0] if diagnostics['dxdy'] else None,
                               dy=diagnostics['dxdy'][1] if diagnostics['dxdy'] else None)
            diagnostics['displacement_z7_pixels'] = (list(np.asarray(info['dxdy'])*(target-latest)/60.)
                                                      if model == 'advection' else [0., 0.])
            return d.copy(), v.copy(), diagnostics
        if not np.isfinite(age) or not 0 <= age < 1800:
            return output(status='unavailable', reason='stale_or_future_observation')
        frames = past[-3:]
        parent = (6, tile[1]//2, tile[2]//2)
        identity = (source_metadata.get('host'), tuple((f['time'], f['path']) for f in frames))
        def motion():
            jobs = [executor.submit(rv_frame, source_metadata, f, parent) for f in frames]
            try:
                decoded = [_decode(j.result()) for j in jobs]
            except Exception as error:
                failed = deepcopy(info)
                failed['reason'] = _fetch_error(error)
                return failed
            return estimate(decoded, [f['time'] for f in frames])
        info = self.motion_cache.get((identity, parent), motion)
        moving = info['status'] == 'estimated'
        def mosaic():
            d = np.full((768, 768), np.nan, np.float32)
            v = np.zeros((768, 768), bool)
            jobs = []
            for oy in range(-1, 2):
                for ox in range(-1, 2):
                    if not moving and (ox or oy):
                        continue
                    neighbor = (7, tile[1]+ox, tile[2]+oy)
                    if not 0 <= neighbor[1] < 128 or not 0 <= neighbor[2] < 128:
                        continue
                    jobs.append((oy, ox, executor.submit(rv_frame, source_metadata, frames[-1], neighbor)))
            failures = []
            for oy, ox, job in jobs:
                try:
                    a, b = _decode(job.result())
                    ys, xs = slice((oy+1)*256, (oy+2)*256), slice((ox+1)*256, (ox+2)*256)
                    d[ys, xs], v[ys, xs] = a, b
                except Exception as error:
                    failures.append(dict(offset=[ox, oy], reason=_fetch_error(error)))
            return d, v, failures
        d, v, failures = self.mosaic_cache.get((identity, tile), mosaic)
        info = deepcopy(info)
        info['mosaic_failures'] = failures
        info['mosaic_source_times'] = [latest]
        # Cached raw observations never override freshness at the time of return.
        age = float(clock())-latest
        if not 0 <= age < 1800:
            return output(status='unavailable', reason='stale_or_future_observation')
        center = (slice(256, 512), slice(256, 512))
        if moving:
            dx, dy = np.asarray(info['dxdy'])*(target-latest)/60.
            result, known = translate(d, v, dx, dy, (256, 512, 256, 512))
            if np.any(v[center] & ~known):
                return output(d[center], v[center], reason='advection_unknown_freeze_latest_center',
                              status='available' if v[center].any() else 'unavailable')
            return output(result, known, 'advection', 'available' if known.any() else 'unavailable')
        return output(d[center], v[center], status='available' if v[center].any() else 'unavailable')
