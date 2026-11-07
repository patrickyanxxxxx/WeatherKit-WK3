import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { buildRadarModules, buildRadarScripts, defaultScriptBase, radarPlatforms, renderRadarModule } from '../scripts/build-wk3-radar.mjs';
import { renderWK3Module } from '../scripts/build-wk3-modules.mjs';
import { renderLocalModule } from '../scripts/build-egern-module.mjs';

const version = '3.3.6';
const frame = new Date(Math.floor(Date.now() / 300000) * 300000).toISOString();
const epoch = Date.parse(frame) / 1000;
const appleURL = `https://weather-map2.apple.com/v1/mapOverlay/precipitationRadarMap?z=1&x=0&y=1&scale=2&frameTime=${encodeURIComponent(frame)}`;
const endpoint = 'https://radar.example.org/v1/radar';
const headers = {
    'Content-Type': 'image/ktx', 'Cache-Control': 'no-store', 'X-WK3-Nowcast': 'EXPERIMENTAL-RADAR-ECHO',
    'X-WK3-Valid-Time': String(epoch), 'X-WK3-Origin': String(epoch - 300), 'X-WK3-Source-Valid-Time': String(epoch),
};
const validKTX = () => {
    const b = new Uint8Array(65604);
    b.set([171,75,84,88,32,49,49,187,13,10,26,10]);
    const fields = [0x04030201,0,1,0,0x93b7,0x1908,512,512,0,0,1,1,0,65536];
    const view = new DataView(b.buffer);
    fields.forEach((n, i) => view.setUint32(12 + i * 4, n, true));
    return b;
};

