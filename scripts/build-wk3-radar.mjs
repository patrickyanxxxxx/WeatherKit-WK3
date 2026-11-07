import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderWK3Module } from './build-wk3-modules.mjs';
import { renderLocalModule } from './build-egern-module.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const radarPlatforms = { surge: 'sgmodule', loon: 'lpx', stash: 'stoverride', egern: 'yaml' };
const pattern = '^https://weather-map2\\.apple\\.com/v1/mapOverlay/precipitationRadarMap\\?';
const rule = 'AND,((DOMAIN,weather-map2.apple.com),(PROTOCOL,UDP),(DEST-PORT,443)),REJECT';
const help = '一小时降水雷达实验版（回波，非校准雨强）；自填 Worker 或 VPS 的 HTTPS /v1/radar；空值透传 Apple；来源、时间或 KTX 校验失败透传';
const endpoint = { name: '雷达 HTTPS endpoint', default_value: '', description: help };

export const defaultScriptBase = 'https://raw.githubusercontent.com/patrickyanxxxxx/WeatherKit-WK3/test/cloudflare-radar/';

function normalizeScriptBase(value) {
    let base;
    try { base = new URL(value); } catch { throw new Error('Specify an HTTPS --script-base'); }
    if (base.protocol !== 'https:' || base.username || base.password || base.search || base.hash || /[\s'"<>\\]/.test(value)) throw new Error('Specify an HTTPS --script-base without credentials, query or fragment');
    return base.href.replace(/\/?$/, '/');
}

export function renderRadarModule(platform, version, { scriptBase = defaultScriptBase, moduleVersion = `${version}-test-radar-cloud.1` } = {}) {
    if (!Object.hasOwn(radarPlatforms, platform)) throw new Error('Unsupported Radar platform: ' + platform + '; Quantumult X binary radar is not supported');
    if (!/^[0-9]+\.[0-9]+\.[0-9]+$/.test(version)) throw new Error('Specify the stable weather release version, e.g. 3.3.6');
    if (!/^[0-9]+\.[0-9]+\.[0-9]+-[0-9A-Za-z.-]+$/.test(moduleVersion)) throw new Error('Specify an experimental --module-version');
    const base = normalizeScriptBase(scriptBase);
    // All five weather rules keep the original stable release URLs and run locally.
    let text = platform === 'egern' ? renderLocalModule({ version }) : renderWK3Module(platform, version);
    text = text.replaceAll('WeatherKit-Local-WK3', 'WeatherKit-Local-WK3-Radar-Experimental').replace("name: 'WeatherKit-WK3'", "name: 'WeatherKit-WK3-Radar-Experimental'");
    text = text.replace(/^(#!version = ).*$/m, `$1${moduleVersion}`).replace(/^version: .*$/m, `version: "${moduleVersion}"`);
    if (platform === 'egern') text = text.replace(/^(name: .*)$/m, `$1\nversion: "${moduleVersion}"`);
    const url = `${base}wk3-radar-${platform}-map.bundle.js`;
    if (platform === 'surge' || platform === 'loon') {
        text = text.replace(/^(#!desc = .*)$/m, `$1；${help}`);
        if (platform === 'surge') {
            text = text.replace(/^(#!arguments = .*)$/m, '$1,RADAR_ENDPOINT:""').replace(/^(#!arguments-desc = .*)$/m, `$1\\nRADAR_ENDPOINT: ${help}`);
            text = text.replace('\n[MITM]', `\nWK3.Experimental.Radar = type=http-request, pattern=${pattern}, binary-body-mode=1, engine=webview, timeout=10, script-path=${url}, argument=RADAR_ENDPOINT="{{{RADAR_ENDPOINT}}}"\n\n[MITM]`);
        } else {
            text = text.replace('\n[Rule]', `\nRADAR_ENDPOINT = input,"",tag=${endpoint.name},desc=${help}\n\n[Rule]`);
            text = text.replace('\n[MITM]', `\nhttp-request ${pattern} binary-body-mode=1, timeout=10, script-path=${url}, tag=WK3.Experimental.Radar, argument=RADAR_ENDPOINT={RADAR_ENDPOINT}\n\n[MITM]`);
        }
        text = text.replace('[Rule]\n', `[Rule]\n${rule}\n`).replace(/^(hostname = .*weatherkit\.apple\.com)$/m, '$1, weather-map2.apple.com');
    } else if (platform === 'stash') {
        text = text.replace('rules:\n', `# ${help}\n# 编辑下方 Radar 独立 argument 的 RADAR_ENDPOINT；天气参数仍在首个 argument。\nrules:\n  - '${rule}'\n`);
        text = text.replace('    - weatherkit.apple.com\n', '    - weatherkit.apple.com\n    - weather-map2.apple.com\n');
        text = text.replace('script-providers:\n', `    - match: '${pattern}'\n      name: WK3.Experimental.Radar\n      type: request\n      timeout: 10\n      binary-mode: true\n      argument: '{"RADAR_ENDPOINT":""}'\nscript-providers:\n  WK3.Experimental.Radar:\n    url: ${url}\n    interval: 86400\n`);
    } else {
        const q = JSON.stringify;
        text = text.replace(/^(description: .*)$/m, `$1\n# ${help}`);
        text = text.replace(/^compat_arguments: (.*)$/m, (_, json) => `compat_arguments: ${q({ ...JSON.parse(json), RADAR_ENDPOINT: '' })}`);
        text = text.replace(/^compat_arguments_desc: (.*)$/m, (_, json) => `compat_arguments_desc: ${q(JSON.parse(json) + '\nRADAR_ENDPOINT: ' + help)}`);
        text = text.replace(/^env_schema: (.*)$/m, (_, json) => `env_schema: ${q({ ...JSON.parse(json), RADAR_ENDPOINT: endpoint })}`);
        text = text.replace('rules:\n', "rules:\n- and:\n    match:\n    - domain:\n        match: weather-map2.apple.com\n    - protocol:\n        match: UDP\n    - dest_port:\n        match: '443'\n    policy: REJECT\n");
        text = text.replace('\nmitm:\n', `\n- http_request:\n    name: WK3.Experimental.Radar\n    env:\n      RADAR_ENDPOINT: '{{{RADAR_ENDPOINT}}}'\n    match: '${pattern}'\n    script_url: ${url}\n    timeout: 10\n    body_required: false\n    binary_body: true\n    update_interval: 86400\n\nmitm:\n`);
        text = text.replace('    - weatherkit.apple.com\n', '    - weatherkit.apple.com\n    - weather-map2.apple.com\n');
    }
    return text;
}

export function buildRadarScripts({ outdir } = {}) {
    if (!outdir) throw new Error('Specify --outdir');
    fs.mkdirSync(outdir, { recursive: true });
    const core = fs.readFileSync(path.join(root, 'src/radar/core.js'), 'utf8');
    const files = {};
    for (const platform of Object.keys(radarPlatforms)) {
        const code = `// Generated standalone WK3 Radar ${platform}. HTTPS endpoint is user supplied.\n${core}\n${fs.readFileSync(path.join(root, `src/radar/${platform}.js`), 'utf8')}`;
        const file = path.join(outdir, `wk3-radar-${platform}-map.bundle.js`);
        fs.writeFileSync(file, code);
        files[platform] = file;
    }
    return files;
}

export function buildRadarModules({ outdir, version, scriptBase, moduleVersion } = {}) {
    if (!outdir) throw new Error('Specify --outdir');
    const texts = Object.fromEntries(Object.keys(radarPlatforms).map(platform => [platform, renderRadarModule(platform, version, { scriptBase, moduleVersion })]));
    fs.mkdirSync(outdir, { recursive: true });
    const files = {};
    for (const [platform, text] of Object.entries(texts)) {
        const file = path.join(outdir, `iRingo.WeatherKit.WK3.Radar.${radarPlatforms[platform]}`);
        fs.writeFileSync(file, text);
        files[platform] = file;
    }
    return files;
}

export function buildRadar({ outdir, version, scriptBase, moduleVersion } = {}) {
    // Validate/render modules before creating any script outputs.
    const modules = buildRadarModules({ outdir, version, scriptBase, moduleVersion });
    const scripts = buildRadarScripts({ outdir });
    return { scripts, modules };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const args = process.argv.slice(2);
    const options = {};
    const flags = { '--outdir': 'outdir', '--version': 'version', '--script-base': 'scriptBase', '--module-version': 'moduleVersion' };
    for (let i = 0; i < args.length; i++) {
        const key = flags[args[i]];
        if (!key || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error('Usage: node scripts/build-wk3-radar.mjs --version 3.3.6 --outdir dist-radar [--script-base https://host/path/] [--module-version 3.3.6-test-radar-cloud.1]');
        options[key] = args[++i];
    }
    if (!options.outdir) throw new Error('Specify --outdir');
    options.outdir = path.resolve(options.outdir);
    const result = buildRadar(options);
    console.log(`WK3 Radar: ${Object.keys(result.scripts).length} map scripts, ${Object.keys(result.modules).length} complete modules; weather uses existing v${options.version} release scripts.`);
    console.log('Quantumult X: binary radar is unsupported; no Radar script or module generated.');
}
