(async () => {
  let finished = false;
  const done = value => { if (!finished) { finished = true; $done(value); } };
  let timer;
  let stage = 'route';
  try {
    const t = tile($request, Math.floor(Date.now() / 1000));
    if (!t) return done({});
    stage = 'config';
    const endpoint = config(typeof $argument === 'undefined' ? '' : $argument, typeof $env === 'undefined' ? {} : $env);
    if (!endpoint) { console.log('[WK3 RADAR] passthrough endpoint-empty'); return done({}); }
    stage = 'request';
    console.log('[WK3 RADAR] gateway-request z=' + t.z + ' x=' + t.x + ' y=' + t.y + ' valid=' + t.time);
    timer = setTimeout(() => { console.log('[WK3 RADAR] passthrough timeout'); done({}); }, 8500);
    const raw = await new Promise((resolve, reject) => {
      $httpClient.get({ url: gatewayURL(endpoint, t), headers: { Accept: 'image/ktx' }, timeout: 8, 'binary-mode': true, 'auto-redirect': false, 'auto-cookie': false }, (error, response, data) => error ? reject(Error('network')) : resolve({ response, data }));
    });
    if (finished) return;
    const status = Number(raw.response?.status ?? raw.response?.statusCode ?? 0);
    console.log('[WK3 RADAR] gateway-status=' + (Number.isFinite(status) ? status : 0));
    if (status !== 200) return done({});
    stage = 'validate';
    const good = validate(raw.response, raw.data, t, Math.floor(Date.now() / 1000));
    console.log('[WK3 RADAR] EXPERIMENTAL-ECHO z=' + t.z + ' x=' + t.x + ' y=' + t.y + ' valid=' + t.time + ' KTX=' + good.bytes.length);
    done({ response: { status: 200, headers: good.headers, body: good.bytes } });
  } catch (error) {
    const known = ['config','status','marker','headers','time','source-time','binary','size','ktx'];
    const reason = known.includes(error?.message) ? error.message : (error?.name === 'ReferenceError' ? 'runtime-api' : (stage === 'request' ? 'network' : 'unknown'));
    console.log('[WK3 RADAR] passthrough stage=' + stage + ' reason=' + reason); done({});
  }
  finally { if (timer) clearTimeout(timer); }
})();
