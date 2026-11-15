"""#12 (experiment): sub-pixel fire fraction from the VIIRS 375 m channels I4 (3.74 um, bright_ti4) and I5 (11.45 um, bright_ti5).

Dozier (1981) mixed-pixel idea with ONE simplifying assumption each: fire temperature Tf (default 900 K, a flaming-front value; 600-1200 K
in the literature) and background temperature Tb = bright_ti5 (11 um is hardly affected by a small fire). Then at 3.74 um
    p = (B(T4) - B(Tb)) / (B(Tf) - B(Tb))        fire fraction of the pixel,   A_fire = p * scan * track (km2)
This is a consistency check of what the FIRMS fields imply, NOT a validated retrieval: no independent fire-area truth is used.
We report (1) the distribution of p, (2) how well p*A_pix tracks the FIRMS FRP (Spearman; partly circular since both come from the same radiances),
(3) the sensitivity of the total fire area to the assumed Tf (600 / 900 / 1200 K), which shows how uncertain the absolute area is.
Usage: .venv/bin/python scripts/subpixel_fire_fraction.py [--file data_country/viirs-snpp/2024/viirs-snpp_2024_Australia.csv] [--n 200000]
"""
import argparse, json
from pathlib import Path
import numpy as np, pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent
C1, C2 = 1.19104e-16, 1.4388e-2          # W m2 sr-1, m K


def planck(lam_um, T):
    lam = lam_um * 1e-6
    return C1 / (lam ** 5 * (np.exp(C2 / (lam * np.asarray(T, float))) - 1.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "data_country/viirs-snpp/2024/viirs-snpp_2024_Australia.csv")); ap.add_argument("--n", type=int, default=200000)
    a = ap.parse_args()
    df = pd.read_csv(a.file, usecols=["bright_ti4", "bright_ti5", "scan", "track", "frp", "daynight", "confidence", "type"])
    df = df[(df["type"] == 0) & (df["daynight"] == "N") & df["confidence"].isin(["n", "h"])].dropna()    # night: no reflected sunlight in 3.74 um
    if len(df) > a.n:
        df = df.sample(a.n, random_state=0)
    T4, T5 = df["bright_ti4"].to_numpy(float), df["bright_ti5"].to_numpy(float)
    area_pix = (df["scan"] * df["track"]).to_numpy(float)
    res = {"file": Path(a.file).name, "n_pixels_night_nominal_high": int(len(df)), "assumptions": "Tb = bright_ti5, Tf as listed; night pixels only (no reflected sunlight at 3.74 um)", "by_Tf": {}}
    for Tf in (600, 900, 1200):
        p = (planck(3.74, T4) - planck(3.74, T5)) / (planck(3.74, Tf) - planck(3.74, T5))
        p = np.clip(p, 0, 1)
        Af = p * area_pix                                   # km2
        sp = spearmanr(Af, df["frp"].to_numpy(float)).statistic
        res["by_Tf"][str(Tf)] = {"median_fire_fraction_percent": round(float(np.median(p) * 100), 3), "p90_fire_fraction_percent": round(float(np.percentile(p, 90) * 100), 3),
                                 "share_pixels_over_5_percent": round(float((p > 0.05).mean()), 3), "total_fire_area_km2": round(float(Af.sum()), 2),
                                 "spearman_with_FRP": round(float(sp), 3)}
    tot = [v["total_fire_area_km2"] for v in res["by_Tf"].values()]
    res["total_area_spread_factor_600_vs_1200K"] = round(max(tot) / max(1e-9, min(tot)), 2)
    res["note"] = ("Consistency check, not a validated retrieval: Tf is assumed, Tb is a proxy, no independent fire-area truth. The absolute area scales roughly with "
                   "1/(B(Tf)-B(Tb)), so it is uncertain by the factor shown. Useful as a relative size indicator within one sensor, not as a measurement.")
    (ROOT / "reports" / "subpixel_fire_fraction.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res))


if __name__ == "__main__":
    main()
