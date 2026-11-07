"""Deterministic, analytic synthetic checks; no observed future data or scipy.
Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_motion_v2
"""
import unittest
import numpy as np
from motion_v2 import estimate as estimate_seconds, predict as predict_tuple


def estimate(frames, minutes):
    # Analytic scenes use minutes; the production API takes UTC Unix seconds.
    return estimate_seconds(frames, 1700000000.+60*np.asarray(minutes))


def predict(source, velocity, lead):
    # Named access is convenient in assertions; exercise the public tuple API.
    if 'status' not in velocity:
        velocity = dict(velocity, status='estimated')
    result = predict_tuple(source, velocity, lead)
    assert isinstance(result, tuple) and len(result) == 3
    echo, valid, dbz = result
    return dict(echo=echo, occurrence=echo, valid=valid, dbz=dbz)


def texture(x, y, seed=7):
    rng = np.random.default_rng(seed)
    out = np.zeros(x.shape)
    for _ in range(28):
        cx, cy = rng.uniform(18, 110, 2)
        sx, sy = rng.uniform(2., 7., 2)
        out += rng.uniform(5, 30)*np.exp(-.5*((x-cx)/sx)**2-.5*((y-cy)/sy)**2)
    return out


def frame(signal, valid=None):
    if valid is None:
        valid = np.ones(signal.shape, bool)
    echo = (signal > 2) & valid
    return dict(signal=np.where(valid, signal, 0), valid=valid, echo=echo,
                dbz=np.where(echo, signal, np.nan))


def translation(t, dx=.4, dy=-.7, seed=7):
    y, x = np.indices((128, 128), dtype=float)
    return frame(texture(x-t*dx, y-t*dy, seed))


