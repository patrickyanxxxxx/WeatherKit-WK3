// Artificial texture probe only. NOT meteorological data.
// Restricted to one Benxi tile and one rolling radar frame; no network calls.
(() => {
    try {
        const url = new URL($request.url);
        const q = url.searchParams;
        if (url.protocol !== "https:" || url.hostname !== "weather-map2.apple.com"
            || url.pathname !== "/v1/mapOverlay/precipitationRadarMap"
            || q.get("z") !== "7" || q.get("x") !== "108" || q.get("y") !== "47"
            || q.get("scale") !== "2" || ($request.method ?? "GET") !== "GET") return $done({});
        const now = Date.now();
        const frame = Date.parse(q.get("frameTime") ?? "");
        // The captured series can use either :00/:10 or :05/:15 boundaries.
        // Select its latest past frame; do not assume a fixed UTC phase.
        if (!Number.isFinite(frame) || frame % 300000 !== 0
            || frame > now || now - frame >= 600000) return $done({});
        const target = frame;
        const bytes = new Uint8Array(65604);
        bytes.set([171,75,84,88,32,49,49,187,13,10,26,10]);
        const view = new DataView(bytes.buffer);
        [0x04030201,0,1,0,0x93b7,0x1908,512,512,0,0,1,1,0]
            .forEach((value, i) => view.setUint32(12 + i * 4, value, true));
        view.setUint32(64, 65536, true);
        // ASTC LDR void-extent blocks: exact constant UNORM16 colors.
        for (let y = 0; y < 64; y++) {
            for (let x = 0; x < 64; x++) {
                let level = [48,96,144,192][Math.floor(x / 16)];
                if (Math.abs(x - y) <= 1 || Math.abs(x + y - 63) <= 1) level = 224;
                if (x < 2 || y < 2 || x >= 62 || y >= 62) level = 255;
                const offset = 68 + (y * 64 + x) * 16;
                bytes.set([252,253,255,255,255,255,255,255], offset);
                for (let c = 0; c < 3; c++) view.setUint16(offset + 8 + c * 2, level * 257, true);
                view.setUint16(offset + 14, 65535, true);
            }
        }
        console.log("[MAP TEST — 人工图案，非降水] z7/x108/y47 frame=" + new Date(target).toISOString() + " KTX=" + bytes.length);
        return $done({response:{status:200,headers:{"Content-Type":"image/ktx","Cache-Control":"no-store","X-WeatherKit-Map-Test":"ARTIFICIAL-NOT-WEATHER"},body:bytes}});
    } catch (error) {
        console.log("[MAP TEST] passthrough: " + (error?.message ?? String(error)));
        return $done({});
    }
})();
