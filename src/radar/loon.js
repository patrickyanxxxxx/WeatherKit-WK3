(async () => {
  let finished = false;
  const done = value => { if (!finished) { finished = true; $done(value); } };
  let timer;
  try {
    const t = tile($request, Math.floor(Date.now() / 1000));
    if (!t) return done({});
    const endpoint = config(typeof $argument === 'undefined' ? '' : $argument, typeof $env === 'undefined' ? {} : $env);
    if (!endpoint) return done({});
    timer = setTimeout(() => { console.log('[WK3 RADAR] passthrough timeout'); done({}); }, 8500);
    const raw = await new Promise((resolve, reject) => {
      $httpClient.get({ url: gatewayURL(endpoint, t), headers: { Accept: 'image/ktx' }, timeout: 8, 'binary-mode': true, 'auto-redirect': false, 'auto-cookie': false }, (error, response, data) => error ? reject(Error('network')) : resolve({ response, data }));
    });
    if (finished) return;
    const good = validate(raw.response, raw.data, t, Math.floor(Date.now() / 1000));
    console.log('[WK3 RADAR] EXPERIMENTAL-ECHO z=' + t.z + ' x=' + t.x + ' y=' + t.y + ' valid=' + t.time + ' KTX=' + good.bytes.length);
    done({ response: { status: 200, headers: good.headers, body: good.bytes } });
  } catch (_) { console.log('[WK3 RADAR] passthrough validation/network'); done({}); }
  finally { if (timer) clearTimeout(timer); }
})();
