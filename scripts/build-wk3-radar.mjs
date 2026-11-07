import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

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

export function renderRadarModule(platform, version, { scriptBase = defaultScriptBase, moduleVersion = `${version}-test-radar-cloud.2` } = {}) {
    if (!Object.hasOwn(radarPlatforms, platform)) throw new Error('Unsupported Radar platform: ' + platform + '; Quantumult X binary radar is not supported');
    if (!/^[0-9]+\.[0-9]+\.[0-9]+$/.test(version)) throw new Error('Specify the stable weather release version, e.g. 3.3.6');
    if (!/^[0-9]+\.[0-9]+\.[0-9]+-[0-9A-Za-z.-]+$/.test(moduleVersion)) throw new Error('Specify an experimental --module-version');
    const url = `${normalizeScriptBase(scriptBase)}wk3-radar-${platform}-map.bundle.js`;
    const name = 'WeatherKit-WK3-Radar-Experimental';
    const description = `独立雷达模块；与普通 WK3 天气模块同时启用；${help}`;
    const homepage = 'https://github.com/patrickyanxxxxx/WeatherKit-WK3/tree/feat/cloudflare-radar';
    if (platform === 'surge' || platform === 'loon') {
        const argumentsText = platform === 'surge'
            ? `#!arguments = RADAR_ENDPOINT:""\n#!arguments-desc = RADAR_ENDPOINT: ${help}\n`
            : `\n[Argument]\nRADAR_ENDPOINT = input,"",tag=${endpoint.name},desc=${help}\n`;
        const script = platform === 'surge'
            ? `WK3.Experimental.Radar = type=http-request, pattern=${pattern}, binary-body-mode=1, engine=webview, timeout=10, script-path=${url}, argument=RADAR_ENDPOINT="{{{RADAR_ENDPOINT}}}"`
            : `http-request ${pattern} binary-body-mode=1, timeout=10, script-path=${url}, tag=WK3.Experimental.Radar, argument=RADAR_ENDPOINT={RADAR_ENDPOINT}`;
        return `#!name = ${name}
#!desc = ${description}
#!homepage = ${homepage}
#!version = ${moduleVersion}
${argumentsText}
[Rule]
${rule}

[Script]
${script}

[MITM]
hostname = ${platform === 'surge' ? '%APPEND% ' : ''}weather-map2.apple.com
`;
    }
    if (platform === 'stash') return `name: ${name}
desc: ${description}
homepage: ${homepage}
version: "${moduleVersion}"
# 编辑雷达脚本 argument 内的 RADAR_ENDPOINT；天气参数仍由普通模块管理。
rules:
  - 'AND,((DOMAIN,weather-map2.apple.com),(NETWORK,udp),(DST-PORT,443)),REJECT'
http:
  mitm:
    - weather-map2.apple.com
  script:
    - match: '${pattern}'
      name: WK3.Experimental.Radar
      type: request
      timeout: 10
      binary-mode: true
      argument: '{"RADAR_ENDPOINT":""}'
script-providers:
  WK3.Experimental.Radar:
    url: ${url}
    interval: 86400
`;
    return `name: '${name}'
description: '${description}'
homepage: ${homepage}
version: "${moduleVersion}"
compat_arguments: ${JSON.stringify({ RADAR_ENDPOINT: '' })}
compat_arguments_desc: ${JSON.stringify('RADAR_ENDPOINT: ' + help)}
env_schema: ${JSON.stringify({ RADAR_ENDPOINT: endpoint })}
rules:
- and:
    match:
    - domain:
        match: weather-map2.apple.com
    - protocol:
        match: udp
    - dest_port:
        match: '443'
    policy: REJECT
scriptings:
- http_request:
    name: WK3.Experimental.Radar
    env:
      RADAR_ENDPOINT: '{{{RADAR_ENDPOINT}}}'
    match: '${pattern}'
    script_url: ${url}
    timeout: 10
    body_required: false
    binary_body: true
    update_interval: 86400
mitm:
  hostnames:
    includes:
    - weather-map2.apple.com
`;
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
        if (!key || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error('Usage: node scripts/build-wk3-radar.mjs --version 3.3.6 --outdir dist-radar [--script-base https://host/path/] [--module-version 3.3.6-test-radar-cloud.2]');
        options[key] = args[++i];
    }
    if (!options.outdir) throw new Error('Specify --outdir');
    options.outdir = path.resolve(options.outdir);
    const result = buildRadar(options);
    console.log(`WK3 Radar: ${Object.keys(result.scripts).length} map scripts, ${Object.keys(result.modules).length} standalone modules; enable alongside the ordinary WK3 weather module.`);
    console.log('Quantumult X: binary radar is unsupported; no Radar script or module generated.');
}
