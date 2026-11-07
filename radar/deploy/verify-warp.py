import json,subprocess,time
from pathlib import Path
p=Path('/var/lib/radar-demo/deployment-result.json');result=json.loads(p.read_text())
secret=Path('/etc/radar-demo/token.env').read_text().strip().split('=',1)[1]
if len(sys.argv)!=2:
    raise SystemExit('Usage: python3 verify-warp.py https://YOUR_VPS_DOMAIN/v1/radar?VALID_TILE_QUERY')
url=sys.argv[1]
u=urlsplit(url)
if u.scheme!='https' or not u.hostname or u.username or u.password or u.path!='/v1/radar' or u.fragment:
    raise SystemExit('Provide your own VPS HTTPS radar URL')
test={'client':'VPS curl through pre-existing WARP SOCKS5 at 127.0.0.1:40000; remote DNS, traffic exits WARP then returns to source','url':url,'sample_origin':'explicit URL supplied by operator; transport verification only','responses':[]}
result['warp_external_tile']=test
for i in range(8):
    r=subprocess.run(['curl','-sS','--socks5-hostname','127.0.0.1:40000','--max-time','25','--config','-','-D','/tmp/radar-warp-headers','-o','/tmp/radar-warp-body','-w','%{http_code} %{ssl_verify_result}',url],input='header = "Authorization: Bearer '+secret+'"\n',text=True,capture_output=True)
    headers={}
    if Path('/tmp/radar-warp-headers').exists():
        for line in Path('/tmp/radar-warp-headers').read_text().splitlines():
            if ':' not in line:continue
            k,v=line.split(':',1)
            if k.lower() in ['content-type','content-length','cache-control','retry-after'] or k.lower().startswith('x-wk3-'):headers[k]=v.strip()
    body=Path('/tmp/radar-warp-body').read_bytes() if Path('/tmp/radar-warp-body').exists() else b''
    row={'at':int(time.time()),'status_tls_verify':r.stdout.strip(),'curl_exit':r.returncode,'headers':headers,'bytes':len(body)}
    if r.stdout.startswith('200'):row['ktx_valid']=len(body)==65604 and body[:12]==b'\xabKTX 11\xbb\r\n\x1a\n'
    test['responses'].append(row);p.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(row),flush=True)
    if row.get('ktx_valid'):test['external_success']=True;p.write_text(json.dumps(result,ensure_ascii=False,indent=2));break
    if not r.stdout.startswith('202'):break
    time.sleep(15)