class MotionTests(unittest.TestCase):
    def test_fractional_translation_and_irregular_intervals(self):
        times = [0., 2., 5.]
        result = estimate([translation(t) for t in times], times)
        self.assertEqual(result['status'], 'estimated')
        u = float(np.median(result['vx'][25:100, 25:100]))
        v = float(np.median(result['vy'][25:100, 25:100]))
        print('fractional velocity', u, v, 'target', .4, -.7)
        self.assertAlmostEqual(u, .4, delta=.10)
        self.assertAlmostEqual(v, -.7, delta=.10)
        prediction = predict(translation(5), result, 3)
        truth = translation(8)
        mask = prediction['valid']
        adv_error = np.mean(prediction['occurrence'][mask] != truth['echo'][mask])
        per_error = np.mean(translation(5)['echo'][mask] != truth['echo'][mask])
        print('translation occurrence errors advected/persistence', adv_error, per_error)
        self.assertLess(adv_error, per_error*.65)
        self.assertEqual(result['diagnostics']['frames_used'], 3)
        self.assertEqual(len(result['diagnostics']['pairs']), 3)

    def test_two_independent_motions(self):
        y, x = np.indices((144, 288), dtype=float)
        def scene(t):
            left = texture(x-.9*t, y-.25*t, 7)
            right = texture(x-158+.8*t, y-.45*t, 23)
            return frame(left+right)
        result = estimate([scene(t) for t in [0, 2, 4]], [0, 2, 4])
        left = (slice(30, 100), slice(30, 95))
        right = (slice(30, 100), slice(190, 255))
        ul, ur = float(np.median(result['vx'][left])), float(np.median(result['vx'][right]))
        vl, vr = float(np.median(result['vy'][left])), float(np.median(result['vy'][right]))
        print('independent velocities', (ul, vl), (ur, vr))
        self.assertAlmostEqual(ul, .9, delta=.22)
        self.assertAlmostEqual(ur, -.8, delta=.22)
        self.assertAlmostEqual(vl, .25, delta=.22)
        self.assertAlmostEqual(vr, .45, delta=.22)
        # Interpolation must not leave block seams or discontinuous velocities.
        self.assertLess(np.max(np.abs(np.diff(result['vx'], axis=1))), .20)

    def test_buffer_crop_and_no_wrap(self):
        a = np.zeros((32, 80))
        a[10:20, 8:14] = 20
        a[:, -1] = 30
        result = predict(frame(a), dict(vx=np.full(a.shape, 1.), vy=np.zeros(a.shape)), 20)
        # Buffer echo reaches an interior crop; the right edge cannot reappear left.
        self.assertTrue(result['occurrence'][12:18, 28:34].all())
        self.assertFalse(result['occurrence'][:, :20].any())
        self.assertFalse(result['valid'][:, :20].any())
        self.assertTrue(result['valid'][:, 20:].all())
        self.assertFalse(result['occurrence'][:, 34:].any())

    def test_unknown_masks_and_dbz_independent(self):
        a = np.full((24, 24), 20.)
        valid = np.ones(a.shape, bool)
        valid[8:12, 8:12] = False
        source = frame(a, valid)
        source['dbz'][16, 16] = np.nan
        result = predict(source, dict(vx=.5, vy=0.), 1)
        self.assertFalse(result['valid'][:, 0].any())
        self.assertFalse(result['valid'][8:12, 8:13].any())
        self.assertTrue(result['valid'][16, 16])
        self.assertTrue(result['occurrence'][16, 16])
        self.assertTrue(np.isnan(result['dbz'][16, 16]))
        self.assertAlmostEqual(result['dbz'][4, 4], 20.)
        identity = predict(source, dict(vx=0., vy=0.), 0)
        np.testing.assert_array_equal(identity['valid'], valid)
        np.testing.assert_array_equal(identity['echo'], source['echo'])

    def test_masked_translation(self):
        y, x = np.indices((128, 128))
        valid = (x > 8) & (x < 119) & ~((x > 55) & (x < 65))
        frames = [translation(t) for t in [0, 2, 4]]
        for f in frames:
            f['valid'] = valid.copy()
            # Invalid values must never influence correlation.
            f['signal'][~valid] = 1e8
        result = estimate(frames, [0, 2, 4])
        print('masked global velocity', result['diagnostics']['global_velocity'])
        self.assertEqual(result['status'], 'estimated')
        self.assertAlmostEqual(float(np.median(result['vx'][25:100, 25:100])), .4, delta=.13)
        self.assertAlmostEqual(float(np.median(result['vy'][25:100, 25:100])), -.7, delta=.13)

    def test_insufficient_signal_and_unrelated_frames(self):
        for signal in [np.zeros((64, 64)), np.full((64, 64), 20.)]:
            f = frame(signal)
            result = estimate([f, f, f], [0, 5, 10])
            self.assertEqual(result['status'], 'unknown')
            self.assertFalse(result['vx'].any())
            self.assertFalse(result['vy'].any())
            self.assertFalse(result['quality'].any())
        rng = np.random.default_rng(80)
        result = estimate([frame(rng.uniform(0, 40, (64, 64))) for _ in range(3)], [0, 1, 2])
        self.assertEqual(result['status'], 'unknown')
        missing = frame(np.ones((64, 64)), np.zeros((64, 64), bool))
        result = estimate([missing, missing], [0, 1])
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(predict(missing, result, 10)['valid'].any())

    def test_last_three_only_and_input_errors(self):
        frames = [translation(t) for t in range(4)]
        first = estimate(frames, range(4))
        frames[0]['signal'][:] = 1e6
        second = estimate(frames, range(4))
        np.testing.assert_array_equal(first['vx'], second['vx'])
        for times in ([0, 0], [1, 0], [0, np.nan]):
            with self.assertRaises(ValueError):
                estimate(frames[:2], times)
        with self.assertRaises(ValueError):
            predict(frames[-1], first, -1)

    def test_temporal_outlier_does_not_dominate(self):
        frames = [translation(0, seed=99), translation(2), translation(4)]
        result = estimate(frames, [0, 2, 4])
        self.assertEqual(result['status'], 'estimated')
        print('outlier global velocity', result['diagnostics']['global_velocity'])
        self.assertAlmostEqual(float(np.median(result['vx'][25:100, 25:100])), .4, delta=.18)
        self.assertAlmostEqual(float(np.median(result['vy'][25:100, 25:100])), -.7, delta=.18)

    def test_stationary_echo_and_growth_failure_mode(self):
        f = translation(0)
        result = estimate([f, f, f], [0, 2, 4])
        self.assertEqual(result['status'], 'estimated')
        self.assertLess(np.max(np.abs(result['vx'])), .02)
        prediction = predict(f, result, 5)
        truth = frame(np.zeros((128, 128)))  # All echoes dissipate after origin.
        self.assertTrue(prediction['occurrence'].any())
        self.assertFalse(truth['echo'].any())
        # Explicitly document the limitation: advection cannot model dissipation.
        self.assertGreater(np.count_nonzero(prediction['echo'] & ~truth['echo']), 100)


    def test_unknown_motion_and_explicit_persistence(self):
        f = translation(0)
        unknown = dict(vx=0., vy=0., status='unknown')
        echo, valid, dbz = predict_tuple(f, unknown, lead_minutes=5)
        self.assertFalse(valid.any())
        self.assertFalse(echo.any())
        self.assertTrue(np.isnan(dbz).all())
        self.assertEqual(unknown['diagnostics']['prediction_fallback'], 'unknown_motion_masked')
        explicit = dict(vx=100., vy=100., status='persistence')
        echo, valid, dbz = predict_tuple(f, explicit, lead_minutes=5)
        np.testing.assert_array_equal(valid, f['valid'])
        np.testing.assert_array_equal(echo, f['echo'])
        self.assertEqual(explicit['diagnostics']['prediction_fallback'], 'explicit_persistence')

    def test_spatial_velocity_characteristics(self):
        y, x = np.indices((40, 80), dtype=float)
        source = frame(10+x)
        source['dbz'] = 10+x
        # dx/dt=k*x has analytic backward trajectory x0=x*exp(-k*lead).
        k, lead = .05, 6.
        echo, valid, dbz = predict_tuple(source, dict(vx=k*x, vy=0., status='estimated'), lead)
        expected = 10+x*np.exp(-k*lead)
        self.assertTrue(valid.all())
        self.assertLess(np.max(np.abs(dbz-expected)), .03)
        self.assertTrue(echo.all())


    def test_output_region_matches_full_mosaic(self):
        y, x = np.indices((40, 80), dtype=float)
        source = frame(10+x)
        source['dbz'] = 10+x
        velocity = dict(vx=.03*x, vy=.02*y, status='estimated')
        full = predict_tuple(source, velocity, 6)
        local = predict_tuple(source, velocity, 6, output_region=(10,30,20,60))
        for a,b in zip(full,local):
            np.testing.assert_array_equal(a[10:30,20:60], b)
        missing=predict_tuple(source, {'status':'unknown'}, 6, output_region=(10,30,20,60))
        self.assertEqual(missing[1].shape,(20,40))
        self.assertFalse(missing[1].any())


if __name__ == '__main__':
    unittest.main(verbosity=2)
