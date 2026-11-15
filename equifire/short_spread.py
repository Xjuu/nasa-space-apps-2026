"""Short-horizon spread training with equal fire/horizon weight, separate from direct.py."""
import numpy as np
from . import direct, spread_policy


def event_weights(weights, groups, horizon):
    """Preserve inverse sampling corrections, but give every fire/horizon equal mass."""
    groups = np.asarray(groups, dtype=np.int64)
    horizon = np.asarray(horizon, dtype=np.int64)
    _, keys = np.unique(np.column_stack((groups, horizon)), axis=0, return_inverse=True)
    totals = np.bincount(keys, weights=weights)
    out = np.asarray(weights, dtype=float) / totals[keys]
    return (out / out.mean()).astype(np.float32)


def rows(fi, cases, weather, cols, seed=0, neighbourhood_day1=False):
    if neighbourhood_day1:
        return neighbourhood_rows(fi, cases, weather, cols, seed)
    X, y, w, g = direct.build_rows(fi, cases, weather, ks=[1,2,3], ks_per_case=0,
                                 cols=cols, rng=np.random.default_rng(seed), verbose=True)
    return X, y, event_weights(w, g, X[:,-1])


def forecast(model, meta, fi, weather=None, case=None):
    if case is None:
        raise ValueError('case=(start_day, grid_x, grid_y) is required')
    if weather is None:
        if meta.get('settings',{}).get('ncep_weather'):
            from .ncep_weather import NCEPWeather
            weather=NCEPWeather()
        else:
            weather=direct.Weather()
    result = direct.predict_case(model, fi, *case, weather, 3, meta['features'])
    if result is None:
        return None
    P, _, _, _ = result
    return np.array([spread_policy.select(P[k], rule) for k,rule in enumerate(meta['policy_rules'])])


def neighbourhood_rows(fi, cases, weather, cols, seed):
    """Learn day-1 matched-risk; days 2/3 retain exact-cell targets. Scores are not
    cumulative ignition probabilities. Evaluation always uses original truth masks.
    """
    from scipy import ndimage
    rng=np.random.default_rng(seed)
    xs,ys,ws,gs=[],[],[],[]
    for ci,case in enumerate(cases):
        A,r=direct._case_inputs(fi,*case,weather,cols)
        if r is None:continue
        X,dist,A0=r
        truth=direct.case_truth(A,dist,A0,3)
        for k in (1,2,3):
            target=ndimage.maximum_filter(truth[0],size=3,mode='constant') if k==1 else truth[k-1]
            eligible=np.flatnonzero((dist.ravel()>=1)&(dist.ravel()<=k))
            positive=eligible[target.ravel()[eligible]]
            negative=eligible[~target.ravel()[eligible]]
            pp=min(250,len(positive));nn=min(len(negative),max(60,3*pp))
            pos=rng.choice(positive,pp,replace=False) if pp else np.array([],dtype=int)
            neg=rng.choice(negative,nn,replace=False) if nn else np.array([],dtype=int)
            pick=np.r_[pos,neg]
            if not len(pick):continue
            xs.append(np.c_[X[pick],np.full(len(pick),k,np.float32)])
            ys.append(np.r_[np.ones(pp),np.zeros(nn)])
            ws.append(np.r_[np.full(pp,len(positive)/max(pp,1)),np.full(nn,len(negative)/max(nn,1))])
            gs.append(np.full(len(pick),ci))
    X=np.concatenate(xs).astype(np.float32)
    y=np.concatenate(ys).astype(np.float32)
    w=event_weights(np.concatenate(ws),np.concatenate(gs),X[:,-1])
    return X,y,w
