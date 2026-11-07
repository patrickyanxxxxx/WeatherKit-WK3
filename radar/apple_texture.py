"""Experimental Apple radar texture adapter, without a weather-to-gray claim.

A KTX1 image contains 512x512 ASTC 8x8 LDR void-extent constant-color blocks.
Thus spatial detail is at most 64x64 cells, not 512 independent pixels or 1 km
fidelity. Per-source-pixel calibrated gray is area-averaged in display space;
this is an experimental display reduction, NOT a physical dBZ/Z/rainfall average.
No Apple grayscale response is assumed: calibration points must come from the
caller. A missing point/range, missing ROI data, or missing dry encoding raises
UnsupportedTexture, so the caller can retain its original-response fallback.

The NPZ schema is echo/valid boolean arrays and a conditional numeric dbz array.
valid=False means unknown, not dry; valid=True and echo=False is displayed-echo
nondetection, not verified precipitation-free weather. The caller must assert
source_units='dBZ' because the NPZ has no trustworthy units field. roi uses
(top, left, bottom, right), exclusive pixel bounds in source-array coordinates.
"""

from pathlib import Path
import struct
from typing import NamedTuple

import numpy as np


class UnsupportedTexture(ValueError):
    """Do not provide an Apple replacement body; use caller fallback."""


class ArtificialTexture(NamedTuple):
    body: bytes
    marker: str = "ARTIFICIAL-NOT-WEATHER"


_KTX_MAGIC = b"\xabKTX 11\xbb\r\n\x1a\n"
_HEADER = struct.Struct("<13I")
_BLOCK_PREFIX = b"\xfc\xfd\xff\xff\xff\xff\xff\xff"
_SIZE = 512
_GRID = 64


def _encode_cells(cells):
    """Encode 64x64 uint8 grayscale cells as opaque constant ASTC blocks."""
    a = np.asarray(cells)
    if a.shape != (_GRID, _GRID) or a.dtype != np.dtype("uint8"):
        raise ValueError("cells must be a 64x64 uint8 array")
    header = _KTX_MAGIC + _HEADER.pack(
        0x04030201, 0, 1, 0, 0x93B7, 0x1908, _SIZE, _SIZE,
        0, 0, 1, 1, 0,
    )
    out = bytearray(header + struct.pack("<I", _GRID * _GRID * 16))
    for gray in a.flat:
        channel = int(gray) * 257  # UNORM16: 255 must become 65535, not 255.
        out.extend(_BLOCK_PREFIX)
        out.extend(struct.pack("<4H", channel, channel, channel, 65535))
    return bytes(out)


