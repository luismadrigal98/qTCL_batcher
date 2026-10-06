"""Automatic lane / band layout for 9-lane, 3-band anthocyanin plates (3 standards + 6 samples).

Detection runs on a "magenta-ness" map, (R + B) / 2 - G: pink/purple pigments score high, while the
brown origin/front spots and grey pencil marks (ticks, labels, baseline) score ~0. Quantification does
NOT use this map; it only places the rectangles.

All rectangles are in full-image pixel coordinates as (x0, x1, y0, y1).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi
from scipy.signal import find_peaks

N_LANES = 9
N_BANDS = 3
STANDARD_LANES = 3


@dataclass
class Layout:
    """Rectangles for every lane/band; `rects[lane, band] = (x0, x1, y0, y1)`. Band 0 is the top band."""
    rects: np.ndarray
    lane_centers: np.ndarray
    lane_spacing: float
    notes: list[str] = field(default_factory=list)

    @property
    def n_lanes(self) -> int:
        return self.rects.shape[0]

    def bounds(self, margin: int = 150):
        r = self.rects
        return (int(r[..., 0].min() - margin), int(r[..., 2].min() - margin),
                int(r[..., 1].max() + margin), int(r[..., 3].max() + margin))


def magenta_map(rgb: np.ndarray, sigma: float = 3.0, max_object_width: float = 0.12) -> np.ndarray:
    """Magenta-ness map with large foreign objects (e.g. a pink pencil left on the bench) suppressed.

    A pigment band plus its smear is narrow (well under one lane pitch). Connected magenta regions wider
    than `max_object_width` x image width, or taller than half the image, cannot be lane streaks and are
    flattened to the background level so they cannot hijack lane / band detection.
    """
    f = rgb.astype(np.float32)
    m = ndi.gaussian_filter((f[..., 0] + f[..., 2]) / 2 - f[..., 1], sigma)
    base = float(np.median(m))
    labels, _ = ndi.label(m - base > 10)
    h, w = m.shape
    for lab, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is None:
            continue
        sh, sw = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if sw > max_object_width * w or sh > 0.5 * h:
            region = ndi.binary_dilation(labels[sl] == lab, iterations=15)
            m[sl][region] = base
    return m


def detect_lane_centers(m: np.ndarray, n_lanes: int = N_LANES, pitch_range=None, return_score: bool = False):
    """Fit a regular comb of `n_lanes` lane centres to the column profile (lanes are spotted on a regular pitch).

    The score is peak-vs-valley contrast, sum_k [prof(c_k) - mean(prof(c_k -/+ pitch/2))], so that combs
    whose "lanes" fall on the gaps between real lanes (double / half pitch) are penalised. The pitch range is
    wide (4%..12% of the long image side) because the plate fills a different fraction of the frame per photo.
    """
    h, w = m.shape
    long_side = max(h, w)
    lo, hi = pitch_range or (int(.04 * long_side), int(.12 * long_side))
    pos = np.clip(m - np.median(m), 0, None)
    prof = ndi.gaussian_filter1d(pos[int(.1 * h):int(.9 * h)].sum(axis=0), 12)
    ks = np.arange(n_lanes)
    best = (-np.inf, 0, 0)
    for d in range(lo, hi + 1):
        x0 = np.arange(int(.02 * w), w - (n_lanes - 1) * d - int(.02 * w), 2)
        if x0.size == 0:
            continue
        idx = x0[:, None] + d * ks[None, :]
        left, right = np.clip(idx - d // 2, 0, w - 1), np.clip(idx + d // 2, 0, w - 1)
        score = (prof[idx] - 0.5 * (prof[left] + prof[right])).sum(axis=1)
        j = int(score.argmax())
        if score[j] > best[0]:
            best = (float(score[j]), int(x0[j]), d)
    sc, x0, d = best
    if d == 0:
        raise ValueError("no lane comb fits inside the image; check orientation / crop")
    centers = x0 + d * np.arange(n_lanes, dtype=float)
    return (centers, float(d), sc) if return_score else (centers, float(d))


def _row_profiles(m, centers, spacing):
    hw = int(0.15 * spacing)
    base = np.median(m)
    return np.array([ndi.gaussian_filter1d(np.clip(m[:, int(c - hw):int(c + hw)] - base, 0, None).mean(axis=1), 4)
                     for c in centers])


def _band_template(profs, n_bands: int = N_BANDS):
    """Band y-centres from the summed standard lanes; relax the prominence until `n_bands` peaks are found."""
    p = profs[:STANDARD_LANES].sum(axis=0)
    for frac in (0.25, 0.15, 0.08, 0.04):
        peaks, props = find_peaks(p, prominence=frac * p.max())
        if len(peaks) >= n_bands:
            keep = peaks[np.argsort(props["prominences"])[::-1][:n_bands]]
            return np.sort(keep).astype(float)
    raise ValueError("could not find the standard bands; check orientation / crop of the photo")


def _lane_shifts(profs, tmpl, centers, search: int):
    """Per-lane vertical shift of the band template; weak lanes inherit a linear fit (plate tilt)."""
    h = profs.shape[1]
    shifts, strength = np.zeros(len(centers)), np.zeros(len(centers))
    for k in range(len(centers)):
        scores = [profs[k, np.clip((tmpl + s).astype(int), 0, h - 1)].sum() for s in range(-search, search + 1)]
        strength[k], shifts[k] = max(scores), int(np.argmax(scores)) - search
    ok = strength > 0.35 * np.median(np.sort(strength)[-5:])
    if ok.sum() >= 3:
        a, b = np.polyfit(centers[ok], shifts[ok], 1)
        shifts = np.where(ok, shifts, a * centers + b)
    return shifts


def detect_layout(rgb: np.ndarray, half_width_frac: float = 0.27) -> Layout:
    m = magenta_map(rgb)
    centers, spacing = detect_lane_centers(m)
    profs = _row_profiles(m, centers, spacing)
    tmpl = _band_template(profs)
    shifts = _lane_shifts(profs, tmpl, centers, search=max(20, int(0.18 * spacing)))
    # band limits sit midway between neighbouring bands, so a rectangle never contains two bands
    edges = np.concatenate([[tmpl[0] - (tmpl[1] - tmpl[0]) / 2], (tmpl[:-1] + tmpl[1:]) / 2,
                            [tmpl[-1] + (tmpl[-1] - tmpl[-2]) / 2]])
    hw = half_width_frac * spacing
    rects = np.zeros((len(centers), len(tmpl), 4))
    for k, (c, s) in enumerate(zip(centers, shifts)):
        for b in range(len(tmpl)):
            rects[k, b] = (c - hw, c + hw, edges[b] + s, edges[b + 1] + s)
    return Layout(rects=rects, lane_centers=centers, lane_spacing=spacing)


def detect_rotation(rgb: np.ndarray):
    """Number of 90-degree counter-clockwise turns (0..3) that make the plate upright, plus notes.

    Axis: lanes must form a horizontal comb (compare comb contrast on the image and its 90-degree rotation).
    Flip: the brown solvent-front spots must lie ABOVE the pigment bands; if they lie below, turn 180 degrees.
    """
    notes: list[str] = []
    cands = {}
    for k in (0, 1):
        r = np.rot90(rgb, k)
        m = magenta_map(np.ascontiguousarray(r))
        try:
            centers, d, sc = detect_lane_centers(m, return_score=True)
        except ValueError:
            continue
        cands[k] = (sc, r, m, centers, d)
    if not cands:
        raise ValueError("could not determine plate orientation")
    k = max(cands, key=lambda c: cands[c][0])
    sc, r, m, centers, d = cands[k]
    f = r.astype(np.float32)
    gray = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    brown = ((f[..., 0] - f[..., 2]) > 25) & (gray < 130)
    x_lo, x_hi = int(centers[0] - d / 2), int(centers[-1] + d / 2)
    ys, _ = np.nonzero(brown[:, x_lo:x_hi])
    mag = np.clip(m - np.median(m), 0, None)[:, x_lo:x_hi]
    rows = mag.sum(axis=1)
    mag_y = float((rows * np.arange(len(rows))).sum() / max(rows.sum(), 1e-9))
    if ys.size > 200:
        flip = float(np.median(ys)) > mag_y
        notes.append("solvent-front spots %s the bands" % ("below" if flip else "above"))
    else:
        flip = False
        notes.append("no solvent-front spots found; orientation flip not verified")
    return (k + (2 if flip else 0)) % 4, notes
