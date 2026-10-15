"""Combine repeat photos of the same plate.

Two photos of one plate measure the same chemistry, but on the current photos their calibrated sample values differ by
a median 18 % (raw signals 8 %), mostly because each photo refits its own 3-point standard curve. Combining them:

1. Grouping: photos with the same plate name form a group; consecutive photos (natural file order) join a group
   when their sample lanes (bands 2-3) correlate at r >= `r_min` = 0.80. Two differently named photos are never merged.
   On the current photos consecutive repeats correlate at r >= 0.92 and consecutive different plates at <= 0.53 (20
   repeat groups, checked against the handwritten labels). Photos of one plate taken far apart are only joined by
   name: non-consecutive pairs do not separate reliably (same plate down to 0.88, different plates up to 0.90).
2. Photos without a CHECK flag are preferred: if a group has any, the flagged ones are left out ("use the clean twin").
3. Each photo is calibrated on its own standards (as for single photos) and the photos are averaged: geometric mean
   for the log-log model, arithmetic mean for the linear one. A sample counts as detected if it is detected in at least
   half of the photos used; otherwise it is reported as 0.

Validation (66 current photos, plates with >= 3 photos, predicting a held-out photo of the same plate): one other
photo misses by a median 23 % (RMS log error 0.45); the average of the remaining photos by 16 % (0.36). Sharing one
curve shape across the photos of a plate made photos agree with each other more but predicted a held-out photo
slightly worse (17 %, 0.38), so it is not used.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .results import BAND_NAMES, N_STANDARDS, calibrate, needs_check


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _profile(values: np.ndarray) -> np.ndarray:
    """Grouping signature: log signal of the SAMPLE lanes, bands 2-3. The standards look alike on every plate (they
    would make different plates correlate) and sample band 1 changes with streak handling, so both are left out."""
    return np.log1p(np.clip(values[1:, 3:], 0, None)).ravel()


def group_photos(items: list[dict], r_min: float = 0.80) -> list[dict]:
    """Groups of photos showing the same plate. Each item needs `image`, `res` (PlateResult) and optionally `plate`.

    Returns [dict(plate, images, how)] in natural file order; `plate` is the group's name (inherited by unnamed
    members) or None; `how` explains the grouping.
    """
    ok = sorted([it for it in items if it.get("res") is not None], key=lambda it: _natural_key(it["image"]))
    n = len(ok)
    parent = list(range(n))
    names = [{it["plate"]} if it.get("plate") else set() for it in ok]
    how = [set() for _ in range(n)]

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j, why):
        ri, rj = find(i), find(j)
        if ri == rj:
            return
        if names[ri] and names[rj] and names[ri] != names[rj]:
            return                                       # never merge two differently named plates
        parent[rj] = ri
        names[ri] |= names[rj]
        how[ri] |= how[rj] | {why}

    by_name: dict[str, int] = {}
    for i, it in enumerate(ok):
        if it.get("plate"):
            if it["plate"] in by_name:
                union(by_name[it["plate"]], i, "same plate name")
            else:
                by_name[it["plate"]] = i
    for i in range(n - 1):
        r = float(np.corrcoef(_profile(ok[i]["res"].values), _profile(ok[i + 1]["res"].values))[0, 1])
        if r >= r_min:
            union(i, i + 1, f"consecutive photos with matching bands (r={r:.3f})")

    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    groups = []
    for root, members in comps.items():
        nm = sorted(names[root])
        groups.append(dict(plate=nm[0] if nm else None, images=[ok[i]["image"] for i in members],
                           how="; ".join(sorted(how[root])) if len(members) > 1 else "single photo"))
    return sorted(groups, key=lambda g: _natural_key(g["images"][0]))


def combine_plates(items: list[dict], model: str = "loglog", r_min: float = 0.80):
    """Plate-level table (one row per plate x sample x band) and the photo groups. See the module docstring."""
    by_image = {it["image"]: it for it in items if it.get("res") is not None}
    groups = group_photos(list(by_image.values()), r_min)
    rows, group_rows = [], []
    for g in groups:
        members = [by_image[i] for i in g["images"]]
        clean = [it for it in members if not needs_check(it.get("flags", []))]
        used = clean or members
        left_out = [it["image"] for it in members if it not in used]
        res_list = [it["res"] for it in used]
        vals = np.stack([r.values for r in res_list])              # [photo, band, lane]
        lods = np.stack([r.lod if r.lod is not None else np.zeros(vals.shape[1]) for r in res_list])
        info = None
        for it in used:                                            # plate-map columns from the photo's Tidy rows
            df = it.get("df")
            if df is not None and "plant" in df.columns:
                info = df[df["sample_no"].notna()].drop_duplicates("sample_no").set_index("sample_no")
                break
        n_s = vals.shape[2] - N_STANDARDS
        cals = np.stack([calibrate(v, model=model, lod=l).conc for v, l in zip(vals, lods)])   # [photo, band, sample]
        for b in range(vals.shape[1]):
            conc = cals[:, b, :]
            det = (vals[:, b, N_STANDARDS:] >= lods[:, b][:, None]) & (vals[:, b, N_STANDARDS:] > 0)
            for s in range(n_s):
                c, d = conc[:, s], det[:, s]
                n_det = int(d.sum())
                good = d & np.isfinite(c) & (c > 0)
                if n_det * 2 < len(used) or not good.any():
                    value = 0.0 if np.isfinite(c).any() else np.nan
                    spread = np.nan
                elif model == "loglog":
                    lc = np.log(c[good])
                    value = float(np.exp(lc.mean()))
                    spread = float(np.expm1(lc.std(ddof=1))) if good.sum() > 1 else np.nan
                else:
                    value = float(c[good].mean())
                    spread = float(c[good].std(ddof=1) / value) if good.sum() > 1 and value > 0 else np.nan
                row = dict(plate=g["plate"], photos=", ".join(it["image"] for it in used), n_photos=len(used),
                           sample_no=s + 1, band=b + 1, band_name=BAND_NAMES[b], calibrated=value,
                           detected_in=f"{n_det}/{len(used)}", photo_spread=spread, calibration=model)
                if info is not None and (s + 1) in info.index:
                    for col in ("parent", "plant", "meoh_ul", "flower_mg"):
                        if col in info.columns:
                            row[col] = info.loc[s + 1, col]
                    mg, ul = row.get("flower_mg"), row.get("meoh_ul")
                    row["calibrated_per_mg"] = value * (ul / 1000.0) / mg if mg and mg > 0 and ul == ul else np.nan
                rows.append(row)
        group_rows.append(dict(plate=g["plate"], photos=", ".join(g["images"]), used=", ".join(it["image"] for it in used),
                               left_out_flagged=", ".join(left_out), how=g["how"]))
    return pd.DataFrame(rows), pd.DataFrame(group_rows)
