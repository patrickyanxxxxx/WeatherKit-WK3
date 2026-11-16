import {handleNew} from './v4-handler.mjs';
import {scriptBody,moduleBody} from './assets.mjs';
// build.mjs generates assets.mjs using your own endpoint; it contains no key.
export default {
  fetch(request,env){
    const u=new URL(request.url);
    if(u.pathname==='/v4/radar')return handleNew(request,env);
    if(request.method==='GET'&&u.pathname==='/test-assets/wk3-radar-motion-v4.surge.js')return new Response(scriptBody,{headers:{'Content-Type':'application/javascript; charset=utf-8','Cache-Control':'no-store'}});
    if(request.method==='GET'&&u.pathname==='/test-assets/WeatherKit-Radar-Motion-Test.sgmodule')return new Response(moduleBody,{headers:{'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store'}});
    return new Response(null,{status:404,headers:{'Cache-Control':'no-store'}});
  }
};
