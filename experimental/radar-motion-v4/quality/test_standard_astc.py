"""Contract/error-path tests; real quality is measured by check_quality.py."""
from pathlib import Path
import struct
import subprocess
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import standard_astc as module


class Contract(unittest.TestCase):
    def setUp(self):
        self.gray = np.broadcast_to(np.arange(256, dtype=np.uint8), (256, 256)).copy()
        self.body = module.HEADER + bytes(65536)

    def test_full_header(self):
        self.assertEqual(module.validate_ktx(self.body), self.body)
        for index in range(14):
            with self.subTest(field=module.FIELD_NAMES[index]):
                body = bytearray(self.body)
                struct.pack_into('<I', body, 12 + index * 4,
                                 struct.unpack_from('<I', body, 12 + index * 4)[0] ^ 1)
                with self.assertRaises(module.EncodingError):
                    module.validate_ktx(bytes(body))
        for body in (b'X' + self.body[1:], self.body[:-1], self.body + b'X'):
            with self.assertRaises(module.EncodingError):
                module.validate_ktx(body)

    def test_input_options(self):
        for gray in (np.zeros((255, 256), np.uint8), np.zeros((256, 256), float)):
            with self.assertRaises(ValueError):
                module.encode_gray256(gray)
        for options in ({'quality': 'slow'}, {'resize': 'cubic'}):
            with self.assertRaises(ValueError):
                module.encode_gray256(self.gray, **options)
        with patch.object(module.shutil, 'which', return_value=None):
            with self.assertRaises(module.EncodingError):
                module.encode_gray256(self.gray)

    def test_resize_and_rgba_command_and_cleanup(self):
        nearest = module.resize_gray256(self.gray, resize='nearest')
        self.assertTrue(np.array_equal(nearest, np.repeat(np.repeat(self.gray, 2, 0), 2, 1)))
        edge = np.zeros((256, 256), np.uint8)
        edge[:, 128:] = 255
        self.assertFalse(np.array_equal(module.resize_gray256(edge, resize='nearest'),
                                       module.resize_gray256(edge)))
        directories = []
        def fake_run(command, **options):
            self.assertEqual(options, dict(check=True, capture_output=True, timeout=5))
            self.assertEqual(command[1], '-cl')
            self.assertEqual(command[4:], ['8x8', '-medium', '-j', '1'])
            source, output = Path(command[2]), Path(command[3])
            directories.append(source.parent)
            rgba = np.array(Image.open(source))
            self.assertEqual(rgba.shape, (512, 512, 4))
            self.assertTrue(np.all(rgba[:, :, 3] == 255))
            self.assertTrue(np.all(rgba[:, :, :3] == module.resize_gray256(self.gray)[:, :, None]))
            output.write_bytes(self.body)
        with patch.object(module.shutil, 'which', return_value='/fake/astcenc'), patch.object(module.subprocess, 'run', side_effect=fake_run):
            for _ in range(2):
                self.assertEqual(module.encode_gray256(self.gray), self.body)
        self.assertNotEqual(*directories)
        self.assertTrue(all(not d.exists() for d in directories))

    def test_failure_cleanup(self):
        for fault in ('timeout', 'process', 'missing-output', 'bad-header'):
            directories = []
            def fake_run(command, **options):
                directories.append(Path(command[2]).parent)
                if fault == 'timeout':
                    raise subprocess.TimeoutExpired(command, 5)
                if fault == 'process':
                    raise subprocess.CalledProcessError(1, command)
                if fault == 'bad-header':
                    Path(command[3]).write_bytes(bytes(65604))
            with self.subTest(fault=fault), patch.object(module.shutil, 'which', return_value='/fake/astcenc'), patch.object(module.subprocess, 'run', side_effect=fake_run):
                with self.assertRaises(module.EncodingError):
                    module.encode_gray256(self.gray)
                self.assertTrue(all(not d.exists() for d in directories))


if __name__ == '__main__':
    unittest.main(verbosity=2)
