"""Two-color-per-block ASTC8x8, 256x256 uint8 -> 512x512 KTX1.

Each 4x4 source block uses its min/max as full 8-bit luminance endpoints;
each source sample selects the nearest endpoint (ties select min), and its
one-bit weight is repeated 2x2. No spatial averaging. This is lossy grayscale,
not an Apple dBZ calibration. Caller must reject missing data before converting
to uint8. Only existing NumPy and Python standard library are used.

Layout checked against ARM astc-encoder source:
Source/astcenc_block_sizes.cpp: decode_block_mode_2d(0x544): 8x8, QUANT_2,
 single plane, 64 weight bits.
Source/astcenc_symbolic_physical.cpp: symbolic_to_physical: one partition,
 CEM=0 in bits13..16, two QUANT_256 endpoints in bits17..24 and25..32;
 weights reverse the ISE bitstream, starting at bit127.
Source/astcenc_color_unquantize.cpp: luminance_unpack: L0,L1, opaque alpha.
https://github.com/ARM-software/astc-encoder/tree/main/Source
"""
import struct
import numpy as np

_MAGIC = b'\xabKTX 11\xbb\r\n\x1a\n'
_HEADER = _MAGIC + struct.pack('<14I', 0x04030201, 0, 1, 0, 0x93B7,
                             0x1908, 512, 512, 0, 0, 1, 1, 0, 65536)


def encode_gray256(gray):
    """Return exactly 65604 KTX bytes; require shape (256,256), dtype uint8.

    Endpoint quantization is full 8-bit; each 4x4 region has at most two gray
    values, while individual source sample positions remain at 256x256.
    """
    a = np.asarray(gray)
    if a.shape != (256, 256) or a.dtype != np.uint8:
        raise ValueError('gray must be a 256x256 uint8 array')
    blocks = a.reshape(64, 4, 64, 4).transpose(0, 2, 1, 3).reshape(4096, 4, 4)
    lo = blocks.min(axis=(1, 2))
    hi = blocks.max(axis=(1, 2))
    # uint16 prevents wraparound in both sides of the nearest-endpoint test.
    choose_hi = (blocks.astype(np.uint16) * 2 >
                 (lo.astype(np.uint16) + hi)[:, None, None])
    # Four source decisions become eight one-bit horizontal weights.
    rows = np.packbits(np.repeat(choose_hi, 2, axis=2), axis=2)[:, :, 0]
    payload = np.zeros((4096, 16), dtype=np.uint8)
    payload[:, 0] = 0x44
    payload[:, 1] = 0x05
    payload[:, 2] = lo << 1
    payload[:, 3] = (lo >> 7) | (hi << 1)
    payload[:, 4] = hi >> 7
    payload[:, 8:16] = np.repeat(rows, 2, axis=1)[:, ::-1]
    return _HEADER + payload.tobytes()


def decode_reference(body):
    """CPU decoder for this restricted block layout, returning 512x512 uint8.

    Validates the KTX header and fixed-mode fields. Parses physical bytes;
    does not call the encoder or recompute decisions from the source image.
    """
    if len(body) != 65604 or body[:68] != _HEADER:
        raise ValueError('Expected single-level 512x512 ASTC8x8 KTX1')
    b = np.frombuffer(body, dtype=np.uint8, offset=68).reshape(4096, 16)
    if (not np.all(b[:, :2] == [0x44, 0x05]) or
            np.any(b[:, 2] & 1) or np.any(b[:, 4] & 254) or
            np.any(b[:, 5:8])):
        raise ValueError('Unsupported ASTC mode, partition, CEM, or padding')
    lo = (b[:, 2] >> 1) | ((b[:, 3] & 1) << 7)
    hi = (b[:, 3] >> 1) | ((b[:, 4] & 1) << 7)
    weights = np.unpackbits(b[:, 8:16][:, ::-1], axis=1).reshape(4096, 8, 8)
    decoded = np.where(weights, hi[:, None, None], lo[:, None, None])
    return decoded.reshape(64, 64, 8, 8).transpose(0, 2, 1, 3).reshape(512, 512)
