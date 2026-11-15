"""Decision policies for unchanged coarse-grid spread metrics.

The tolerant policy optimizes expected matched precision/coverage under the existing
3x3 tolerance. Labels are used only by evaluation/tuning, never by selection.
"""
from functools import lru_cache

import numpy as np
from scipy import ndimage
from . import direct


@lru_cache(maxsize=4)
def neighbours(shape):
    y, x = np.indices(shape)
    out = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            yy, xx = y + dy, x + dx
            out.append(np.where((yy >= 0) & (yy < shape[0]) & (xx >= 0) & (xx < shape[1]),
                                yy * shape[1] + xx, -1).ravel())
    return np.stack(out, axis=1)


def tolerant_select(P, scale=1., gate=0.):
    """Greedy expected tolerant-overlap maximizer, with an explicit empty option."""
    eligible = np.isfinite(P)
    p = np.clip(np.nan_to_num(P, nan=0.) * scale, 0, 1).astype(float)
    out = np.zeros(P.shape, bool)
    if not eligible.any() or p.sum() < gate:
        return out
    nb = neighbours(P.shape)
    # Probability of at least one actual new fire in each candidate's neighbourhood.
    q = 1 - np.exp(ndimage.convolve(np.log1p(-np.clip(p, 0, 1-1e-9)),
                                     np.ones((3, 3)), mode='constant', cval=0))
    gains = q.ravel() + ndimage.convolve(p, np.ones((3, 3)), mode='constant').ravel()
    available = eligible.ravel().copy()
    covered = np.zeros(p.size, bool)
    selected = []
    numerator = 0.
    expected = p.sum()
    best = float(np.exp(np.log1p(-np.clip(p, 0, 1-1e-9)).sum()))
    best_n = 0
    for _ in range(int(available.sum())):
        i = int(np.argmax(np.where(available, gains, -np.inf)))
        numerator += gains[i]
        selected.append(i)
        available[i] = False
        value = numerator / (len(selected) + expected)
        if value > best:
            best, best_n = value, len(selected)
        new = nb[i]
        new = new[(new >= 0) & ~covered[np.maximum(new, 0)]]
        covered[new] = True
        if len(new):
            affected = nb[new]
            valid = affected >= 0
            decrement = np.broadcast_to(p.ravel()[new, None], affected.shape)
            np.subtract.at(gains, affected[valid], decrement[valid])
    out.ravel()[selected[:best_n]] = True
    return out


RULES = ([('empty',)] + list(direct.RULES) + [('all',)]
         + [('tol', scale, gate) for scale in (.5, 1., 2.) for gate in (0., 1.)]
         + [('thr', threshold) for threshold in (.6, .7, .8, .9, .95)]
         + [('topn', scale, gate) for scale in (.1, .2, .3) for gate in (0., 1.)]
         + [('smooth', radius, threshold) for radius in (1, 2) for threshold in (.05, .1, .2, .3)])


def select(P, rule):
    if rule[0] == 'empty':
        return np.zeros(P.shape, bool)
    if rule[0] == 'all':
        return np.isfinite(P)
    if rule[0] == 'tol':
        return tolerant_select(P, rule[1], rule[2])
    if rule[0] == 'smooth':
        valid = np.isfinite(P)
        numerator = ndimage.uniform_filter(np.nan_to_num(P, nan=0.), size=2*rule[1]+1, mode='constant')
        denominator = ndimage.uniform_filter(valid.astype(float), size=2*rule[1]+1, mode='constant')
        return valid & (numerator / np.maximum(denominator, 1e-9) >= rule[2])
    return direct.select(P, rule)


def tolerant_score(pred, truth):
    if not pred.any() and not truth.any():
        return 1.
    if not pred.any() or not truth.any():
        return 0.
    return float(((pred & ndimage.maximum_filter(truth, size=3, mode='constant')).sum()
                  + (truth & ndimage.maximum_filter(pred, size=3, mode='constant')).sum())
                 / (pred.sum() + truth.sum()))


def score_rules(P, truth):
    out = np.empty((len(P), len(RULES), 2), np.float32)
    for k in range(len(P)):
        for j, rule in enumerate(RULES):
            mask = select(P[k], rule)
            out[k, j] = direct.iou_masks(mask, truth[k]), tolerant_score(mask, truth[k])
    return out


def summary_features(P, A, case):
    """Event features from the prediction and observed history only."""
    t0, cx, cy = case
    lat, lon = direct.cell_center(cx, cy)
    sine, cosine = direct.day_of_year_features(np.array([t0]))
    b = A[:direct.HIST+1] > 0
    features = [float(lat), float(lon), sine[0], cosine[0]]
    features += [np.log1p(b[-1-lag].sum()) for lag in (0, 1, 2, 3, 7)]
    features += [np.log1p((b[-1] & ~b[-2]).sum()), np.log1p((b[-1] & b[-2]).sum()),
                 np.log1p(b[-8:-1].any(0).sum()), np.log1p(ndimage.label(b[-1])[1])]
    for p in P:
        vals = p[np.isfinite(p)]
        if len(vals):
            features.extend([np.log1p(len(vals)), np.log1p(vals.sum()), vals.mean(), vals.std(),
                             *np.quantile(vals, [.5, .75, .9, .99]), vals.max(),
                             float((vals > .2).mean()), float((vals > .5).mean())])
        else:
            features.extend([0.] * 11)
    return np.asarray(features, np.float32)