function moduleRoutes(platform, text) {
    if (platform === 'surge') return [...text.matchAll(/^([^\n=]+) = type=http-(request|response), pattern=("[^"\n]+"|[^,\n]+),/gm)].map(([, name, stage, pattern]) => ({ name: name.trim(), stage, pattern: pattern.replace(/^"|"$/g, '') }));
    if (platform === 'loon') return [...text.matchAll(/^http-(request|response) (\S+).*tag=([^,\n]+)/gm)].map(([, stage, pattern, name]) => ({ name, stage, pattern }));
    if (platform === 'stash') return [...text.matchAll(/^    - match: '([^'\n]+)'\n      name: ([^\n]+)\n      type: (request|response)/gm)].map(([, pattern, name, stage]) => ({ name, stage, pattern }));
    return [...text.matchAll(/^- http_(request|response):\n    name: ([^\n]+)[\s\S]*?\n    match: '([^'\n]+)'/gm)].map(([, stage, name, pattern]) => ({ name, stage, pattern }));
}

for (const platform of Object.keys(radarPlatforms)) test(`${platform} standalone Radar overlays ordinary weather without duplicate routes or settings`, () => {
    const plain = platform === 'egern' ? renderLocalModule({ version }) : renderWK3Module(platform, version);
    const radar = renderRadarModule(platform, version);
    assert.doesNotMatch(plain, /weather-map2\.apple\.com|wk3-radar-/);
    assert.doesNotMatch(radar, /weatherkit\.apple\.com|WeatherKit\.api\.|wk3-arguments|ColorfulClouds|QWeather|releases\/download/);
    assert.equal(radar.split(defaultScriptBase).length - 1, 1);
    assert.match(radar, /3\.3\.6-test-radar-cloud\.2/);
    assert.match(radar, /binary-body-mode=1|binary-mode: true|binary_body: true/);
    assert.match(radar, /RADAR_ENDPOINT/);
    assert.doesNotMatch(radar, /127\.0\.0\.1|__WK3_/);
    const weatherRoutes = moduleRoutes(platform, plain), radarRoutes = moduleRoutes(platform, radar);
    assert.equal(weatherRoutes.length, 5);
    assert.equal(radarRoutes.length, 1);
    assert.equal(radarRoutes[0].name, 'WK3.Experimental.Radar');
    assert.equal(radarRoutes[0].stage, 'request');
    assert.equal(new Set([...weatherRoutes, ...radarRoutes].map(r => r.name)).size, 6);
    const cases = [
        [appleURL, 'request', 'WK3.Experimental.Radar'],
        [appleURL, 'response', null],
        ['https://weatherkit.apple.com/api/v2/availability/zh', 'response', 'WeatherKit.api.availability.response'],
        ['https://weatherkit.apple.com/api/v2/airQualityScale/zh/EPA_NowCast', 'request', 'WeatherKit.api.airQualityScale.request'],
        ['https://weatherkit.apple.com/api/v2/weatherAlerts?ids=abc-123456789', 'request', 'WeatherKit.api.weatherAlerts.html.request'],
        ['https://weatherkit.apple.com/api/v2/weatherAlerts?ids=wk3_example', 'request', 'WeatherKit.api.weatherAlerts.api.request'],
        ['https://weatherkit.apple.com/api/v2/weatherAlerts?ids=31,121', 'request', 'WeatherKit.api.weatherAlerts.api.request'],
        ['https://weatherkit.apple.com/api/v3/weather/zh/31/121?dataSets=airQuality,forecastNextHour,weatherAlerts', 'response', 'WeatherKit.api.weather.response'],
        [appleURL.replace('precipitationRadarMap', 'precipitationForecastByFrameTime'), 'request', null],
        [appleURL.replace('weather-map2.apple.com', 'weather-map2.apple.com.evil.example'), 'request', null],
        ['https://radar.example.org/v1/radar?z=1', 'request', null],
    ];
    for (const modules of [[weatherRoutes, radarRoutes], [radarRoutes, weatherRoutes]]) for (const [url, stage, expected] of cases) {
        const matches = modules.flat().filter(r => r.stage === stage && new RegExp(r.pattern).test(url));
        assert.deepEqual(matches.map(r => r.name), expected ? [expected] : [], `${stage} ${url}`);
        if (new URL(url).hostname === 'weatherkit.apple.com') assert.deepEqual(matches, weatherRoutes.filter(r => r.stage === stage && new RegExp(r.pattern).test(url)));
    }
    const hosts = text => platform === 'stash' ? [...text.matchAll(/^    - ([\w.-]+\.apple\.com)$/gm)].map(m => m[1]) : platform === 'egern' ? [...text.split('\nmitm:\n')[1].matchAll(/^    - ([\w.-]+)$/gm)].map(m => m[1]) : text.match(/^hostname = (?:%APPEND% )?(.+)$/m)[1].split(/,\s*/);
    assert.deepEqual(hosts(plain), ['weatherkit.apple.com']);
    assert.deepEqual(hosts(radar), ['weather-map2.apple.com']);
    assert.deepEqual([...new Set([...hosts(plain), ...hosts(radar)])].sort(), ['weather-map2.apple.com', 'weatherkit.apple.com']);
    if (platform === 'surge') assert.match(radar, /hostname = %APPEND% weather-map2\.apple\.com/);
    if (platform === 'egern') {
        const get = name => JSON.parse(radar.match(new RegExp(`^${name}: (.*)$`, 'm'))[1]);
        assert.deepEqual(Object.keys(get('env_schema')), ['RADAR_ENDPOINT']);
        assert.deepEqual(get('compat_arguments'), { RADAR_ENDPOINT: '' });
        assert.equal(get('env_schema').RADAR_ENDPOINT.default_value, '');
        assert.match(radar, /- domain:\n        match: weather-map2\.apple\.com\n    - protocol:\n        match: udp\n    - dest_port:\n        match: '443'\n    policy: REJECT/);
    } else if (platform === 'stash') assert.match(radar, /^  - 'AND,\(\(DOMAIN,weather-map2\.apple\.com\),\(NETWORK,udp\),\(DST-PORT,443\)\),REJECT'$/m);
    else assert.equal((radar.match(/AND,\(\(DOMAIN,weather-map2\.apple\.com\),\(PROTOCOL,UDP\),\(DEST-PORT,443\)\),REJECT/g) ?? []).length, 1);
});

test('Radar files are separate; Quantumult X binary module is explicitly unsupported', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wk3-radar-'));
    try {
        assert.throws(() => renderRadarModule('quantumultx', version), /binary radar is not supported/);
        assert.throws(() => buildRadarModules({ version }), /--outdir/);
        buildRadarScripts({ outdir: dir });
        buildRadarModules({ outdir: dir, version });
        assert.equal(fs.readdirSync(dir).length, 8);
        assert.ok(!fs.readdirSync(dir).some(name => /quantumultx|snippet/i.test(name)));
        for (const [platform, ext] of Object.entries(radarPlatforms)) {
            assert.equal(fs.readFileSync(path.join(dir, `iRingo.WeatherKit.WK3.Radar.${ext}`), 'utf8'), renderRadarModule(platform, version));
            assert.match(fs.readFileSync(path.join(dir, `wk3-radar-${platform}-map.bundle.js`), 'utf8'), /EXPERIMENTAL-RADAR-ECHO/);
        }
    } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

async function run(platform, { argument = '', env = {}, responseHeaders = headers, data = validKTX(), url = appleURL } = {}) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wk3-radar-vm-'));
    try {
        const file = buildRadarScripts({ outdir: dir })[platform];
        const code = fs.readFileSync(file, 'utf8');
        const requests = [], done = [];
        const globals = {
            URL, Uint8Array, ArrayBuffer, DataView, TextEncoder, console: { log() {} },
            $argument: argument, $env: env, $request: { url, method: 'GET', headers: { Authorization: 'Bearer private', Cookie: 'private' } },
            $done: value => done.push(value), setTimeout, clearTimeout,
            $httpClient: { get: (options, callback) => { requests.push(options); callback(null, { status: 200, headers: responseHeaders }, data); } },
        };
        if (platform === 'egern') {
            globals.ctx = {
                request: globals.$request, env, http: { get: async (target, options) => {
                    requests.push({ url: target, ...options });
                    return { status: 200, headers: responseHeaders, arrayBuffer: async () => data.buffer };
                } }, respond: value => { done.push(value); return value; },
            };
            const script = new vm.Script(code.replace('export default async function(ctx)', 'async function execute(ctx)') + '\nexecute(ctx)');
            await script.runInNewContext(globals);
        } else {
            await new vm.Script(code).runInNewContext(globals);
            // Request callbacks are async; wait for their microtasks before inspecting $done.
            await new Promise(resolve => setImmediate(resolve));
        }
        return { requests, done };
    } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}

for (const platform of Object.keys(radarPlatforms)) test(`${platform} VM: user HTTPS endpoint only, strict marker/KTX/time, no Apple credentials`, async () => {
    const module = renderRadarModule(platform, version);
    const template = platform === 'stash' ? JSON.parse(module.match(/^      argument: '(.*)'$/m)[1]) : null;
    const argument = platform === 'surge' || platform === 'loon'
        ? module.match(/, argument=(.*)$/m)[1].replace(/\{\{\{RADAR_ENDPOINT\}\}\}|\{RADAR_ENDPOINT\}/g, endpoint)
        : platform === 'stash' ? JSON.stringify({ ...template, RADAR_ENDPOINT: endpoint }) : '';
    const env = platform === 'egern' ? { ...JSON.parse(module.match(/^compat_arguments: (.*)$/m)[1]), RADAR_ENDPOINT: endpoint } : {};
    const empty = await run(platform);
    assert.equal(empty.requests.length, 0);
    assert.equal(empty.done.length, platform === 'egern' ? 0 : 1);
    const ok = await run(platform, { argument, env });
    assert.equal(ok.requests.length, 1);
    assert.match(ok.requests[0].url, /^https:\/\/radar\.example\.org\/v1\/radar\?/);
    assert.deepEqual(Object.keys(ok.requests[0].headers), ['Accept']);
    assert.equal(ok.done.length, 1);
    assert.equal(platform === 'egern' ? ok.done[0].body.length : platform === 'stash' ? ok.done[0].response.body.length : ok.done[0].response.body.length, 65604);
    const badMarker = await run(platform, { argument, env, responseHeaders: { ...headers, 'X-WK3-Nowcast': 'wrong' } });
    assert.equal(badMarker.done.length, platform === 'egern' ? 0 : 1);
    if (platform !== 'egern') assert.equal(badMarker.done[0].response, undefined);
    const badKTX = await run(platform, { argument, env, data: new Uint8Array(65604) });
    assert.equal(badKTX.done.length, platform === 'egern' ? 0 : 1);
    const badTime = await run(platform, { argument, env, responseHeaders: { ...headers, 'X-WK3-Valid-Time': String(epoch + 300) } });
    assert.equal(badTime.done.length, platform === 'egern' ? 0 : 1);
    const badSource = await run(platform, { argument, env, responseHeaders: { ...headers, 'X-WK3-Source-Valid-Time': String(epoch - 600) } });
    assert.equal(badSource.done.length, platform === 'egern' ? 0 : 1);
    const wrongType = await run(platform, { argument, env, responseHeaders: { ...headers, 'Content-Type': 'image/png' } });
    assert.equal(wrongType.done.length, platform === 'egern' ? 0 : 1);
    if (platform !== 'egern') for (const result of [badKTX, badTime, badSource, wrongType]) assert.equal(result.done[0].response, undefined);
    const weather = await run(platform, { argument, env, url: 'https://weatherkit.apple.com/api/v3/weather/zh/31/121' });
    assert.equal(weather.requests.length, 0, 'weather/AQ/alerts stay in the existing local proxy scripts');
    const insecure = await run(platform, { argument: platform === 'stash' ? '{"RADAR_ENDPOINT":"http://radar.example.org/v1/radar"}' : 'RADAR_ENDPOINT=http://radar.example.org/v1/radar', env: platform === 'egern' ? { RADAR_ENDPOINT: 'http://radar.example.org/v1/radar' } : {} });
    assert.equal(insecure.requests.length, 0);
    const otherRoute = await run(platform, { argument, env, url: appleURL.replace('weather-map2.apple.com', 'evil.example.org') });
    assert.equal(otherRoute.requests.length, 0);
});

test('actual Radar CLI builds exactly eight files without bundling weather or dependencies', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wk3-radar-cli-'));
    try {
        const cli = new URL('../scripts/build-wk3-radar.mjs', import.meta.url);
        const runCLI = flags => spawnSync(process.execPath, [cli.pathname, '--version', version, '--outdir', dir, ...flags], { encoding: 'utf8' });
        const result = runCLI([]);
        assert.equal(result.status, 0, result.stderr);
        assert.match(result.stdout, /4 map scripts, 4 standalone modules/);
        assert.match(result.stdout, /Quantumult X: binary radar is unsupported/);
        const expected = Object.entries(radarPlatforms).flatMap(([p, ext]) => [`wk3-radar-${p}-map.bundle.js`, `iRingo.WeatherKit.WK3.Radar.${ext}`]).sort();
        assert.deepEqual(fs.readdirSync(dir).sort(), expected);
        for (const [platform, ext] of Object.entries(radarPlatforms)) {
            const text = fs.readFileSync(path.join(dir, `iRingo.WeatherKit.WK3.Radar.${ext}`), 'utf8');
            assert.equal(text, renderRadarModule(platform, version));
        }
        const custom = runCLI(['--script-base', 'https://cdn.example.org/radar', '--module-version', '3.3.6-test-radar-cloud.3']);
        assert.equal(custom.status, 0, custom.stderr);
        for (const [platform, ext] of Object.entries(radarPlatforms)) {
            const text = fs.readFileSync(path.join(dir, `iRingo.WeatherKit.WK3.Radar.${ext}`), 'utf8');
            assert.ok(text.includes(`https://cdn.example.org/radar/wk3-radar-${platform}-map.bundle.js`));
            assert.ok(text.includes('3.3.6-test-radar-cloud.3'));
            assert.doesNotMatch(text, /releases\/download\/v3\.3\.6-test/);
        }
        for (const base of ['http://cdn.example.org/', 'https://user:secret@cdn.example.org/', 'https://cdn.example.org/?token=private', 'https://cdn.example.org/#fragment', 'https://cdn.example.org/\n[Script]']) {
            assert.throws(() => renderRadarModule('surge', version, { scriptBase: base }), /HTTPS --script-base/);
        }
        assert.notEqual(runCLI(['--script-base', 'http://cdn.example.org/']).status, 0);
        assert.throws(() => renderRadarModule('egern', '3.3.6-test-radar-cloud.1'), /stable weather release/);
        assert.throws(() => renderRadarModule('surge', version, { moduleVersion: '3.3.6' }), /experimental/);
    } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
