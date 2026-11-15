"""EquiFire ML pipeline.

    python main.py                 # collect + train everything
    python main.py collect         # only aggregate downloaded CSVs into cache/
    python main.py train           # train on what is cached (collects new files first)
    python main.py direct          # only the direct multi-day spread model (best for forecast.py)
Options: --parallel (GPU ~95% + CPU ~65% at once, 18 GB RAM cap), --use-gpu (XGBoost on CUDA), --max-rows N (spread sample size), --weather-tiles N (0 = no weather), --skip size|spread
"""
import argparse
import time

from equifire.collect import collect


def _train_direct(cells, a):
    import json
    from equifire import direct
    from equifire.common import REPORTS
    rf = REPORTS / "results.json"
    test_from = json.loads(rf.read_text()).get("spread", {}).get("test_from_year", 2023) if rf.exists() else 2023
    print(f"\n[direct] training direct multi-day spread model (years < {test_from})", flush=True)
    direct.train(cells, test_from, n_cases=a.direct_cases, n_val=max(200, a.direct_cases // 10),
                 use_gpu=a.use_gpu or a.parallel, trials=a.direct_trials)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", nargs="?", default="all", choices=["all", "collect", "train", "direct"])
    ap.add_argument("--max-rows", type=int, default=3_000_000)
    ap.add_argument("--weather-tiles", type=int, default=40)
    ap.add_argument("--skip", nargs="*", default=[], choices=["size", "spread", "direct"])
    ap.add_argument("--direct-cases", type=int, default=6000,
                    help="fires used to train the direct multi-day spread model")
    ap.add_argument("--direct-trials", type=int, default=1, help="model-setting trials for the direct model")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--use-gpu", action="store_true", help="train with XGBoost on CUDA GPU")
    ap.add_argument("--use-pool-params", action="store_true",
                    help="use best model settings found by pool_server.py (reports/pool_best.json)")
    ap.add_argument("--parallel", action="store_true",
                    help="train on NVIDIA GPU (~95%%) and CPU (~65%%) at the same time, RAM capped at 18 GB")
    ap.add_argument("--trials", type=int, default=10, help="model-setting trials per lane in --parallel mode")
    a = ap.parse_args()
    if a.parallel:
        from equifire import parallel
        parallel.reexec_with_memory_cap()  # re-runs this command inside an 18 GB cgroup limit
    t0 = time.time()

    cells = collect(a.workers)
    if a.step == "collect":
        return

    from equifire import datasets, train, weather
    if a.step == "direct":
        _train_direct(cells, a)
        return
    train.USE_GPU = a.use_gpu
    if a.use_pool_params:
        import json
        from equifire.common import REPORTS
        best = json.loads((REPORTS / "pool_best.json").read_text())
        train.POOL_PARAMS = {k: v for k, v in best.items() if v}
        print(f"[pool] using pool settings for: {', '.join(train.POOL_PARAMS) or 'nothing yet'}")
    if a.use_gpu:
        print("[gpu] training on CUDA GPU with XGBoost")
    results, models = {}, {}
    if a.parallel:
        if a.skip:
            raise SystemExit("--parallel trains size and spread together; do not combine with --skip")
        from equifire import parallel
        parallel.RAM_GB = 18.0
        size_data = datasets.build_size(cells)
        X, label, year, meta = datasets.build_spread(cells, min(a.max_rows, 4_000_000))
        if a.weather_tiles:
            X = weather.attach_weather(X, meta, a.weather_tiles)
        pre_size, pre_spread = parallel.train_lanes((X, label, year), size_data, a.trials, use_gpu=True,
                                                    row_cap=a.max_rows)
        results["size"], m = train.train_size(*size_data, pre=pre_size)
        models.update({f"size_{k}": v for k, v in m.items()})
        results["spread"], models["spread"] = train.train_spread(X, label, year, pre=pre_spread)
    else:
        if "size" not in a.skip:
            X, Y, year = datasets.build_size(cells)
            results["size"], m = train.train_size(X, Y, year)
            models.update({f"size_{k}": v for k, v in m.items()})
        if "spread" not in a.skip:
            X, label, year, meta = datasets.build_spread(cells, a.max_rows)
            if a.weather_tiles:
                X = weather.attach_weather(X, meta, a.weather_tiles)
            results["spread"], models["spread"] = train.train_spread(X, label, year)
    train.save(models, results)
    train.print_report(results)
    if "direct" not in a.skip:
        _train_direct(cells, a)
    print(f"models -> models/, report -> reports/results.json, total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

