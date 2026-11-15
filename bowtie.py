"""EquiFire goal 2: bow-tie / swath-edge duplicate clusters and footprint-adaptive clustering.

    .venv/bin/python bowtie.py eval [--countries Brazil,Bolivia,Australia] [--year 2023]
    .venv/bin/python bowtie.py geometry --lat -10 --lon -55 --time 2026-11-13T07:47 --sat Aqua [--scan 2.2 --track 1.4]

eval: raw FIRMS archive CSVs (data_country/), type==0. Geometry from FIRMS pixel sizes (archive data is
years older than the TLEs, so SGP4 is not used - see equifire/geometry.py). For each method in
equifire.clusters.METHODS it reports, per MODIS scan-angle bin:
  * detections per cluster and total clusters (all MODIS overpasses),
  * coincident Aqua vs VIIRS (S-NPP / NOAA-20 within +-30 min, same date) in 0.1 deg boxes:
    - ratio   = MODIS clusters / VIIRS clusters (VIIRS clustered adaptively, 375 m: the reference),
    - per_event = mean MODIS clusters in boxes where VIIRS sees exactly one fire cluster
      (ideal ~1; values > 1 are duplicate / fragmented clusters of one fire).
Writes reports/bowtie_eval.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

import numpy as np
import pandas as pd

from equifire import clusters as C
from equifire import geometry as G
from equifire.common import REPORTS, ROOT

BINS = [0, 15, 30, 40, 48, 56]
BOX = 0.1
COINC_MIN = 30


def _bin_labels():
    return [f"{a}-{b}" for a, b in zip(BINS[:-1], BINS[1:])]


def _bin(x):
    return pd.cut(x, BINS, labels=_bin_labels(), include_lowest=True, right=False)


def _minutes(t):
    t = np.asarray(t, int)
    return (t // 100) * 60 + t % 100


def _load(country: str, year: int):
    paths = {
        "modis": ROOT / f"data_country/modis/{year}/modis_{year}_{country}.csv",
        "snpp": ROOT / f"data_country/viirs-snpp/{year}/viirs-snpp_{year}_{country}.csv",
        "noaa20": ROOT / f"data_country/viirs-jpss1/{year}/viirs-jpss1_{year}_{country}.csv",
    }
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    m = C.load_firms(paths["modis"])
    v = pd.concat([C.load_firms(paths["snpp"]), C.load_firms(paths["noaa20"])], ignore_index=True)
    return m, v


def _box_counts(cl: pd.DataFrame, gid_col: str) -> pd.DataFrame:
    by = cl.assign(by=np.floor(cl["latitude"] / BOX).astype(int), bx=np.floor(cl["longitude"] / BOX).astype(int))
    return by.groupby([gid_col, "by", "bx"]).size().rename("n").reset_index()


def evaluate_country(country: str, year: int) -> dict:
    t0 = time.time()
    m, v = _load(country, year)
    m["gid"] = C.overpass_ids(m)
    v["gid"] = C.overpass_ids(v)
    print(f"[{country} {year}] MODIS {len(m):,} VIIRS {len(v):,} detections, "
          f"{m.gid.nunique():,} / {v.gid.nunique():,} overpasses ({time.time() - t0:.1f}s)", flush=True)
    out: dict = {"country": country, "year": year, "n_modis": len(m), "n_viirs": len(v)}

    # --- coincident Aqua vs VIIRS overpasses (same date, |dt| <= 30 min)
    mo = m.groupby("gid").agg(sat=("sat_key", "first"), date=("acq_date", "first"), t=("acq_time", "min")).reset_index()
    vo = v.groupby("gid").agg(sat=("sat_key", "first"), date=("acq_date", "first"), t=("acq_time", "min")).reset_index()
    pair = mo[mo.sat == "aqua"].merge(vo, on="date", suffixes=("_m", "_v"))
    pair = pair[np.abs(_minutes(pair.t_m) - _minutes(pair.t_v)) <= COINC_MIN]
    pair = pair.sort_values("gid_v").drop_duplicates("gid_v")       # each VIIRS overpass -> one Aqua overpass
    vmap = dict(zip(pair.gid_v, pair.gid_m))
    out["coincident_overpass_pairs"] = int(len(pair))
    out["coincident_by_viirs_sat"] = pair.sat_v.value_counts().to_dict()
    out["median_abs_dt_min"] = float(np.median(np.abs(_minutes(pair.t_m) - _minutes(pair.t_v)))) if len(pair) else None

    v = v[v.gid.isin(vmap)].reset_index(drop=True)                 # only VIIRS overpasses used as reference
    out["n_viirs_coincident"] = len(v)
    v_lab = C.cluster(v, "adaptive", gid=v["gid"].to_numpy())
    vcl = C.summarize(v, v_lab).assign(gid=v.groupby(v_lab)["gid"].first().to_numpy())
    vcl = vcl[vcl.gid.isin(vmap)].assign(gid_m=lambda d: d.gid.map(vmap))
    vbox = _box_counts(vcl, "gid_m").rename(columns={"n": "n_v"})
    # MODIS scan angle of each (overpass, box): from detections, identical for every method
    mbox_scan = (m.assign(by=np.floor(m.latitude / BOX).astype(int), bx=np.floor(m.longitude / BOX).astype(int))
                   .groupby(["gid", "by", "bx"]).scan_deg.mean().rename("scan_m").reset_index()
                   .rename(columns={"gid": "gid_m"}))
    vscan = (vcl.assign(by=np.floor(vcl.latitude / BOX).astype(int), bx=np.floor(vcl.longitude / BOX).astype(int))
                .groupby(["gid_m", "by", "bx"]).scan_deg.mean().rename("scan_v").reset_index())

    out["methods"] = {}
    for meth in C.METHODS:
        lab = C.cluster(m, meth, gid=m["gid"].to_numpy())
        cl = C.summarize(m, lab).assign(gid=m.groupby(lab)["gid"].first().to_numpy())
        cl["bin"] = _bin(cl.scan_deg)
        det_bin = _bin(m.scan_deg).value_counts().sort_index()
        all_tab = cl.groupby("bin", observed=False).agg(clusters=("n", "size"), det_in_clusters=("n", "sum"))
        all_tab["det_per_cluster"] = all_tab.det_in_clusters / all_tab.clusters.replace(0, np.nan)
        # coincident boxes
        mc = cl[cl.gid.isin(set(vmap.values()))].rename(columns={"gid": "gid_m"})
        mbox = _box_counts(mc, "gid_m").rename(columns={"n": "n_m"})
        both = mbox.merge(vbox, on=["gid_m", "by", "bx"]).merge(mbox_scan, on=["gid_m", "by", "bx"], how="left") \
                   .merge(vscan, on=["gid_m", "by", "bx"], how="left")
        both["bin"] = _bin(both.scan_m)
        co = both.groupby("bin", observed=False).agg(boxes=("n_m", "size"), modis_clusters=("n_m", "sum"),
                                                     viirs_clusters=("n_v", "sum"), viirs_scan=("scan_v", "mean"))
        co["ratio"] = co.modis_clusters / co.viirs_clusters.replace(0, np.nan)
        one = both[both.n_v == 1].groupby("bin", observed=False).agg(boxes_1event=("n_m", "size"),
                                                                     per_event=("n_m", "mean"),
                                                                     frac_dup=("n_m", lambda s: float((s > 1).mean()) if len(s) else np.nan))
        multi = both[both.n_v >= 2].groupby("bin", observed=False).agg(
            boxes_multi=("n_m", "size"), frac_multi_merged=("n_m", lambda s: float((s == 1).mean()) if len(s) else np.nan))
        tab = all_tab.join(co).join(one).join(multi)
        tab.insert(0, "detections", det_bin.reindex(tab.index).to_numpy())
        out["methods"][meth] = {
            "total_clusters": int(len(cl)),
            "by_scan_bin": json.loads(tab.reset_index().rename(columns={"bin": "scan_bin"}).to_json(orient="records")),
        }
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _pooled(results: list[dict]) -> dict:
    """Sum counts over countries per method and bin, recompute ratios."""
    pooled = {}
    for meth in C.METHODS:
        rows = pd.concat([pd.DataFrame(r["methods"][meth]["by_scan_bin"]) for r in results])
        rows["pe_sum"] = rows.per_event.fillna(0) * rows.boxes_1event.fillna(0)
        rows["dup_sum"] = rows.frac_dup.fillna(0) * rows.boxes_1event.fillna(0)
        rows["mrg_sum"] = rows.frac_multi_merged.fillna(0) * rows.boxes_multi.fillna(0)
        g = rows.groupby("scan_bin", sort=False)[["detections", "clusters", "det_in_clusters", "boxes", "modis_clusters",
                                                  "viirs_clusters", "boxes_1event", "pe_sum", "dup_sum",
                                                  "boxes_multi", "mrg_sum"]].sum()
        g["det_per_cluster"] = g.det_in_clusters / g.clusters
        g["ratio_modis_viirs"] = g.modis_clusters / g.viirs_clusters
        g["modis_clusters_per_viirs_event"] = g.pe_sum / g.boxes_1event
        g["frac_events_duplicated"] = g.dup_sum / g.boxes_1event
        g["frac_multi_fire_boxes_merged"] = g.mrg_sum / g.boxes_multi
        g = g.drop(columns=["pe_sum", "dup_sum", "mrg_sum", "det_in_clusters"])
        pooled[meth] = g.round(4).reset_index().to_dict(orient="records")
    return pooled


def _print_tables(pooled: dict):
    cols = [("clusters", "{:>8.0f}"), ("det_per_cluster", "{:>7.2f}"), ("ratio_modis_viirs", "{:>7.3f}"),
            ("modis_clusters_per_viirs_event", "{:>7.3f}"), ("frac_events_duplicated", "{:>7.3f}"),
            ("frac_multi_fire_boxes_merged", "{:>7.3f}")]
    print("\nPooled, by MODIS scan-angle bin (deg).  clus = MODIS clusters, d/c = detections per cluster,")
    print("M/V = MODIS/VIIRS clusters in coincident 0.1-deg boxes, c/ev = MODIS clusters per single VIIRS fire,")
    print("dup = share of single-VIIRS-fire boxes split into >1 MODIS cluster,")
    print("merged = share of boxes with >=2 VIIRS fires that MODIS reports as ONE cluster (over-merge check)\n")
    bins = [r["scan_bin"] for r in pooled[C.METHODS[0]]]
    head = f"{'method':<13}{'bin':>7}" + "".join(f"{h:>9}" for h in ["clus", "d/c", "M/V", "c/ev", "dup", "merged"])
    print(head)
    for meth in C.METHODS:
        for r in pooled[meth]:
            print(f"{meth:<13}{r['scan_bin']:>7}" + "".join(" " + fmt.format(r[c]).rjust(8) for c, fmt in cols))
        print()
    print("edge/nadir (48-56 vs 0-15):")
    for meth in C.METHODS:
        rr = {r["scan_bin"]: r for r in pooled[meth]}
        a, b = rr[bins[0]], rr[bins[-1]]
        print(f"  {meth:<13} M/V {b['ratio_modis_viirs'] / a['ratio_modis_viirs']:.2f}   "
              f"clusters/VIIRS-event {b['modis_clusters_per_viirs_event'] / a['modis_clusters_per_viirs_event']:.2f}   "
              f"det/cluster {b['det_per_cluster'] / a['det_per_cluster']:.2f}")


def cmd_eval(args):
    C.F_DEFAULT = args.f
    countries = [c.strip() for c in args.countries.split(",") if c.strip()]
    results = []
    for c in countries:
        try:
            results.append(evaluate_country(c, args.year))
        except FileNotFoundError as e:
            print(f"skip {c}: missing {e}", file=sys.stderr)
    if not results:
        sys.exit("no data")
    pooled = _pooled(results)
    base = {r["scan_bin"]: r["clusters"] for r in pooled["fixed_nadir"]}
    reduction = {meth: {r["scan_bin"]: round(1 - r["clusters"] / base[r["scan_bin"]], 4) for r in pooled[meth]}
                 for meth in C.METHODS}
    report = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "geom_source": "pixel_size (FIRMS scan/track inverted through the MODIS/VIIRS footprint model); "
                       "SGP4 not used for archive data (TLE epoch years after the detections)",
        "scan_bins_deg": BINS, "box_deg": BOX, "coincidence_min": COINC_MIN,
        "neighbour_factor_f": C.F_DEFAULT, "overpass_gap_min": C.OVERPASS_GAP_MIN,
        "methods_doc": C.__doc__,
        "pooled": pooled, "cluster_reduction_vs_fixed_nadir": reduction, "per_country": results,
    }
    from pathlib import Path
    f = Path(args.out)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(report, indent=1, default=float))
    _print_tables(pooled)
    print("\ncluster-count reduction vs fixed_nadir, by bin:")
    for meth in C.METHODS[1:]:
        print(f"  {meth:<13}" + "  ".join(f"{k}:{v:+.1%}" for k, v in reduction[meth].items()))
    print(f"\nwrote {f}")


def cmd_geometry(args):
    t = dt.datetime.fromisoformat(args.time)
    print(json.dumps(G.detection_geometry(args.lat, args.lon, t, args.sat, args.scan, args.track, args.daynight), indent=1))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("eval")
    e.add_argument("--countries", default="Brazil,Bolivia,Australia")
    e.add_argument("--year", type=int, default=2023)
    e.add_argument("--f", type=float, default=C.F_DEFAULT, help="neighbour factor for adaptive/fixed_nadir")
    e.add_argument("--out", default=str(REPORTS / "bowtie_eval.json"))
    e.set_defaults(fn=cmd_eval)
    g = sub.add_parser("geometry")
    g.add_argument("--lat", type=float, required=True)
    g.add_argument("--lon", type=float, required=True)
    g.add_argument("--time", required=True, help="ISO UTC")
    g.add_argument("--sat", required=True)
    g.add_argument("--scan", type=float)
    g.add_argument("--track", type=float)
    g.add_argument("--daynight")
    g.set_defaults(fn=cmd_geometry)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
