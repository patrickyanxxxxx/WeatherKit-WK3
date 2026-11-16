"""NumPy masked tracking/interpolation copied unchanged from motion_v2.py."""
import numpy as np

BLOCK_SIZE = 64

BLOCK_STRIDE = 32

MAX_SHIFT = 24

MIN_ECHO = 16

MIN_CORRELATION = 0.45

MIN_OVERLAP = 0.5

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
