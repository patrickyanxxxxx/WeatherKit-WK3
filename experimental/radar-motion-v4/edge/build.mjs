import{readFile,writeFile}from'node:fs/promises';import{fileURLToPath}from'node:url';import path from'node:path';
const dir=path.dirname(fileURLToPath(import.meta.url));
const endpoint=process.env.RADAR_ENDPOINT;
const scriptURL=process.env.RADAR_SCRIPT_URL;
for(const [name,value,pathName]of[['RADAR_ENDPOINT',endpoint,'/v4/radar'],['RADAR_SCRIPT_URL',scriptURL,'/test-assets/wk3-radar-motion-v4.surge.js']]){
 if(!value)throw Error(name+' is required');const u=new URL(value);
 if(u.protocol!=='https:'||u.username||u.password||u.search||u.hash||u.pathname!==pathName||u.hostname.endsWith('.apple.com'))throw Error(name+' must be a plain HTTPS URL ending '+pathName);
}
const original=await readFile(path.join(dir,'surge.template.js'),'utf8');
const script=original.replace("const ENDPOINT='__RADAR_ENDPOINT__';","const ENDPOINT="+JSON.stringify(endpoint)+";");
if(script===original)throw Error('missing endpoint placeholder');
await writeFile(path.join(dir,'wk3-radar-motion-v4.surge.js'),script);
const module=`#!name=WeatherKit-Radar-Motion-Test
#!version=0.4.0-test-motion.5
#!desc=RainViewer experiment; inland z7 only. Native next-hour availability and hatching remain unchanged.
[Rule]
AND,((DOMAIN,weather-map2.apple.com),(PROTOCOL,UDP),(DEST-PORT,443)),REJECT
[Script]
WeatherKit-Radar-Motion-Test = type=http-request,pattern=^https:\\/\\/weather-map2\\.apple\\.com\\/v1\\/mapOverlay\\/precipitationRadarMap(?:\\?|$),script-path=${scriptURL},timeout=10,binary-body-mode=1,engine=webview
[MITM]
hostname = %APPEND% weather-map2.apple.com
`;
await writeFile(path.join(dir,'WeatherKit-Radar-Motion-Test.sgmodule'),module);
await writeFile(path.join(dir,'assets.mjs'),'export const scriptBody='+JSON.stringify(script)+';\nexport const moduleBody='+JSON.stringify(module)+';\n');
console.log('Generated personal Surge module and script for your own Worker');
