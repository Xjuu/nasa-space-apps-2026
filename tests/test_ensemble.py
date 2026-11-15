"""Serving-contract and temporal tests for the unfinished Claude combined predictor."""
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault('EQUIFIRE_NO_LOAD', '1')
from backend import ensemble
from equifire import direct
from equifire.common import EPOCH
from equifire.ensemble_core import FEATS, case_rows

DAY = int((np.datetime64('2024-08-01') - EPOCH).astype(int))
CX, CY = 1000, 1500


class ML:
    def __init__(self):
        self.inputs = []

    def inplace_predict(self, X):
        self.inputs.append(X.copy())
        return np.full(len(X), .4, np.float32)


class Blend:
    def predict_proba(self, X):
        p = 1 / (1 + np.exp(-X[:, 0] - X[:, FEATS.index('ph_c')]))
        return np.c_[1-p, p]


def index(future=False):
    days = np.full(5, DAY, np.int32)
    x = np.array([CX, CX+1, CX, CX-1, CX])
    y = np.array([CY, CY, CY+1, CY, CY-1])
    if future:
        days, x, y = np.r_[days, DAY+1], np.r_[x, CX+2], np.r_[y, CY+1]
    n = len(days)
    return direct.FireIndex(_v=(days, x, y, np.ones(n), np.ones(n), np.full(n, 3)),
                            _m=(np.array([], int), np.array([], int), np.array([], int), np.array([], float)))


def pc(fi=None, case=None, days=3, **kw):
    return {'frac': np.full((3, days, direct.S, direct.S), .2),
            'arr_h': np.full((direct.S, direct.S), 12.), 'cover': np.ones((direct.S, direct.S)),
            'meta': {'ok': True, 'fuel': 'grass', 'm': 1.0}}


def bundle(ml=None):
    return (ml or ML(), {'test_from': 2024, 'features': direct.V2_FEATURES+['k'],
                         'rules': [['thr', .3]] * 3}, Blend(),
            {'fit_year': 2023, 'days': 3, 'rules_iou': [['thr', .3]] * 3,
             'rules_tol': [['thr', .1]] * 3, 'apply_ml_model': 'wf_2024'}, 'test-blend')


def predict(**kw):
    lat, lon = map(float, ensemble.cell_center(CX, CY))
    return ensemble.predict(lat, lon, '2024-08-01', fi=index(), bundle=bundle(), physics_fn=pc, **kw)


def test_shared_feature_order_and_physics_fraction():
    P = np.full((3, direct.S, direct.S), np.nan)
    P[:, 20, 21] = .4
    X, ids = case_rows(P, np.ones((direct.S, direct.S)), pc(), 3)
    assert X.shape == (3, len(FEATS)) and ids[:, 0].tolist() == [1, 2, 3]
    assert X[:, FEATS.index('ph_c')].tolist() == pytest.approx([.2]*3)
    assert X[:, FEATS.index('ml_logit')].tolist() == pytest.approx([np.log(.4/.6)]*3)


def test_combined_geojson_labels_all_three_values():
    out = predict()
    f = out['features'][0]
    assert out['type'] == 'FeatureCollection' and len(out['per_day']) == 3
    assert f['geometry']['type'] == 'Polygon'
    assert f['properties']['ml_probability'] == pytest.approx(.4)
    assert f['properties']['physics_burned_fraction'] == pytest.approx(.2)
    assert f['properties']['combined_probability'] > .4
    assert isinstance(f['properties']['selected'], bool)
    assert out['hourly_simulation_url'].startswith('/predict/simulate?')
    assert 'ignition=archive' in out['hourly_simulation_url']
    assert out['method']['probabilities_calibrated'] is False


def test_future_observations_cannot_change_served_predictions():
    ml = ML(); b = bundle(ml)
    lat, lon = map(float, ensemble.cell_center(CX, CY))
    a = ensemble.predict(lat, lon, '2024-08-01', fi=index(), bundle=b, physics_fn=pc)
    z = ensemble.predict(lat, lon, '2024-08-01', fi=index(future=True), bundle=b, physics_fn=pc)
    np.testing.assert_allclose(ml.inputs[0], ml.inputs[1], equal_nan=True)
    assert a['features'] == z['features']


def test_later_fitted_model_rejected():
    b = list(bundle()); b[3] = {**b[3], 'fit_year': 2024}
    lat, lon = map(float, ensemble.cell_center(CX, CY))
    with pytest.raises(ensemble.PredictorUnavailable, match='cutoff'):
        ensemble.predict(lat, lon, '2024-08-01', fi=index(), bundle=b, physics_fn=pc)


def test_no_ignition_and_failed_physics_are_explicit():
    with pytest.raises(ensemble.NoIgnition):
        ensemble.nearest_fire(index(), DAY, CX+15, CY+15)
    lat, lon = map(float, ensemble.cell_center(CX, CY))
    with pytest.raises(ensemble.PredictorUnavailable, match='Physics input unavailable'):
        ensemble.predict(lat, lon, '2024-08-01', fi=index(), bundle=bundle(),
                         physics_fn=lambda *a, **k: {'meta': {'ok': False, 'errors': ['test unavailable']}})


def test_model_loader_refuses_future_only_artifact(tmp_path):
    meta = {'fit_year': 2025, 'features': FEATS}
    (tmp_path/'ensemble_blend_2026_meta.json').write_text(json.dumps(meta))
    with pytest.raises(ensemble.PredictorUnavailable):
        ensemble.load_bundle(str(tmp_path), 2024)


def test_live_feed_discards_detections_after_as_of_and_preserves_modis():
    asof = pd.Timestamp('2026-11-14T10:00')
    day = int((asof.to_datetime64().astype('datetime64[D]') - EPOCH).astype(int))
    d = pd.DataFrame({'day': [day]*3, 'gx': [CX]*3, 'gy': [CY]*3, 'frp': [1., 2., 100.],
                      'sat': ['N20', 'MODIS', 'N20'],
                      't': pd.to_datetime(['2026-11-14T09:00', '2026-11-14T09:05', '2026-11-14T11:00'])})
    fi, history = ensemble.live_index(d, day, CX, CY, asof)
    assert len(history) == 2
    assert fi.v.vals['frp'].tolist() == [1.]
    assert fi.m.vals['frp'].tolist() == [2.]


def test_predict_endpoint_validation_and_unavailable(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import app
    def missing(*args, **kwargs):
        raise ensemble.PredictorUnavailable('no fitted blender')
    monkeypatch.setattr(ensemble, 'predict', missing)
    client = TestClient(app)
    assert client.get('/predict?lat=95&lon=0&date=2024-08-01').status_code == 422
    r = client.get('/predict?lat=0&lon=0&date=2024-08-01')
    assert r.status_code == 503 and r.json()['detail'] == 'no fitted blender'


def test_modis_teacher_cutoff_checked(tmp_path, monkeypatch):
    from equifire import modis_era
    monkeypatch.setattr(modis_era, 'MODELS', tmp_path)
    (tmp_path/'harmonize_meta.json').write_text(json.dumps({'train_years': [2019], 'val_years': [2020]}))
    assert modis_era.reconstruction_provenance(2023)['teacher_fit_and_validation_years'] == [2019, 2020]
    with pytest.raises(ValueError, match='teacher'):
        modis_era.reconstruction_provenance(2020)
