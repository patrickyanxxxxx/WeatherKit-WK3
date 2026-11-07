import os, subprocess, sys, tempfile, threading, unittest
import urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from native_gateway import Store, handler

class DeploymentTests(unittest.TestCase):
    def test_bearer_required_for_status_and_radar(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(Store(tmp), token='test-only-token'))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                for path in ['/status', '/v1/radar?z=7']:
                    for auth in [None, 'Bearer wrong', 'Basic test-only-token', 'Bearer 非法']:
                        req = urllib.request.Request(base+path)
                        if auth is not None:
                            if not auth.isascii():
                                continue
                            req.add_header('Authorization', auth)
                        with self.assertRaises(urllib.error.HTTPError) as error:
                            urllib.request.urlopen(req)
                        self.assertEqual(error.exception.code, 401)
                        self.assertEqual(error.exception.headers['WWW-Authenticate'], 'Bearer')
                        self.assertEqual(error.exception.read(), b'')
                req = urllib.request.Request(base+'/status', headers={'Authorization':'Bearer test-only-token'})
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(response.status, 200)
            finally:
                server.shutdown(); server.server_close(); worker.join()

    def test_explicit_missing_token_fails_closed(self):
        env = dict(os.environ)
        env.pop('RADAR_TEST_UNSET_TOKEN', None)
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('native_gateway.py')),
                                 '--token-env', 'RADAR_TEST_UNSET_TOKEN'],
                                env=env, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'Token environment variable is missing', result.stderr)
