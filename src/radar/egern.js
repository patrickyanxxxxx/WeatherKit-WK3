export default async function(ctx) {
  try {
    const t = tile(ctx.request, Math.floor(Date.now() / 1000));
    if (!t) return;
    const endpoint = config('', ctx.env);
    if (!endpoint) return;
    const response = await ctx.http.get(gatewayURL(endpoint, t), { headers: { Accept: 'image/ktx' }, timeout: 8000, credentials: 'omit', redirect: 'error' });
    const good = validate(response, await response.arrayBuffer(), t, Math.floor(Date.now() / 1000));
    console.log('[WK3 RADAR] EXPERIMENTAL-ECHO z=' + t.z + ' x=' + t.x + ' y=' + t.y + ' valid=' + t.time + ' KTX=' + good.bytes.length);
    return ctx.respond({ status: 200, headers: good.headers, body: good.bytes });
  } catch (_) { console.log('[WK3 RADAR] passthrough validation/network'); return; }
}
