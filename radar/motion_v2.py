"""Past-only radar motion, using NumPy only.

estimate(frames, times): frames are decode dictionaries with equally shaped 2-D
signal/valid/echo/dbz arrays; times are strictly increasing UTC UNIX SECONDS.
Only the last three frames are used (two are allowed). Returns vx/vy float32
fields in pixels/minute: +x right, +y down. quality is a heuristic [0,1], NOT a
probability; support counts accepted local temporal pairs, NOT radar coverage.
status='unknown' means motion could not be determined, and predict returns an
all-unknown mask. An explicit status='persistence' requests zero motion; this
choice is recorded in velocity['diagnostics']['prediction_fallback'].

predict(frame, velocity, lead_minutes): lead is nonnegative MINUTES. Returns
(echo_bool, valid_bool, dbz_float), compatible with backtest.prediction.
Unknown source pixels and out-of-domain trajectories remain unknown; there is
no periodic wrapping. Advect the full buffer, then crop.

Fixed configuration describes image-space tracking scales, not a skill gate.
No growth/decay model: even a confidently estimated translation can lose skill
when echoes develop, dissipate, or change direction.
"""
import numpy as np

BLOCK_SIZE = 64
BLOCK_STRIDE = 32
MAX_SHIFT = 24              # pixels per pair, including the two-interval pair
MIN_ECHO = 16
MIN_CORRELATION = 0.45
MIN_OVERLAP = 0.5


def _frame(frame):
    signal = np.asarray(frame['signal'], dtype=float)
    valid = np.asarray(frame['valid'], dtype=bool)
    if signal.ndim != 2 or valid.shape != signal.shape or min(signal.shape) < 4:
        raise ValueError('signal/valid must be equal 2-D arrays, at least 4x4')
    valid = valid & np.isfinite(signal)
    return np.where(valid, signal, 0.), valid


def _track(a, ma, b, mb, radius):
    """Linear masked normalized correlation; positive shift moves a toward b."""
    if min(np.count_nonzero(a[ma] > 0), np.count_nonzero(b[mb] > 0)) < MIN_ECHO:
        return None, 'insufficient_echo'
    if min(ma.sum(), mb.sum()) < 32:
        return None, 'insufficient_support'
    if min(np.std(a[ma]), np.std(b[mb])) < 1e-5:
        return None, 'no_texture'
    h, w = a.shape
    shape = (2*h, 2*w)
    aa = [np.fft.rfft2(v, s=shape) for v in (ma.astype(float), a, a*a)]
    bb = [np.fft.rfft2(v, s=shape) for v in (mb.astype(float), b, b*b)]
    shifts = np.arange(-radius, radius+1)
    indices = np.ix_(shifts % shape[0], shifts % shape[1])
    def corr(i, j):
        return np.fft.irfft2(bb[j]*aa[i].conj(), s=shape)[indices]
    n = np.maximum(corr(0, 0), 1.)
    sa, sb = corr(1, 0), corr(0, 1)
    va = np.maximum(corr(2, 0)-sa*sa/n, 0.)
    vb = np.maximum(corr(0, 2)-sb*sb/n, 0.)
    denom = np.sqrt(va*vb)
    score = np.full(n.shape, -1.)
    usable = (n >= MIN_OVERLAP*min(ma.sum(), mb.sum())) & (denom > 1e-6)
    np.divide(corr(1, 1)-sa*sb/n, denom, out=score, where=usable)
    score = np.clip(score, -1., 1.)
    iy, ix = np.unravel_index(np.argmax(score), score.shape)
    peak = score[iy, ix]
    if peak < MIN_CORRELATION:
        return None, 'weak_correlation'
    if iy in (0, 2*radius) or ix in (0, 2*radius):
        return None, 'search_boundary'
    yy, xx = np.indices(score.shape)
    distant = usable & ((yy-iy)**2+(xx-ix)**2 > 16)
    # Reject repeated/ambiguous patterns; broad but unique peaks are allowed.
    second = np.max(score[distant]) if distant.any() else -1.
    if peak-second < 0.015:
        return None, 'ambiguous_peak'
    def offset(lo, mid, hi):
        curvature = lo-2*mid+hi
        return float(np.clip(.5*(lo-hi)/curvature, -.5, .5)) if curvature < -1e-8 else 0.
    dx = shifts[ix]+offset(score[iy, ix-1], peak, score[iy, ix+1])
    dy = shifts[iy]+offset(score[iy-1, ix], peak, score[iy+1, ix])
    quality = float(peak*np.sqrt(np.clip((peak-second)/.15, 0., 1.)))
    return (float(dx), float(dy), quality), 'estimated'


