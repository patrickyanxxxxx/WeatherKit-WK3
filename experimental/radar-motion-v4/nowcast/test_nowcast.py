import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from nowcast import Engine, estimate, translate

T = 100000
META = dict(host='synthetic', radar=dict(past=[dict(time=T-1200+i*600, path='/v2/radar/'+str(i)) for i in range(3)]))


def echo(shape=(256, 256)):
    y, x = np.indices(shape)
    return (45*np.exp(-((x-110)**2/150+(y-105)**2/80))
            + 30*np.exp(-((x-145)**2/65+(y-150)**2/110))).astype(np.float32)


def history(dx=4, dy=-3):
    d = echo()
    v = np.ones(d.shape, bool)
    return [translate(d, v, i*dx, i*dy) for i in range(3)]


class TestNowcast(unittest.TestCase):
    def test_signed_motion_and_independent_holdout(self):
        for dx, dy in [(4, -3), (-5, 2), (0, 5)]:
            info = estimate(history(dx, dy), [T-1200, T-600, T])
            self.assertEqual(info['status'], 'estimated', info)
            np.testing.assert_allclose(info['dxdy'], np.array([dx, dy])*.2, atol=.04)
            self.assertGreater(info['holdout']['skill'], .9)

    def test_unknown_and_stationary_never_claim_advection(self):
        d = np.full((256, 256), -32.)
        v = np.ones(d.shape, bool)
        for frames in [[(d, v)]*3, [(echo(), v)]*3, [(echo(), ~v)]*3]:
            info = estimate(frames, [T-1200, T-600, T])
            self.assertEqual(info['status'], 'unknown')
            self.assertIsNone(info['dxdy'])

    def test_no_wrap_and_exact_missing(self):
        d = np.arange(64, dtype=float).reshape(8, 8)
        v = np.ones_like(d, bool)
        v[2, 2] = False
        a, known = translate(d, v, .5, 0)
        self.assertFalse(known[:, 0].any())
        self.assertFalse(known[2, 2])
        self.assertFalse(known[2, 3])
        self.assertTrue(np.isnan(a[~known]).all())
        a, known = translate(d, v, 20, 0)
        self.assertFalse(known.any())
        a, known = translate(d, v, 0, 0)
        np.testing.assert_array_equal(known, v)
        np.testing.assert_array_equal(a[v], d[v])

    def test_engine_concurrency_and_no_intensity_fade(self):
        calls = []
        guard = threading.Lock()
        frames = history()
        observed = np.full((256, 256), 42., np.float32)
        def fetch(m, f, tile):
            with guard:
                calls.append((f['time'], tile))
            return frames[(f['time']-(T-1200))//600] if tile[0] == 6 else (observed, np.ones_like(observed, bool))
        with ThreadPoolExecutor(12) as pool, ThreadPoolExecutor(16) as callers:
            engine = Engine(pool, clock=lambda: T+60)
            jobs = [callers.submit(engine.predict, META, (7, 100, 50), T+600*(i%8), fetch) for i in range(16)]
            results = [j.result() for j in jobs]
        self.assertEqual(len(calls), 12)  # 3 parents and latest 9 neighbors, once
        for d, v, info in results:
            self.assertEqual(info['model'], 'advection')
            self.assertTrue(v.all())
            np.testing.assert_allclose(d, 42.)
            self.assertEqual(info['source_time'], T)

    def test_parent_motion_shared_by_adjacent_tiles(self):
        calls = []
        frames = history()
        def fetch(m, f, tile):
            calls.append(tile)
            return frames[(f['time']-(T-1200))//600] if tile[0] == 6 else (echo(), np.ones((256, 256), bool))
        engine = Engine(clock=lambda: T)
        try:
            engine.predict(META, (7, 100, 50), T+600, fetch)
            engine.predict(META, (7, 101, 51), T+600, fetch)
        finally:
            engine.close()
        self.assertEqual(sum(t[0] == 6 for t in calls), 3)

    def test_rate_limit_and_stale_fallback(self):
        class RateLimitPending(Exception):
            pass
        calls = []
        def fetch(m, f, tile):
            calls.append(tile)
            if tile[0] == 6:
                raise RateLimitPending('do not echo secret URL')
            return echo(), np.ones((256, 256), bool)
        age = [0]
        engine = Engine(clock=lambda: T+age[0])
        try:
            d, v, info = engine.predict(META, (7, 100, 50), T+4800, fetch)
            self.assertEqual(info['model'], 'persistence')
            self.assertEqual(info['reason'], 'rate_limited')
            self.assertTrue(v.all())
            np.testing.assert_array_equal(d, echo())
            age[0] = 1800
            d, v, info = engine.predict(META, (7, 100, 50), T+600, fetch)
            self.assertFalse(v.any())
            self.assertEqual(info['status'], 'unavailable')
            self.assertEqual(len(calls), 4)
        finally:
            engine.close()

    def test_missing_advection_freezes_center(self):
        frames = history()
        def fetch(m, f, tile):
            if tile[0] == 6:
                return frames[(f['time']-(T-1200))//600]
            return echo(), np.full((256, 256), tile == (7, 100, 50), bool)
        engine = Engine(clock=lambda: T)
        try:
            d, v, info = engine.predict(META, (7, 100, 50), T+3600, fetch)
        finally:
            engine.close()
        self.assertEqual(info['model'], 'persistence')
        self.assertEqual(info['reason'], 'advection_unknown_freeze_latest_center')
        self.assertTrue(v.all())
        np.testing.assert_array_equal(d, echo())

    def test_partial_coverage_remains_unknown(self):
        observed = echo()
        mask = np.ones((256, 256), bool)
        mask[:, :60] = False
        def fetch(m, f, tile):
            if tile[0] == 6:
                return np.full((256, 256), -32.), np.ones((256, 256), bool)
            return observed, mask
        engine = Engine(clock=lambda: T)
        try:
            d, v, info = engine.predict(META, (7, 100, 50), T+600, fetch)
        finally:
            engine.close()
        self.assertEqual(info['model'], 'persistence')
        np.testing.assert_array_equal(v, mask)
        self.assertTrue(np.isnan(d[~v]).all())

    def test_cache_is_bounded(self):
        from nowcast.engine import _Cache
        cache = _Cache(limit=16)
        for i in range(50):
            self.assertEqual(cache.get(i, lambda: i), i)
        self.assertEqual(len(cache.entries), 16)

    def test_invalid_targets(self):
        engine = Engine(clock=lambda: T)
        try:
            for target in [T-1, T+4801, float('nan')]:
                with self.assertRaises(ValueError):
                    engine.predict(META, (7, 100, 50), target, None)
        finally:
            engine.close()


if __name__ == '__main__':
    unittest.main()
