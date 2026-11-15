"""Compare models at the same validation precision goal, without fitting on test data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score

from alerts1km import build, stack, choose_threshold, report
from equifire import alerts1km as A
from equifire.weather import ALERT_WEATHER_FEATURES


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--models', nargs='+', type=Path, required=True)
    ap.add_argument('--calibration-year', type=int, default=2022)
    ap.add_argument('--precision-goal', type=float, default=.73)
    ap.add_argument('--n-eval', type=int, default=400)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--output', type=Path, default=Path('reports/alerts_comparison.json'))
    args = ap.parse_args()
    metas = [json.loads((p / 'alerts1km_meta.json').read_text()) for p in args.models]
    first = metas[0]
    for meta in metas:
        for key in ('countries', 'test_years', 'radius_km'):
            if meta[key] != first[key]:
                ap.error(f'models must share {key}')
        if args.calibration_year <= max(meta['train_years']) or args.calibration_year >= min(meta['test_years']):
            ap.error('calibration year must be between training and test periods')
    if not 0 < args.precision_goal < 1:
        ap.error('precision goal must be between 0 and 1')
    # A superset of input columns; each model receives its own schema.
    enhanced = any(m.get('feature_version', 1) >= 2 for m in metas)
    weather = any(m.get('weather') for m in metas)
    csvs = {m.get('weather_csv') for m in metas if m.get('weather_csv')}
    if len(csvs) > 1:
        ap.error('models require different offline weather files; compare separately')
    csv = next(iter(csvs), None)
    cols = A.FEATURES + (A.EXTRA_FEATURES if enhanced else []) + (ALERT_WEATHER_FEATURES if weather else [])
    va = build(first['countries'], [args.calibration_year], args.n_eval, None, True,
               args.workers, first['radius_km'], weather, csv, enhanced)
    te = build(first['countries'], first['test_years'], args.n_eval, None, True,
               args.workers, first['radius_km'], weather, csv, enhanced)
    output = {'calibration_year': args.calibration_year, 'precision_goal': args.precision_goal,
              'radius_km': first['radius_km'], 'test_years': first['test_years'],
              'note': 'Retrospective benchmark. Shared calibration goal; no test threshold tuning.', 'models': {}}
    for path, meta in zip(args.models, metas):
        model_out = {}
        indices = [cols.index(c) for c in meta['features']]
        for h in (1, 3):
            Xv, yv = stack(va, h); Xt, yt = stack(te, h)
            booster = xgb.Booster()
            booster.load_model(path / meta['models'][str(h)]['file'])
            booster.set_param({'device': 'cpu', 'nthread': 4})
            pv = booster.predict(xgb.DMatrix(Xv[:, indices], feature_names=meta['features']))
            pt = booster.predict(xgb.DMatrix(Xt[:, indices], feature_names=meta['features']))
            threshold = choose_threshold(yv, pv, args.precision_goal)
            metrics = report(te, pt, threshold)[h]
            model_out[str(h)] = {'matched_threshold': threshold, 'validation': report(va, pv, threshold)[h],
                                 'test': metrics, 'test_ap': float(average_precision_score(yt, pt)),
                                 'test_auc': float(roc_auc_score(yt, pt)),
                                 'original_threshold_test': report(te, pt, meta['models'][str(h)]['threshold'])[h]}
            print(f'{path} h={h}: P={metrics["precision"]:.3f} R={metrics["recall"]:.3f}, AP={model_out[str(h)]["test_ap"]:.4f}', flush=True)
        output['models'][str(path)] = model_out
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