def _combine(tracks):
    """Weighted geometric-median-like robust temporal velocity, then reweight."""
    values = np.asarray([t[:2] for t in tracks])
    weights = np.asarray([t[2] for t in tracks])
    center = np.median(values, axis=0)
    for _ in range(4):
        distance = np.linalg.norm(values-center, axis=1)
        robust = weights/np.maximum(distance, .05)
        center = np.sum(values*robust[:, None], axis=0)/robust.sum()
    distance = np.linalg.norm(values-center, axis=1)
    # Soft rejection in px/min, never extrapolate acceleration from three frames.
    robust = weights/(1+(distance/.5)**2)
    center = np.sum(values*robust[:, None], axis=0)/robust.sum()
    quality = float(np.average(weights, weights=robust)/(1+np.average(distance, weights=robust)))
    return float(center[0]), float(center[1]), quality


def _bilinear(values, x, y):
    """Zero exterior bilinear sampling and the geometric in-domain support."""
    h, w = values.shape
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x-x0, y-y0
    result = np.zeros(x.shape, dtype=float)
    support = np.zeros(x.shape, dtype=float)
    for ox, oy, weight in ((0, 0, (1-fx)*(1-fy)), (1, 0, fx*(1-fy)),
                            (0, 1, (1-fx)*fy), (1, 1, fx*fy)):
        ix, iy = x0+ox, y0+oy
        inside = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        result[inside] += values[iy[inside], ix[inside]]*weight[inside]
        support[inside] += weight[inside]
    return result, support


def _sample_velocity(vx, vy, x, y):
    """Sample both components with shared indices; outside points stay unknown."""
    h, w = vx.shape
    inside = (x >= 0) & (x <= w-1) & (y >= 0) & (y <= h-1)
    x, y = np.clip(x, 0, w-1), np.clip(y, 0, h-1)
    x0, y0 = x.astype(int), y.astype(int)
    x1, y1 = np.minimum(x0+1, w-1), np.minimum(y0+1, h-1)
    fx, fy = x-x0, y-y0
    def sample(field):
        top = field[y0, x0]*(1-fx)+field[y0, x1]*fx
        bottom = field[y1, x0]*(1-fx)+field[y1, x1]*fx
        return top*(1-fy)+bottom*fy
    return sample(vx), sample(vy), inside


