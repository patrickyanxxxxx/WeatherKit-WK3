import unittest,struct,tempfile
from pathlib import Path
import numpy as np
from apple_texture import encode_arrays,encode_npz,synthetic_ramp,UnsupportedTexture

class TextureTests(unittest.TestCase):
 def inputs(self):
  return np.ones((64,64),bool),np.ones((64,64),bool),np.full((64,64),30.)
 def encode(self,e,v,d,**kw):
  args=dict(calibration=[(0,0),(60,240)],source_units='dBZ',dry_gray=0);args.update(kw)
  return encode_arrays(e,v,d,**args)
 def test_exact_container_blocks_and_high_byte(self):
  b=self.encode(*self.inputs());self.assertEqual(len(b),65604);self.assertEqual(b[:12],b'\xabKTX 11\xbb\r\n\x1a\n')
  h=struct.unpack_from('<13I',b,12);self.assertEqual(h,(0x04030201,0,1,0,0x93b7,0x1908,512,512,0,0,1,1,0));self.assertEqual(struct.unpack_from('<I',b,64)[0],65536)
  for i in range(4096):
   o=68+16*i;self.assertEqual(b[o:o+8],bytes.fromhex('fcfdffffffffffff'));self.assertEqual(struct.unpack_from('<4H',b,o+8),(120*257,)*3+(65535,))
  e,v,d=self.inputs();d[:]=60;b=self.encode(e,v,d,calibration=[(0,0),(60,255)]);self.assertEqual(struct.unpack_from('<4H',b,76),(65535,)*4)
 def test_missing_mapping_and_bad_units(self):
  for kw in [dict(calibration=None),dict(source_units='mm/h'),dict(calibration=[(0,2),(60,2)]),dict(calibration=[(60,1),(0,2)]),dict(calibration=[(0,0),(60,256)])]:
   with self.assertRaises(UnsupportedTexture):self.encode(*self.inputs(),**kw)
 def test_unknown_refused_and_roi_isolation(self):
  e,v,d=self.inputs();v[0,0]=False
  with self.assertRaises(UnsupportedTexture):self.encode(e,v,d)
  self.assertEqual(len(self.encode(e,v,d,roi=(1,1,64,64))),65604)
  v[:]=False
  with self.assertRaises(UnsupportedTexture):self.encode(e,v,d)
 def test_dbz_unknown_and_range(self):
  e,v,d=self.inputs()
  for bad in [np.nan,61.]:
   d[0,0]=bad
   with self.assertRaises(UnsupportedTexture):self.encode(e,v,d)
 def test_nondetection_requires_separate_mapping(self):
  e,v,d=self.inputs();e[:]=False;d[:]=np.nan
  with self.assertRaises(UnsupportedTexture):self.encode(e,v,d,dry_gray=None)
  b=self.encode(e,v,d);self.assertEqual(struct.unpack_from('<4H',b,76),(0,0,0,65535))
 def test_artificial_ramp_and_npz(self):
  for n in [16,32]:
   t=synthetic_ramp(n);self.assertEqual(t.marker,'ARTIFICIAL-NOT-WEATHER');self.assertEqual(len(t.body),65604)
  with tempfile.TemporaryDirectory() as tmp:
   e,v,d=self.inputs();p=Path(tmp)/'f.npz';np.savez(p,echo=e,valid=v,dbz=d)
   self.assertEqual(encode_npz(p,calibration=[(0,0),(60,240)],source_units='dBZ',dry_gray=0),self.encode(e,v,d))

if __name__=='__main__':unittest.main()
