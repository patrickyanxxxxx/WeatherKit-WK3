"""256-square uint8 gray -> opaque 512-square KTX1 ASTC8x8.

Requires existing NumPy, Pillow and Arm astcenc (tested with 5.2.0).
No production imports, changes, network access or fallback encoder.
"""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

import numpy as np
from PIL import Image

MAGIC = b'\xabKTX 11\xbb\r\n\x1a\n'
# Full KTX1 header + level-0 imageSize; byte-for-byte legacy contract.
HEADER = MAGIC + struct.pack('<14I', 0x04030201, 0, 1, 0, 0x93B7,
                            0x1908, 512, 512, 0, 0, 1, 1, 0, 65536)
FIELD_NAMES = ('endianness', 'glType', 'glTypeSize', 'glFormat',
               'glInternalFormat', 'glBaseInternalFormat', 'pixelWidth',
               'pixelHeight', 'pixelDepth', 'numberOfArrayElements',
               'numberOfFaces', 'numberOfMipmapLevels', 'bytesOfKeyValueData',
               'imageSize')


class EncodingError(RuntimeError):
    """Missing/failed/timed-out encoder or invalid KTX output."""


def validate_ktx(body):
    """Check every header field, exact file/payload length, no metadata/trailer.

    This validates the container contract, not ASTC block semantics. Offline
    quality tests additionally decode the payload with astcenc and the GPU.
    """
    if not isinstance(body, bytes) or len(body) != 65604:
        raise EncodingError('KTX must be exactly 65604 bytes')
    if body[:12] != MAGIC:
        raise EncodingError('Invalid KTX1 identifier')
    actual = struct.unpack('<14I', body[12:68])
    expected = struct.unpack('<14I', HEADER[12:])
    for name, got, want in zip(FIELD_NAMES, actual, expected):
        if got != want:
            raise EncodingError(f'Invalid {name}: {got:#x}, expected {want:#x}')
    return body


def resize_gray256(gray, *, resize='bilinear'):
    """Pillow pixel-center resize in grayscale, before RGBA expansion."""
    a = np.asarray(gray)
    if a.shape != (256, 256) or a.dtype != np.uint8:
        raise ValueError('gray must be a 256x256 uint8 array')
    methods = {'bilinear': Image.Resampling.BILINEAR,
               'nearest': Image.Resampling.NEAREST}
    if resize not in methods:
        raise ValueError('resize must be bilinear or nearest')
    return np.asarray(Image.fromarray(a).resize((512, 512), methods[resize]))


def encode_gray256(gray, *, executable=None, quality='medium', resize='bilinear'):
    """Return 65604 bytes. astcenc runs with one thread and timeout=5 seconds.

    Set executable explicitly or ASTCENC; default searches PATH for astcenc.
    TemporaryDirectory isolates concurrent calls and cleans up on all exits.
    No shell interpolation; the caller owns any handling of EncodingError.
    """
    if quality not in ('fast', 'medium'):
        raise ValueError('quality must be fast or medium')
    enlarged = resize_gray256(gray, resize=resize)
    candidate = executable or os.environ.get('ASTCENC', 'astcenc')
    binary = shutil.which(str(candidate))
    if not binary:
        raise EncodingError(f'astcenc executable not found: {candidate}')
    rgba = np.empty((512, 512, 4), dtype=np.uint8)
    rgba[:, :, :3] = enlarged[:, :, None]
    rgba[:, :, 3] = 255
    with tempfile.TemporaryDirectory(prefix='gray-astc-') as temporary:
        directory = Path(temporary)
        source, output = directory / 'input.png', directory / 'output.ktx'
        Image.fromarray(rgba).save(source)
        command = [binary, '-cl', str(source), str(output), '8x8',
                   '-' + quality, '-j', '1']
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise EncodingError('astcenc exceeded 5-second timeout') from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise EncodingError('astcenc failed') from exc
        try:
            # Bound read, also rejects files with trailing bytes.
            with output.open('rb') as stream:
                body = stream.read(65605)
        except OSError as exc:
            raise EncodingError('astcenc did not produce a readable KTX') from exc
        return validate_ktx(body)
