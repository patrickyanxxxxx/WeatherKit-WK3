import unittest
import numpy as np
from PIL import Image
from mosaic import load_mosaic,mosaic_plan
class Fake:
 def coverage(self,z,x,y,size=256):return Image.fromarray(np.zeros((size,size,4),np.uint8),'RGBA')
 def tile(self,m,f,z,x,y,size=256):
  # Exact official palette value for a visible echo; opaque valid colour.
  from backtest import palette
  from pathlib import Path
  colors=palette(Path(__file__).parent/'palette.csv');rgba=next(k for k,v in colors.items() if v==[x] and k[3]>0)
  a=np.empty((size,size,4),np.uint8);a[:]=rgba;return Image.fromarray(a,'RGBA')
class Tests(unittest.TestCase):
 def test_wrap(self):
  p=mosaic_plan((2,0,1));self.assertIn((2,3,1),[c['tile']for c in p['cells']]);self.assertEqual(p['crop'],(256,512,256,512))
 def test_mosaic(self):
  f=load_mosaic(Fake(),{}, {'time':0},(2,0,1));self.assertEqual(f['signal'].shape,(768,768));self.assertTrue(f['valid'].all());self.assertTrue(np.all(f['dbz'][256:512,:256]==3));self.assertTrue(np.all(f['dbz'][256:512,256:512]==0))
 def test_poles(self):
  f=load_mosaic(Fake(),{}, {'time':0},(2,0,0));self.assertFalse(f['valid'][:256].any());self.assertTrue(f['valid'][256:512].all())
if __name__=='__main__':unittest.main()