def estimate(frames, times):
    """Estimate stationary Eulerian vx/vy (px/min); see module API contract."""
    if len(frames) != len(times) or len(frames) < 2:
        raise ValueError('at least two frames and matching times required')
    times = np.asarray(times, dtype=float)
    if not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        raise ValueError('times must be finite, strictly increasing Unix seconds')
    frames, times = frames[-3:], times[-3:]/60.
    fields = [_frame(f) for f in frames]
    shape = fields[-1][0].shape
    if any(a.shape != shape for a, _ in fields):
        raise ValueError('all frames must have the same shape')
    pairs = [(i, j) for j in range(1, len(fields)) for i in range(j)]
    global_tracks, pair_info = [], []
    for i, j in pairs:
        a, ma = fields[i]
        b, mb = fields[j]
        # Coarse global FFT bounds memory/CPU; local tracking stays native-res.
        scale = max(1, int(np.ceil(max(shape)/256)))
        if scale > 1:
            hh, ww = (shape[0]//scale)*scale, (shape[1]//scale)*scale
            def reduce(image, mask):
                tiles = (hh//scale, scale, ww//scale, scale)
                weights = mask[:hh, :ww].reshape(tiles).mean(axis=(1, 3))
                values = image[:hh, :ww].reshape(tiles).mean(axis=(1, 3))
                values = np.divide(values, weights, out=np.zeros_like(values), where=weights > 0)
                keep = weights >= .75
                return np.where(keep, values, 0.), keep
            a, ma = reduce(a, ma)
            b, mb = reduce(b, mb)
        track, reason = _track(a, ma, b, mb, max(1, min(MAX_SHIFT//scale, min(a.shape)//3)))
        if track:
            track = (track[0]*scale, track[1]*scale, track[2])
        dt = times[j]-times[i]
        if track:
            global_tracks.append((track[0]/dt, track[1]/dt, track[2]))
        pair_info.append(dict(pair=[i, j], interval_minutes=float(dt), status=reason,
                              displacement=list(track[:2]) if track else None))
    global_motion = _combine(global_tracks) if global_tracks else (0., 0., 0.)
    h, w = shape
    block = min(BLOCK_SIZE, h, w)
    # At most 10x10 local blocks even for large buffer mosaics.
    stride = max(BLOCK_STRIDE, int(np.ceil(max(h-block, w-block)/8)))
    ys = np.unique(np.r_[np.arange(0, h-block+1, stride), h-block])
    xs = np.unique(np.r_[np.arange(0, w-block+1, stride), w-block])
    nodes = []
    for y in ys:
        for x in xs:
            tracks = []
            for i, j in pairs:
                a, ma = (v[y:y+block, x:x+block] for v in fields[i])
                b, mb = (v[y:y+block, x:x+block] for v in fields[j])
                track, _ = _track(a, ma, b, mb, min(MAX_SHIFT, block//3))
                if track:
                    dt = times[j]-times[i]
                    tracks.append((track[0]/dt, track[1]/dt, track[2]))
            if tracks:
                nodes.append((x+(block-1)/2, y+(block-1)/2, *_combine(tracks), len(tracks)))
    # Interpolate a coarse normalized Gaussian kernel, then bilinear upsample.
    # This bounds work for buffer mosaics, with no scipy dependency.
    gy = np.linspace(0, h-1, max(2, (h+15)//16))
    gx = np.linspace(0, w-1, max(2, (w+15)//16))
    xx, yy = np.meshgrid(gx, gy)
    norm = np.full(xx.shape, .03 if global_tracks else 0.)
    vx, vy = norm*global_motion[0], norm*global_motion[1]
    quality = norm*global_motion[2]
    support = np.zeros(xx.shape)
    for x, y, u, v, q, count in nodes:
        weight = q*np.exp(-((xx-x)**2+(yy-y)**2)/(2*stride**2))
        norm += weight
        vx += weight*u
        vy += weight*v
        quality += weight*q
        support += weight*count
    nonzero = norm > 1e-12
    for field in (vx, vy, quality, support):
        np.divide(field, norm, out=field, where=nonzero)
    yy, xx = np.indices(shape, dtype=float)
    xx *= (len(gx)-1)/(w-1)
    yy *= (len(gy)-1)/(h-1)
    dense = [_bilinear(v, xx, yy)[0].astype(np.float32) for v in (vx, vy, quality, support)]
    return dict(vx=dense[0], vy=dense[1], quality=dense[2], support=dense[3],
                units='pixels/minute', status='estimated' if nodes or global_tracks else 'unknown',
                diagnostics=dict(frames_used=len(frames), pairs=pair_info,
                                 global_velocity=list(global_motion[:2]),
                                 accepted_global_pairs=len(global_tracks), accepted_local_nodes=len(nodes),
                                 fallback='global' if global_tracks else ('local_only' if nodes else 'unknown'),
                                 max_pair_displacement=MAX_SHIFT))


def predict(frame, velocity, lead_minutes, output_region=None):
    """Backward midpoint characteristic integration; sample source only once.

    Integration uses <= 2 minute steps, <= 1 pixel nominal steps (cap 256).
    A frozen velocity field is assumed for the full lead. All nonzero bilinear
    source contributions must be valid. dBZ requires all contributions finite;
    an ambiguous dBZ does not invalidate occurrence. Unknown occurrence is
    False with valid=False: consumers MUST apply valid when scoring/cropping.
    """
    lead = float(lead_minutes)
    if not np.isfinite(lead) or lead < 0:
        raise ValueError('lead must be finite nonnegative minutes')
    _, valid = _frame(frame)
    h, w = valid.shape
    y0, y1, x0, x1 = output_region if output_region is not None else (0, h, 0, w)
    if not (0 <= y0 < y1 <= h and 0 <= x0 < x1 <= w):
        raise ValueError('output_region outside source mosaic')
    output_shape = (y1-y0, x1-x0)
    status = velocity.get('status', 'unknown')
    diagnostics = velocity.setdefault('diagnostics', {})
    if status not in ('estimated', 'persistence'):
        diagnostics['prediction_fallback'] = 'unknown_motion_masked'
        return np.zeros(output_shape, bool), np.zeros(output_shape, bool), np.full(output_shape, np.nan)
    if status == 'persistence':
        diagnostics['prediction_fallback'] = 'explicit_persistence'
        vx, vy = np.zeros((h, w)), np.zeros((h, w))
    else:
        vx = np.broadcast_to(np.asarray(velocity['vx'], dtype=float), (h, w))
        vy = np.broadcast_to(np.asarray(velocity['vy'], dtype=float), (h, w))
    if not np.isfinite(vx).all() or not np.isfinite(vy).all():
        raise ValueError('velocity must be finite')
    echo = np.asarray(frame['echo'], dtype=bool)
    dbz = np.asarray(frame['dbz'], dtype=float)
    if echo.shape != valid.shape or dbz.shape != valid.shape:
        raise ValueError('echo/dbz must match signal shape')
    yy, xx = np.indices(output_shape, dtype=float)
    yy += y0
    xx += x0
    constant = np.ptp(vx) < 1e-6 and np.ptp(vy) < 1e-6
    if constant:
        # Exact characteristics for a constant field; one source interpolation.
        xx -= lead*float(vx[0, 0])
        yy -= lead*float(vy[0, 0])
        inside = (xx >= 0) & (xx <= w-1) & (yy >= 0) & (yy <= h-1)
        steps = 0
    else:
        speed = float(np.max(np.hypot(vx, vy)))
        steps = min(256, max(1, int(np.ceil(lead/2)), int(np.ceil(lead*speed))))
        dt = lead/steps
        inside = np.ones(output_shape, dtype=bool)
        for _ in range(steps):
            u, v, s = _sample_velocity(vx, vy, xx, yy)
            mx, my = xx-.5*dt*u, yy-.5*dt*v
            u, v, sm = _sample_velocity(vx, vy, mx, my)
            inside &= s & sm
            xx -= dt*u
            yy -= dt*v
            inside &= (xx >= 0) & (xx <= w-1) & (yy >= 0) & (yy <= h-1)
            # Once outside, it stays unknown; clip only to bound numerical work.
            xx = np.clip(xx, 0, w-1)
            yy = np.clip(yy, 0, h-1)
    fraction, _ = _bilinear((echo & valid).astype(float), xx, yy)
    known, _ = _bilinear(valid.astype(float), xx, yy)
    output_valid = inside & (known > 1-1e-8)
    finite = valid & np.isfinite(dbz)
    known_dbz, _ = _bilinear(finite.astype(float), xx, yy)
    values, _ = _bilinear(np.where(finite, dbz, 0.), xx, yy)
    occurrence = output_valid & (fraction >= .5)
    diagnostics['last_prediction'] = dict(lead_minutes=lead, integration_steps=steps,
                                         integration_capped=steps == 256, constant_velocity_fast_path=bool(constant),
                                         motion_status=status, valid_pixels=int(output_valid.sum()),
                                         unknown_pixels=int((~output_valid).sum()))
    return occurrence, output_valid, np.where(output_valid & (known_dbz > 1-1e-8), values, np.nan)