def _calibration(points):
    if points is None:
        raise UnsupportedTexture(
            "Uncalibrated Apple grayscale: supply measured (dBZ, gray) points"
        )
    try:
        a = np.asarray(points, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise UnsupportedTexture("Calibration must be numeric (dBZ, gray) pairs") from exc
    if a.ndim != 2 or a.shape[1] != 2 or a.shape[0] < 2:
        raise UnsupportedTexture("Calibration needs at least two (dBZ, gray) points")
    gray_delta = np.diff(a[:, 1])
    gray_monotonic = np.all(gray_delta > 0) or np.all(gray_delta < 0)
    if (not np.isfinite(a).all() or np.any(np.diff(a[:, 0]) <= 0)
            or not gray_monotonic or a[:, 1].min() < 0 or a[:, 1].max() > 255):
        raise UnsupportedTexture(
            "Calibration needs sorted unique dBZ and strictly monotonic finite gray in [0,255]"
        )
    return a


def encode_arrays(echo, valid, dbz, *, calibration=None, source_units=None,
                  dry_gray=None, roi=None):
    """Return an Apple KTX body, or raise UnsupportedTexture; never fake unknown.

    The 512-grid uses nearest-neighbor center sampling from the source ROI.
    Each 8x8 display-pixel group is reduced by arithmetic mean of its mapped
    gray values, then rounded to nearest-even uint8. This is not a physical
    reflectivity aggregate. The entire requested ROI is checked for unknowns
    before sampling; one invalid source pixel rejects the whole texture.
    """
    points = _calibration(calibration)
    if source_units != "dBZ":
        raise UnsupportedTexture("Source units unverified: explicitly set source_units='dBZ'")
    e, v, d = np.asarray(echo), np.asarray(valid), np.asarray(dbz)
    if (e.ndim != 2 or e.shape != v.shape or e.shape != d.shape
            or not e.size or e.dtype != np.dtype("bool")
            or v.dtype != np.dtype("bool") or d.dtype.kind not in "fi"):
        raise UnsupportedTexture("Expected same-shape 2D boolean echo/valid and numeric dBZ")
    if roi is not None:
        if (not isinstance(roi, (tuple, list)) or len(roi) != 4
                or any(type(n) is not int for n in roi)):
            raise UnsupportedTexture("ROI must be integer (top,left,bottom,right) bounds")
        top, left, bottom, right = roi
        if not (0 <= top < bottom <= e.shape[0] and 0 <= left < right <= e.shape[1]):
            raise UnsupportedTexture("ROI is empty or outside the source arrays")
        e, v, d = (a[top:bottom, left:right] for a in (e, v, d))
    if not v.any():
        raise UnsupportedTexture("ROI has no valid source pixels (all missing); use fallback")
    if not v.all():
        raise UnsupportedTexture("Unknown pixels in requested ROI; no Apple body, use fallback")
    if np.any(e & ~np.isfinite(d)):
        raise UnsupportedTexture("Echo dBZ missing/nonfinite in requested ROI; use fallback")
    wet = d[e]
    if wet.size and (wet.min() < points[0, 0] or wet.max() > points[-1, 0]):
        raise UnsupportedTexture("Echo dBZ outside measured calibration range; use fallback")
    if np.any(~e) and (dry_gray is None or not np.isscalar(dry_gray)
                       or not np.isfinite(dry_gray) or not 0 <= dry_gray <= 255):
        raise UnsupportedTexture("Dry/nondetection gray is uncalibrated: supply dry_gray in [0,255]")
    gray = np.empty(d.shape, dtype=np.float64)
    if wet.size:
        gray[e] = np.interp(wet, points[:, 0], points[:, 1])
    if np.any(~e):
        gray[~e] = dry_gray
    ys = np.minimum((np.arange(_SIZE) + 0.5) * d.shape[0] / _SIZE, d.shape[0] - 1).astype(int)
    xs = np.minimum((np.arange(_SIZE) + 0.5) * d.shape[1] / _SIZE, d.shape[1] - 1).astype(int)
    display_gray = gray[np.ix_(ys, xs)]
    cells = np.rint(display_gray.reshape(_GRID, 8, _GRID, 8).mean(axis=(1, 3)))
    return _encode_cells(cells.astype(np.uint8))


def encode_npz(path, *, calibration=None, source_units=None, dry_gray=None, roi=None):
    """Read echo/valid/dbz from a forecast NPZ without pickles and encode ROI."""
    with np.load(Path(path), allow_pickle=False) as data:
        if not {"echo", "valid", "dbz"}.issubset(data.files):
            raise UnsupportedTexture("NPZ must contain echo, valid and dbz arrays")
        return encode_arrays(data["echo"], data["valid"], data["dbz"],
                             calibration=calibration, source_units=source_units,
                             dry_gray=dry_gray, roi=roi)


def synthetic_ramp(steps=16):
    """Artificial 16/32-level left-to-right gray test, never meteorological.

    The marker is out-of-band so the KTX header/payload remain byte-for-byte
    compatible with the phone-accepted synthetic format. Pass marker in a
    response diagnostic header; never present this texture as a radar forecast.
    """
    if type(steps) is not int or steps not in (16, 32):
        raise ValueError("Artificial ramp steps must be 16 or 32")
    levels = np.rint(np.linspace(0, 255, steps)).astype(np.uint8)
    cells = np.tile(np.repeat(levels, _GRID // steps), (_GRID, 1))
    return ArtificialTexture(_encode_cells(cells))
