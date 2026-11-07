"""Past-only model selection, not calibrated precipitation or probability.
Select persistence, one global translation, or v2 local flow by an earlier
held-out frame. The forecast target is never used. A single 10-minute validation
cannot establish 60-minute skill. Diagnostics expose the choice and limitations.
"""
import numpy as np
import motion_v2 as v2


def _central(shape):
    h,w=shape
    if min(shape)>=384:return (h//3,2*h//3,w//3,2*w//3)
    return (0,h,0,w)


def _crop(frame,region):
    y0,y1,x0,x1=region
    return {k:np.asarray(frame[k])[y0:y1,x0:x1] for k in ('signal','echo','valid','dbz')}


def _options(flow):
    options={'persistence':dict(status='persistence',diagnostics={})}
    if flow['status']=='estimated':
        u,v=flow['diagnostics']['global_velocity']
        if flow['diagnostics']['accepted_global_pairs']:
            options['global']=dict(status='estimated',vx=u,vy=v,diagnostics={})
        options['local']=flow
    return options


def estimate(frames,times,validation_lead_minutes=None):
    if len(frames)!=len(times) or len(frames)<4:
        raise ValueError('v3 requires >=4 historical frames for past-only validation')
    times=np.asarray(times,dtype=float)
    if not np.isfinite(times).all() or np.any(np.diff(times)<=0):
        raise ValueError('times must increase and be finite Unix seconds')
    origin_index=len(times)-2
    if validation_lead_minutes is not None:
        lead=float(validation_lead_minutes)
        if not np.isfinite(lead) or lead<=0:raise ValueError('validation lead must be positive minutes')
        candidates=np.flatnonzero(np.abs(times-(times[-1]-lead*60))<1e-6)
        if not len(candidates) or candidates[-1]<2:
            return dict(status='unknown',vx=0.,vy=0.,diagnostics={
                'selected':'unknown','reason':'insufficient_same_lead_history',
                'requested_validation_lead_minutes':lead})
        origin_index=int(candidates[-1])
    region=_central(np.asarray(frames[-1]['valid']).shape)
    # Validate only on observations already available at the forecast origin.
    flow=v2.estimate(frames[origin_index-2:origin_index+1],times[origin_index-2:origin_index+1])
    truth=_crop(frames[-1],region);origin=_crop(frames[origin_index],region)
    domain=truth['valid']&origin['valid'];lead=(times[-1]-times[origin_index])/60
    scores={}
    for name,velocity in _options(flow).items():
        echo,valid,_=v2.predict(frames[origin_index],velocity,lead,output_region=region)
        known=domain&valid
        tp=int((known&echo&truth['echo']).sum())
        fp=int((known&echo&~truth['echo']).sum())
        # Unknown prediction pixels with observed echo count as misses for selection.
        fn=int((domain&truth['echo']&(~valid|~echo)).sum())
        den=tp+fp+fn
        scores[name]=dict(csi=tp/den if den else None,tp=tp,fp=fp,fn=fn,
                          unknown=int((domain&~valid).sum()))
    usable=[k for k in scores if scores[k]['csi'] is not None]
    if not usable:
        return dict(status='unknown',vx=0.,vy=0.,diagnostics={
            'selected':'unknown','reason':'no_validation_echo','scores':scores})
    # In ties prefer simpler motion; never choose using an actual forecast target.
    selected=max(usable,key=lambda k:scores[k]['csi'])
    latest=v2.estimate(frames[-3:],times[-3:])
    options=_options(latest)
    if selected not in options:
        return dict(status='unknown',vx=0.,vy=0.,diagnostics={
            'selected':'unknown','reason':'selected_motion_unavailable','scores':scores})
    chosen=options[selected]
    chosen.setdefault('diagnostics',{}).update(
        selected=selected,validation_origin=float(times[origin_index]),validation_target=float(times[-1]),
        validation_lead_minutes=lead,scores=scores,
        selection_note='short past-only validation; does not prove one-hour skill')
    return chosen


def predict(frame,velocity,lead_minutes,output_region=None):
    return v2.predict(frame,velocity,lead_minutes,output_region=output_region)
