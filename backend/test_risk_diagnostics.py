"""Research overlays must stay bound to their model, chronology and archive inputs."""
from copy import deepcopy
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend import risk_diagnostics as risk


@pytest.fixture
def research(tmp_path, monkeypatch):
    models = tmp_path / "models"; models.mkdir()
    for name in ("ensemble_blend_fixture.json", "ensemble_blend_fixture_meta.json", "wf_2025.json"):
        (models / name).write_text('{}')
    artifact = {"version": 1, "source_file": "ensemble_blend_fixture.json",
                "source_sha256": risk.fingerprint(models / "ensemble_blend_fixture.json"),
                "source_meta_sha256": risk.fingerprint(models / "ensemble_blend_fixture_meta.json"),
                "base_ml_sha256": risk.fingerprint(models / "wf_2025.json"),
                "calibration_outcomes_through_date": "2025-05-31", "calibration_outcomes_through_year": 2025,
                "test_year": 2025, "target": "test cell target", "horizons": {
                    str(h): {"slope": 1., "intercept": 0., "supported": True} for h in [1, 2, 3]}}
    ap = tmp_path / 'calibrator.json'; ap.write_text(json.dumps(artifact))
    rp = tmp_path / 'report.json'; rp.write_text(json.dumps({"artifact_sha256": risk.fingerprint(ap),
        "n_calibration_cases": 45, "n_test_cases": 51, "horizons": {}, "limits": ["fixture, not real validation"]}))
    monkeypatch.setattr(risk, 'MODEL_DIR', models); monkeypatch.setattr(risk, 'ARTIFACT_PATH', ap); monkeypatch.setattr(risk, 'REPORT_PATH', rp)
    polygon = {"type": "Polygon", "coordinates": [[[1., 1.], [1.05, 1.], [1.05, 1.05], [1., 1.05], [1., 1.]]]}
    body = {"ignition": {"mode": "archive"}, "guidance": {"history": {"reduced": False},
        "model": {"blender": "ensemble_blend_fixture", "fingerprints": risk.load_diagnostics()[1]['fingerprints']},
        "query": {"date": "2025-07-01"}, "features": [
            {"type": "Feature", "geometry": deepcopy(polygon), "properties": {"kind": "combined_forecast", "day": h, "combined_probability": p}}
            for h, p in [(1, .9), (2, .1)]]}}
    return body, models


def test_coherent_overlay_levels_and_original_forecast_are_separate(research):
    body, _ = research; before = deepcopy(body)
    result = risk.calibrate_guidance(body)
    assert body == before
    assert result['status'] == 'research_candidate_not_promoted'
    assert [f['properties']['research_probability'] for f in result['features']] == [.9, .9]
    assert result['coherence_adjusted_cells'] == 1
    assert {f['properties']['level'] for f in result['contours']['features']} == {.5, .8}
    assert all(p['thresholds'][-1]['cells'] == 0 for p in result['per_day'])
    assert any('not confidence intervals' in s for s in result['warnings'])


@pytest.mark.parametrize('change', ['live', 'reduced', 'no_history', 'pre_cutoff', 'other_year', 'wrong_hash', 'missing_hash', 'invalid_score', 'duplicate'])
def test_rejects_unsupported_inputs(research, change):
    body, _ = research; g = body['guidance']
    if change == 'live': body['ignition']['mode'] = 'live'
    elif change == 'reduced': g['history']['reduced'] = True
    elif change == 'no_history': del g['history']['reduced']
    elif change == 'pre_cutoff': g['query']['date'] = '2025-05-28'
    elif change == 'other_year': g['query']['date'] = '2026-07-01'
    elif change == 'wrong_hash': g['model']['fingerprints']['ml_sha256'] = 'wrong'
    elif change == 'missing_hash': del g['model']['fingerprints']
    elif change == 'invalid_score': g['features'][0]['properties']['combined_probability'] = 1.1
    else: g['features'].append(deepcopy(g['features'][0]))
    with pytest.raises(ValueError): risk.calibrate_guidance(body)


def test_changed_model_or_artifact_cannot_use_old_metrics(research):
    _, models = research
    (models / 'wf_2025.json').write_text('{"modified": true}')
    assert risk.load_diagnostics()[1]['model_matches'] is False
    with pytest.raises(ValueError, match='installed model'):
        risk.calibrate_guidance(research[0])


def test_api_missing_artifacts_and_schema_errors(research, monkeypatch, tmp_path):
    app = FastAPI(); app.include_router(risk.router)
    with TestClient(app) as client:
        assert client.get('/risk/diagnostics').json()['model_matches']
        assert client.post('/risk/calibrate', json=research[0]).status_code == 200
        for body in ({'guidance': []}, {'guidance': {'history': []}}, {'guidance': {'history': {'reduced': False}, 'model': []}}):
            assert client.post('/risk/calibrate', json=body).status_code == 422
        assert client.post('/risk/calibrate', content=b'x' * (6*1024*1024+1)).status_code == 413
        monkeypatch.setattr(risk, 'ARTIFACT_PATH', tmp_path/'missing.json')
        assert client.get('/risk/diagnostics').json()['available'] is False
        assert client.post('/risk/calibrate', json=research[0]).status_code == 503
