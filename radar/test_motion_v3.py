import unittest
from unittest.mock import patch
import numpy as np
import motion_v3 as m


def frame(x):
    s=np.zeros((32,64));s[10:20,x:x+6]=20
    return dict(signal=s,echo=s>0,valid=np.ones(s.shape,bool),dbz=np.where(s>0,s,np.nan))

class SelectionTests(unittest.TestCase):
    def test_forecast_target_never_used(self):
        fs=[frame(x) for x in [4,6,8,10]];seen=[]
        def estimate(frames,times):
            seen.append(list(times))
            return dict(status='estimated',vx=.2,vy=0.,diagnostics={
                'global_velocity':[.2,0.],'accepted_global_pairs':1})
        with patch.object(m.v2,'estimate',side_effect=estimate):
            v=m.estimate(fs,[0,600,1200,1800])
        self.assertEqual(seen,[[0,600,1200],[600,1200,1800]])
        self.assertEqual(v['diagnostics']['validation_target'],1800)
        self.assertEqual(v['diagnostics']['selected'],'global')
    def test_stationary_case_chooses_explicit_persistence(self):
        fs=[frame(5)]*4
        with patch.object(m.v2,'estimate',return_value=dict(status='unknown',diagnostics={})):
            v=m.estimate(fs,[0,600,1200,1800])
        self.assertEqual(v['status'],'persistence')
        p=m.predict(fs[-1],v,60)
        np.testing.assert_array_equal(p[0],fs[-1]['echo'])
    def test_no_echo_is_unknown_not_dry_forecast(self):
        fs=[frame(5)]*4
        for f in fs:f['signal'][:]=0;f['echo'][:]=False;f['dbz'][:]=np.nan
        with patch.object(m.v2,'estimate',return_value=dict(status='unknown',diagnostics={})):
            v=m.estimate(fs,[0,600,1200,1800])
        self.assertEqual(v['status'],'unknown')
        self.assertFalse(m.predict(fs[-1],v,60)[1].any())
    def test_same_lead_history(self):
        fs=[frame(5)]*9;times=np.arange(9)*600;seen=[]
        def estimate(frames,times):
            seen.append(list(times));return dict(status='unknown',diagnostics={})
        with patch.object(m.v2,'estimate',side_effect=estimate):
            v=m.estimate(fs,times,validation_lead_minutes=60)
        self.assertEqual(seen,[[0,600,1200],[3600,4200,4800]])
        self.assertEqual(v['diagnostics']['validation_origin'],1200)
        self.assertEqual(v['diagnostics']['validation_target'],4800)
        self.assertEqual(v['diagnostics']['validation_lead_minutes'],60)
        v=m.estimate(fs[:8],times[:8],validation_lead_minutes=60)
        self.assertEqual(v['status'],'unknown')
        self.assertEqual(v['diagnostics']['reason'],'insufficient_same_lead_history')
    def test_input_errors(self):
        for times in [[0,600,1200],[0,600,600,1800],[0,600,np.nan,1800]]:
            with self.assertRaises(ValueError):m.estimate([frame(5)]*len(times),times)

if __name__=='__main__':unittest.main()
