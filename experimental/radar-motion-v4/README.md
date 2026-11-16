# Experimental next-hour precipitation radar (v4)

**Experimental, not a release.** This adds RainViewer observation tiles and a local motion estimate for selected mainland China z7 tiles in Apple Weather's one-hour precipitation map. It does **not** enable Apple's officially unsupported mainland next-hour map: diagonal hatching and the unavailable-forecast message may remain. The source's data and timing are independent of Apple's region support.

## What it does

- Reuses public RainViewer historical tiles, decodes the official palette, and estimates translation from the last three frames. Motion is accepted only after an independent 10-minute holdout improves on persistence; no echo/no reliable motion freezes the latest observation and labels it `persistence`.
- If the RainViewer area is genuinely unknown, it may fall back to Rainbow `precip-global` with the required `RAINBOW_API_KEY`. Missing data are not fabricated as zero precipitation.
- Encodes a 512x512 ASTC 8x8 KTX1 tile (65,604 bytes). The grayscale mapping is **temporary** `clip((dBZ-5)/50*255)` and is **not** Apple's calibrated physical rain-intensity scale. The result is an experimental display; no validated snow palette, rain-rate calibration, long-range model skill, or full-resolution meteorological fidelity is claimed.
- The Surge script only intercepts `precipitationRadarMap`, z7 / scale2, and a checked set of 101 whole-mainland tiles. Other areas, edge tiles, scales, zooms and Apple weather products pass through unchanged. Source data require visible RainViewer/Rainbow attribution in a distribution; a response header alone does not satisfy a user-facing attribution requirement.

## Scope and limits

Apple [lists next-hour precipitation maps](https://support.apple.com/en-us/105038) only for Australia, Ireland, Japan, the UK and US; 12-hour maps work in most regions. On-device testing showed diagonal hatching on mainland China's next-hour layer even with both local weather and radar modules off. Tokyo's next-hour layer and mainland China's 12-hour layer had no hatching. Apple draws a separate `HashOverlay` from GeoJSON coverage and regional support state. Its exact coverage endpoint and the client's live `supportedRegion` decision remain unknown; **this project does not alter or fabricate them**. The hatching cannot currently be removed safely in native Weather. Do not rebrand this as an unhatched next-hour map.

Forecast skill is site/weather dependent. A synthetic 10-minute holdout and one real event passing it do not establish mainland-wide +60-minute skill. A frozen echo may linger, and partial coverage stays unknown. The free RainViewer API has usage limits and no SLA; Rainbow has its own plan and attribution conditions. No server/API key is bundled.

## Self-deployment (personal test only)

Requirements: Python 3 with NumPy and Pillow, Arm `astcenc` (tested 5.2.0), Node 22+, an HTTPS reverse proxy and a Cloudflare Worker. Set `RAINBOW_API_KEY`, `VPS_TOKEN`, and writable `RADAR_CACHE` **privately** on your VPS. Start `hybrid_gateway.py`; it listens only on `127.0.0.1:8769`. Proxy only `/v4/radar` over your own HTTPS origin and forward its `Authorization` header. Never publish your tokens or cache.

Deploy `edge/worker.mjs` and `edge/v4-handler.mjs` to your own Worker with `VPS_ORIGIN=https://your-origin.example` and `VPS_TOKEN` as a Secret matching the backend. Do not use the author's live test Worker. `edge/build.mjs` generates the device script and module for your own Worker:

```sh
cd experimental/radar-motion-v4/edge
RADAR_ENDPOINT=https://your-worker.example/v4/radar \
RADAR_SCRIPT_URL=https://your-worker.example/test-assets/wk3-radar-motion-v4.surge.js \
node build.mjs
```

The builder writes `assets.mjs` as well as the script and module. Deploy `edge/worker.mjs`, `edge/v4-handler.mjs`, and generated `edge/assets.mjs` together (or use your existing Wrangler with `edge/wrangler.jsonc`); set `VPS_ORIGIN` and Secret `VPS_TOKEN` before use. Then install the generated `.sgmodule` in Surge. Keep the existing ordinary weather module enabled; disable any older radar-overlay module with the same HTTP-request pattern. Do not enable this experimental rule for all zoom levels. The self-deployment wiring and app UI still need device acceptance on your own account.

## Verification

```sh
python3 -m unittest test_hybrid nowcast.test_nowcast -v
cd quality && python3 -m unittest test_standard_astc -v
cd ../edge
RADAR_ENDPOINT=https://test-worker.example/v4/radar \
RADAR_SCRIPT_URL=https://test-worker.example/test-assets/wk3-radar-motion-v4.surge.js node build.mjs
node --test v4.test.mjs worker.test.mjs
```

The test suite uses synthetic/fixed fixtures. Real deployment on the author's temporary Worker previously returned 200/65,604-byte KTX and passed actual Surge binary output; that evidence does not validate any independently deployed Worker or eliminate Apple's hatching.

**Not included:** private HAR/cookies, real radar PNG/KTX caches, personal VPS/Worker address, keys, production configurations, and Apple's GeoJSON coverage.
