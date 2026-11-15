"""Monotone calibration of observed new-fire-by-horizon cell scores.

Calibrated probabilities target satellite detections on the original eligible
coarse grid, not true fire perimeters, incident confidence, or the physics ROS
multiplier. Fit only on separate earlier-year predictions. JSON artifacts bind
calibration to a frozen blender hash and an outcome cutoff.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

VERSION = 1
EPS = 1e-6


def group_weights(groups):
    """Every fire/event proxy contributes equal total mass, irrespective of cell count."""
    groups = np.asarray(groups)
    _, inverse, counts = np.unique(groups, return_inverse=True, return_counts=True)
    return 1.0 / counts[inverse]


def _arrays(p, y, groups):
    p, y, groups = np.asarray(p, float), np.asarray(y, float), np.asarray(groups)
    if p.ndim != 1 or not (p.shape == y.shape == groups.shape) or not len(p):
        raise ValueError('Scores, binary targets and groups must be matching nonempty vectors')
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any() or not np.isin(y, [0, 1]).all():
        raise ValueError('Calibration needs finite scores in [0,1] and binary targets')
    return p, y, groups


def fit_horizon(p, y, groups, min_groups=20):
    p, y, groups = _arrays(p, y, groups)
    support = dict(groups=int(len(np.unique(groups))), cells=len(p), positives=int(y.sum()))
    if support['groups'] < min_groups or y.sum() < 20 or (1-y).sum() < 20:
        return dict(slope=1.0, intercept=0.0, supported=False, reason='Insufficient independent calibration support', **support)
    z = logit(np.clip(p, EPS, 1-EPS))
    w = group_weights(groups)
    w /= w.sum()
    # Fixed weak regularization; no test-year hyperparameter/threshold search.
    def objective(ab):
        a, b = ab
        eta = a*z + b
        loss = np.sum(w * (np.logaddexp(0, eta) - y*eta)) + 1e-4*(a-1)**2 + 1e-6*b*b
        diff = w*(expit(eta)-y)
        grad = np.array([np.sum(diff*z)+2e-4*(a-1), np.sum(diff)+2e-6*b])
        return loss, grad
    result = minimize(objective, [1., 0.], method='L-BFGS-B', jac=True, bounds=[(0.0, 20.0), (-20., 20.)])
    if not result.success:
        raise RuntimeError('Calibration optimization failed: '+result.message)
    return dict(slope=float(result.x[0]), intercept=float(result.x[1]), supported=True, **support)


def transform(p, parameters):
    p = np.asarray(p, dtype=float)
    out = np.full(p.shape, np.nan)
    finite = np.isfinite(p)
    if ((p[finite] < 0) | (p[finite] > 1)).any():
        raise ValueError('Scores must be in [0,1]')
    if not parameters.get('supported', False):
        out[finite] = p[finite]
    else:
        out[finite] = expit(parameters['slope']*logit(np.clip(p[finite], EPS, 1-EPS))+parameters['intercept'])
    return out


def apply(p, horizon, artifact, *, source_sha256, prediction_year, observation_mode, prediction_date=None):
    if observation_mode != 'archive':
        raise ValueError('This calibrator is not validated for live reduced-history inputs')
    if artifact.get('version') != VERSION or artifact.get('source_sha256') != source_sha256:
        raise ValueError('Calibrator does not match this frozen blender')
    if prediction_date is not None and artifact.get('calibration_outcomes_through_date'):
        date = np.datetime64(prediction_date, 'D')
        if str(date)[:4] != str(int(prediction_year)) or date <= np.datetime64(artifact['calibration_outcomes_through_date'], 'D'):
            raise ValueError('Calibration outcomes are not earlier than this prediction date')
    elif int(prediction_year) <= int(artifact['calibration_outcomes_through_year']):
        raise ValueError('Calibration outcomes are not earlier than this prediction year')
    parameters = artifact['horizons'].get(str(int(horizon)))
    if parameters is None:
        raise ValueError('Unsupported calibration horizon')
    return transform(p, parameters)


def metrics(p, y, groups, bins=10):
    p, y, groups = _arrays(p, y, groups)
    w = group_weights(groups)
    w /= w.sum()
    clipped = np.clip(p, EPS, 1-EPS)
    out = dict(cells=len(p), groups=int(len(np.unique(groups))), positives=int(y.sum()),
               prevalence=float(np.sum(w*y)), brier=float(np.sum(w*(p-y)**2)),
               log_loss=float(-np.sum(w*(y*np.log(clipped)+(1-y)*np.log1p(-clipped)))))
    table = []
    ece = 0.
    for lo, hi in zip(np.linspace(0,1,bins+1)[:-1], np.linspace(0,1,bins+1)[1:]):
        mask = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        mass = w[mask].sum()
        row = dict(lower=float(lo), upper=float(hi), cells=int(mask.sum()), weight=float(mass))
        if mass:
            mean = float(np.sum(w[mask]*p[mask])/mass)
            observed = float(np.sum(w[mask]*y[mask])/mass)
            row.update(mean_probability=mean, observed_frequency=observed)
            ece += mass*abs(mean-observed)
        table.append(row)
    out.update(ece=float(ece), reliability=table)
    return out


def paired_bootstrap(raw, calibrated, y, groups, seed=20261115, samples=2000):
    raw, y, groups = _arrays(raw, y, groups)
    calibrated, _, _ = _arrays(calibrated, y, groups)
    delta = (calibrated-y)**2 - (raw-y)**2
    by_group = np.array([delta[groups == g].mean() for g in np.unique(groups)])
    rng = np.random.default_rng(seed)
    bs = by_group[rng.integers(0, len(by_group), (samples, len(by_group)))].mean(1)
    return dict(metric='calibrated_minus_raw_brier; negative is better', mean=float(by_group.mean()),
                lower_95=float(np.quantile(bs,.025)), upper_95=float(np.quantile(bs,.975)), groups=len(by_group))


def cumulative(scores, horizons, locations):
    """Enforce risk-by-day1 <= day2 <= day3 for each eligible case/cell.

    Only existing finite entries are returned; no probability is invented for an
    ineligible location/horizon. `locations` must identify case+cell, not just cell.
    """
    scores, horizons, locations = np.asarray(scores,float), np.asarray(horizons,int), np.asarray(locations)
    if not (scores.shape == horizons.shape == locations.shape) or scores.ndim != 1 or not len(scores):
        raise ValueError('Matching nonempty score/horizon/location vectors required')
    if not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any() or (horizons < 1).any() or (horizons > 3).any():
        raise ValueError('Finite scores and horizons1..3 required')
    _, inv = np.unique(locations,return_inverse=True)
    grid = np.full((3,int(inv.max())+1),-np.inf)
    keys=(horizons-1)*grid.shape[1]+inv
    if len(np.unique(keys)) != len(keys):raise ValueError('Duplicate location/horizon')
    grid[horizons-1,inv]=scores
    coherent=np.maximum.accumulate(grid,axis=0)
    return coherent[horizons-1,inv]
