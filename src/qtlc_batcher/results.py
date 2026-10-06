"""From raw band integrals to calibrated sample values, quality flags and tidy tables."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .pipeline import PlateResult

BAND_NAMES = ["Band 1 (pelargonidin)", "Band 2 (cyanidin)", "Band 3 (delphinidin)"]
STANDARD_CONC = (500.0, 125.0, 31.25)   # S500, S125, S31.25 -- units of the standards (4-fold dilution series)
N_STANDARDS = 3
CALIBRATION_MODELS = ("linear", "loglog")


@dataclass
class Calibration:
    slope: np.ndarray        # [band]
    intercept: np.ndarray    # [band]
    r2: np.ndarray           # [band]
    conc: np.ndarray         # [band, sample] calibrated concentration (NaN if the curve is unusable)
    model: str = "linear"
    outside: np.ndarray = None  # [band, sample] -1 below the weakest / +1 above the strongest standard / 0 inside
    n_standards_used: np.ndarray = None  # [band] standards in the fit (loglog drops standards with zero signal)


def calibrate(values: np.ndarray, standard_conc=STANDARD_CONC, model: str = "linear") -> Calibration:
    """Per-band calibration on the plate's own standards, inverted for the samples.

    `values` is [band, lane]: the first three lanes are the standards, the rest are samples.
    - "linear": OLS line signal = slope*conc + intercept (what the qTLC paper does). Samples below the intercept
      are clipped to 0.
    - "loglog": power law log(signal) = intercept + slope*log(conc). On the benchmark plates the response is
      sub-linear (S500/S125 signal ratio ~2.4 instead of 4; exponent ~0.73), which a straight line through three
      standards does not capture. Samples with zero signal get 0. `slope`/`intercept`/`r2` are on the log scale.
    """
    if model not in CALIBRATION_MODELS:
        raise ValueError(f"calibration model must be one of {CALIBRATION_MODELS}")
    x = np.asarray(standard_conc, dtype=float)
    n_bands, n_lanes = values.shape
    slope, icpt, r2 = np.full(n_bands, np.nan), np.full(n_bands, np.nan), np.full(n_bands, np.nan)
    conc = np.full((n_bands, n_lanes - N_STANDARDS), np.nan)
    outside = np.zeros((n_bands, n_lanes - N_STANDARDS), dtype=int)
    n_used = np.full(n_bands, N_STANDARDS)
    for b in range(n_bands):
        y = values[b, :N_STANDARDS]
        ys = values[b, N_STANDARDS:]
        if np.ptp(y) == 0:
            continue
        outside[b] = np.where(ys > y.max(), 1, np.where(ys < y.min(), -1, 0))
        if model == "linear":
            a, c = np.polyfit(x, y, 1)
            slope[b], icpt[b], r2[b] = a, c, np.corrcoef(x, y)[0, 1] ** 2
            if a > 0:
                conc[b] = np.clip((ys - c) / a, 0, None)
        else:
            use = y > 0                           # a standard with no signal cannot sit on a power law
            if use.sum() < 2:
                continue
            lx, ly = np.log(x[use]), np.log(y[use])
            a, c = np.polyfit(lx, ly, 1)
            slope[b], icpt[b] = a, c
            r2[b] = np.corrcoef(lx, ly)[0, 1] ** 2 if use.sum() > 2 else np.nan   # 2 points: R2 meaningless
            n_used[b] = int(use.sum())
            if a > 0:
                conc[b] = np.where(ys > 0, np.exp((np.log(np.maximum(ys, 1e-12)) - c) / a), 0.0)
    return Calibration(slope, icpt, r2, conc, model, outside, n_used)


def quality_flags(res: PlateResult, cal: Calibration, rotated_turns: int = 0) -> list[str]:
    """Human-readable reasons a plate deserves a second look in the GUI."""
    flags = [f"layout: {n}" for n in res.layout.notes]
    std = res.values[:, :N_STANDARDS]
    for b in range(std.shape[0]):   # the standards carry all three pigments, so every band must dilute down
        if not (std[b, 0] > std[b, 1] > std[b, 2]):
            flags.append(f"{BAND_NAMES[b]}: standards are not decreasing S500 > S125 > S31.25")
    for b in range(res.values.shape[0]):
        if np.isfinite(cal.slope[b]) and cal.slope[b] <= 0:
            flags.append(f"{BAND_NAMES[b]}: standard curve slope <= 0, samples not calibrated")
        elif np.isfinite(cal.r2[b]) and cal.r2[b] < 0.95:
            flags.append(f"{BAND_NAMES[b]}: standard curve R2 = {cal.r2[b]:.2f} (< 0.95)")
    n_over = int(res.overflow_cols.sum()) if res.overflow_cols is not None else 0
    if n_over and res.params.uint8_wrap:
        cells = np.argwhere(res.overflow_cols > 0)
        where = ", ".join(f"band {b + 1}/lane {k + 1}" for b, k in cells[:4])
        flags.append(f"web-app uint8 overflow hit {n_over} columns ({where}): bands are under-integrated "
                     f"(re-run with uint8_wrap off for the correct value)")
    if res.top_touch is not None:
        streaky = [k for k in range(N_STANDARDS, res.values.shape[1])
                   if res.top_touch[0, k] >= 0.15 and res.values[0, k] > 1000]
        if streaky:
            flags.append("streak from the solvent front runs into the band-1 box of sample(s) " +
                         ", ".join(str(k - N_STANDARDS + 1) for k in streaky) +
                         ": band 1 (pelargonidin) may be smear, not pigment - check / tighten the box")
    if cal.n_standards_used is not None:
        for b in np.nonzero(cal.n_standards_used < N_STANDARDS)[0]:
            if np.isfinite(cal.slope[b]):
                flags.append(f"{BAND_NAMES[b]}: a standard has no signal; power law fitted through "
                             f"{cal.n_standards_used[b]} standards")
    if cal.outside is not None:
        above = [(b, k) for b, k in np.argwhere(cal.outside > 0) if res.values[b, k + N_STANDARDS] > 0]
        if above:
            flags.append("extrapolated above the S500 standard: " +
                         ", ".join(f"band {b + 1}/sample {k + 1}" for b, k in above[:6]) + (" ..." if len(above) > 6 else ""))
    if rotated_turns:
        flags.append(f"photo was rotated {90 * rotated_turns} degrees automatically")
    return flags


def plate_tables(res: PlateResult, plate: str | None = None, platemap: pd.DataFrame | None = None,
                 model: str = "linear"):
    """Long-format table (one row per sample lane x band) with raw and calibrated values."""
    cal = calibrate(res.values, model=model)
    rows = []
    n_lanes = res.values.shape[1]
    for k in range(n_lanes):
        is_std = k < N_STANDARDS
        lane = (f"Standard {k + 1} ({STANDARD_CONC[k]:g})" if is_std else f"Sample {k - N_STANDARDS + 1}")
        for b in range(res.values.shape[0]):
            row = dict(image=res.image, plate=plate, lane=lane, lane_index=k + 1,
                       sample_no=(np.nan if is_std else k - N_STANDARDS + 1), band=b + 1,
                       band_name=BAND_NAMES[b], integral=res.values[b, k],
                       calibrated=np.nan if is_std else cal.conc[b, k - N_STANDARDS],
                       calibration=model,
                       outside_standards=("" if is_std else {1: "above", -1: "below", 0: ""}[
                           int(cal.outside[b, k - N_STANDARDS])]))
            rows.append(row)
    df = pd.DataFrame(rows)
    if platemap is not None and plate is not None:
        df = df.merge(platemap[platemap["plate"] == plate].drop(columns=["plate"]), how="left", on="sample_no")
        mg_ok = df["flower_mg"].notna() & (df["flower_mg"] > 0)
        df["calibrated_per_mg"] = np.where(mg_ok, df["calibrated"] * (df["meoh_ul"] / 1000.0) / df["flower_mg"], np.nan)
    return df, cal


def export_workbook(path, items: list[dict], params) -> None:
    """Write the batch workbook. Each item: dict(res=PlateResult|None, plate=str|None, df=DataFrame|None,
    cal=Calibration|None, flags=list[str], image=str, error=str|None)."""
    tidy, wide, flags = [], [], []
    for it in items:
        if it.get("error"):
            flags.append(dict(image=it["image"], plate=it.get("plate"), flag=f"processing failed: {it['error']}"))
            continue
        res = it["res"]
        tidy.append(it["df"])
        for b in range(res.values.shape[0]):
            wide.append(dict(image=res.image, plate=it.get("plate"), band=BAND_NAMES[b],
                             **{f"lane {k + 1}": res.values[b, k] for k in range(res.values.shape[1])}))
        flags += [dict(image=res.image, plate=it.get("plate"), flag=f) for f in it.get("flags", [])]
    if not tidy:
        raise ValueError("nothing to export")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path) as xw:
        pd.concat(tidy).to_excel(xw, sheet_name="Tidy", index=False)
        pd.DataFrame(wide).to_excel(xw, sheet_name="Integrals", index=False)
        pd.DataFrame(flags or [dict(image="", plate="", flag="none")]).to_excel(xw, sheet_name="Flags", index=False)
        pd.DataFrame([vars(params)]).to_excel(xw, sheet_name="Parameters", index=False)
