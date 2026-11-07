"""Deploy the dependency-free module via Cloudflare API; credentials stay in env.
Usage: CLOUDFLARE_API_TOKEN=... python3 cloudflare/deploy.py --account ID --origin https://compute.example
Optional env VPS_TOKEN is stored as a Cloudflare secret_text binding.
"""
import argparse, json, os, secrets, urllib.request, urllib.error
from pathlib import Path

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--account', required=True)
    p.add_argument('--name', default='wk3-radar')
    p.add_argument('--origin', required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    token = os.environ.get('CLOUDFLARE_API_TOKEN')
    if not token:
        raise SystemExit('Missing CLOUDFLARE_API_TOKEN')
    from urllib.parse import urlsplit
    u = urlsplit(a.origin)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
        raise SystemExit('--origin must be an HTTPS origin')
    metadata = {'main_module': 'worker.mjs', 'compatibility_date': '2026-11-07', 'bindings': [{'type': 'plain_text', 'name': 'VPS_ORIGIN', 'text': a.origin.rstrip('/')} ]}
    if os.environ.get('VPS_TOKEN'):
        metadata['bindings'].append({'type': 'secret_text', 'name': 'VPS_TOKEN', 'text': os.environ['VPS_TOKEN']})
    boundary = 'minis-' + secrets.token_hex(12)
    parts = []
    for name, filename, mime, content in [('metadata', None, 'application/json', json.dumps(metadata).encode()), ('worker.mjs', 'worker.mjs', 'application/javascript+module', Path(__file__).with_name('worker.mjs').read_bytes())]:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else '')
        parts.append(f'--{boundary}\r\nContent-Disposition: {disposition}\r\nContent-Type: {mime}\r\n\r\n'.encode() + content + b'\r\n')
    body = b''.join(parts) + f'--{boundary}--\r\n'.encode()
    base = f'https://api.cloudflare.com/client/v4/accounts/{a.account}/workers'
    def api(path, data, mime, method):
        request = urllib.request.Request(base + path, data=data, method=method, headers={'Authorization': 'Bearer ' + token, 'Content-Type': mime})
        try:
            with urllib.request.urlopen(request, timeout=60) as r: result = json.load(r)
        except urllib.error.HTTPError as e:
            # Cloudflare errors are useful but a binding value must never be printed.
            raise SystemExit(f'Cloudflare HTTP {e.code}; deployment not confirmed') from None
        if not result.get('success'): raise SystemExit('Cloudflare request failed; deployment not confirmed')
        return result.get('result')
    result = api('/scripts/' + a.name, body, 'multipart/form-data; boundary=' + boundary, 'PUT')
    api('/scripts/' + a.name + '/subdomain', b'{"enabled":true,"previews_enabled":false}', 'application/json', 'POST')
    subdomain = api('/subdomain', None, 'application/json', 'GET')['subdomain']
    doc = {'name': a.name, 'url': f'https://{a.name}.{subdomain}.workers.dev', 'origin': a.origin, 'version_id': result.get('version_id'), 'compute': 'vps'}
    if a.output: a.output.write_text(json.dumps(doc, indent=2) + '\n')
    print(json.dumps(doc))

if __name__ == '__main__': main()
