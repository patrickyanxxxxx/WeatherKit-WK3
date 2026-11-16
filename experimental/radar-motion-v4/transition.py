"""Prototype display-space crossfade; never a physical rain-rate correction."""
import numpy as np
DURATION=1800
ATTRIBUTION='Weather data by RainViewer; Powered by Rainbow.ai'
def observation_weight(anchor,valid):
    return float(np.clip(1-(valid-anchor)/DURATION,0,1))
def transition_dbz(observation,prediction,valid_observation,valid_prediction,anchor,valid):
    if not valid_observation.all() or not valid_prediction.all():
        raise ValueError('transition requires complete known coverage')
    weight=observation_weight(anchor,valid)
    # Blend the user-approved display mapping, not logarithmic radar physics.
    a=np.clip((observation-5)/50,0,1);b=np.clip((prediction-5)/50,0,1)
    return 5+50*(weight*a+(1-weight)*b),weight
