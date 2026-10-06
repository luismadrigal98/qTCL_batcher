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
    tilt_deg: float = 0.0          # detected plate tilt (rectangles stay axis-aligned, like the web app)

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


def _line_map(gray: np.ndarray, f: int) -> np.ndarray:
    """Thin dark horizontal structures (pencil origin line, solvent front) on an f-times downsampled image."""
    h, w = gray.shape[0] // f * f, gray.shape[1] // f * f
    g = gray[:h, :w].astype(np.float32).reshape(h // f, f, w // f, f).mean(axis=(1, 3))
    return np.clip(ndi.grey_closing(g, size=(9, 1)) - g, 0, None)


def estimate_tilt(gray: np.ndarray, x_lo: float, x_hi: float, y_lo: float, y_hi: float, x_ref: float,
                  f: int = 4, max_deg: float = 5.0, step_deg: float = 0.05):
    """Plate tilt from the straight pencil lines (origin line, solvent front), by projection-profile search.

    Returns (slope dy/dx, profile, y_axis, peakiness): `profile` is the line strength along de-tilted rows
    y' = y - slope * (x - x_ref), sampled at full-resolution rows `y_axis`; peakiness = max / median of the
    profile (low when the plate has no usable straight lines, e.g. no origin line was drawn).
    """
    H, W = gray.shape
    x0, x1 = max(0, int(x_lo / f)), min(W // f, int(x_hi / f))
    y0, y1 = max(0, int(y_lo / f)), min(H // f, int(y_hi / f))
    L = _line_map(gray, f)[y0:y1, x0:x1]
    ys, xs = np.mgrid[y0:y1, x0:x1]
    pad = int(np.tan(np.deg2rad(max_deg)) * (x1 - x0)) + 2
    best = (-1.0, 0.0, None)
    for deg in np.arange(-max_deg, max_deg + 1e-9, step_deg):
        t = np.tan(np.deg2rad(deg))
        yy = np.round(ys - t * (xs - x_ref / f)).astype(int) - y0 + pad
        prof = np.bincount(yy.ravel(), weights=L.ravel(), minlength=(y1 - y0) + 2 * pad + 1)
        score = float((prof ** 2).sum())
        if score > best[0]:
            best = (score, t, prof)
    _, slope, prof = best
    y_axis = (np.arange(prof.size) - pad + y0) * f + f / 2
    peakiness = float(prof.max() / max(np.median(prof[prof > 0]) if (prof > 0).any() else 1.0, 1e-9))
    return slope, prof, y_axis, peakiness


def _shift_profile(p: np.ndarray, dy: float) -> np.ndarray:
    """Profile re-sampled so that q[y] = p[y + dy] (linear interpolation, zero outside)."""
    y = np.arange(p.size, dtype=float)
    return np.interp(y + dy, y, p, left=0.0, right=0.0)


def _refine_residuals(profs_dt, tmpl, gap, max_res, center=None):
    """Per-lane vertical offsets by normalised cross-correlation with the standards' de-tilted profile, searched
    within `center` +/- `max_res` px (keep max_res below half a band gap so a band can never be taken for its
    neighbour). Returns (offsets, strong): `strong` marks lanes with enough pigment to measure."""
    ref = profs_dt[:STANDARD_LANES].sum(axis=0)
    lo, hi = int(max(0, tmpl[0] - gap)), int(min(ref.size, tmpl[-1] + gap))
    r_ref = ref[lo:hi] - ref[lo:hi].mean()
    strength = np.array([p[lo:hi].max() for p in profs_dt])
    strong = strength >= 0.08 * strength[:STANDARD_LANES].max()
    center = np.zeros(len(profs_dt)) if center is None else np.asarray(center, dtype=float)
    res = center.copy()
    for k, p in enumerate(profs_dt):
        if not strong[k]:
            continue                                      # empty lane: keep the prediction
        best = (-np.inf, center[k])
        for r in range(int(np.floor(center[k] - max_res)), int(np.ceil(center[k] + max_res)) + 1):
            seg = _shift_profile(p, r)[lo:hi]
            seg = seg - seg.mean()
            c = float((seg * r_ref).sum() / (np.linalg.norm(seg) * np.linalg.norm(r_ref) + 1e-12))
            if c > best[0]:
                best = (c, r)
        res[k] = best[1]
    return res, strong


def _robust_trend(x, y, use, gap):
    """Smooth trend of lane offsets across the plate: robust straight line (residual tilt between the bands and
    the pencil line), upgraded to a parabola only when that clearly fits better (TLC "smile"). Lanes further than
    0.12 band gaps from the fit are dropped iteratively (they keep their own offset later)."""
    idx = np.nonzero(use)[0]
    if idx.size < 3:
        return np.zeros_like(x, dtype=float)
    keep = idx.copy()
    for _ in range(len(idx)):
        coef = np.polyfit(x[keep], y[keep], 1)
        err = np.abs(np.polyval(coef, x[keep]) - y[keep])
        if err.max() <= 0.12 * gap or keep.size <= 4:
            break
        keep = np.delete(keep, int(err.argmax()))
    rms1 = np.sqrt(np.mean((np.polyval(coef, x[keep]) - y[keep]) ** 2))
    if keep.size >= 6:
        coef2 = np.polyfit(x[keep], y[keep], 2)
        rms2 = np.sqrt(np.mean((np.polyval(coef2, x[keep]) - y[keep]) ** 2))
        if rms2 < 0.7 * rms1 and rms1 > 0.04 * gap:
            coef = coef2
    return np.polyval(coef, x)


def _line_y(line_dt: float, slope: float, x, x_ref: float):
    return line_dt + slope * (np.asarray(x, dtype=float) - x_ref)


def detect_layout(rgb: np.ndarray, half_width_frac: float = 0.17, m: np.ndarray | None = None) -> Layout:
    """Lanes, plate tilt, bands and rectangles.

    Bands are placed with an explicit tilt model (from the pencil lines) plus a bounded per-lane correction, so a
    lane whose top band is empty (most samples) cannot slip by one band; rectangles are clipped so they never
    reach the origin pencil line or the solvent front.
    """
    notes: list[str] = []
    m = magenta_map(rgb) if m is None else m          # `m` may be passed in (already computed by detect_rotation)
    centers, spacing = detect_lane_centers(m)
    profs = _row_profiles(m, centers, spacing)
    tmpl0 = _band_template(profs)                       # rough band rows (standards, not yet de-tilted)
    gap = float(np.diff(tmpl0).mean())
    x_ref = float(centers[0])

    gray = imaging_gray(rgb)
    slope, lprof, ly, peaky = estimate_tilt(gray, centers[0] - spacing / 2, centers[-1] + spacing / 2,
                                            tmpl0[0] - 4 * gap, tmpl0[-1] + 4 * gap, x_ref)
    trust_lines = peaky >= 4
    if not trust_lines:                                 # no clear straight lines: do not trust the tilt
        slope = 0.0
        notes.append("no straight pencil line found; plate tilt not estimated")

    # de-tilt every lane profile into lane-0 coordinates, rebuild the template from the standards
    profs_dt = np.array([_shift_profile(p, slope * (c - x_ref)) for p, c in zip(profs, centers)])
    tmpl = _band_template(profs_dt)
    gap = float(np.diff(tmpl).min())
    # 1) every lane with pigment, measured within +/-0.45 gap (still cannot slip by a whole band)
    res1, strong = _refine_residuals(profs_dt, tmpl, gap, 0.45 * gap)
    # 2) the bands' own residual tilt / smile across the plate, from the measurable lanes
    trend = _robust_trend(centers - x_ref, res1, strong, gap)
    # 3) final per-lane correction, tight around that trend; empty lanes simply follow the trend
    tight = 0.2 * gap
    res, _ = _refine_residuals(profs_dt, tmpl, gap, tight, center=trend)
    at_limit = strong & (np.abs(res - trend) >= tight - 1)
    at_limit[:STANDARD_LANES] = False   # standards run slightly apart with load (S500 lowest); they define the template
    if at_limit.any():
        bad = [str(k + 1) for k in np.nonzero(at_limit)[0]]
        notes.append(f"lane(s) {', '.join(bad)}: bands far from the other lanes' positions; check the boxes")
    shifts = slope * (centers - x_ref) + res
    eff = slope + (np.polyfit(centers - x_ref, trend, 1)[0] if len(centers) > 1 else 0.0)
    tilt_deg = float(np.rad2deg(np.arctan(eff)))     # what the bands actually follow

    # band limits midway between neighbouring bands, so a rectangle never contains two bands
    edges = np.concatenate([[tmpl[0] - (tmpl[1] - tmpl[0]) / 2], (tmpl[:-1] + tmpl[1:]) / 2,
                            [tmpl[-1] + (tmpl[-1] - tmpl[-2]) / 2]])

    # origin line = strongest straight line just below the bands; solvent front = strongest just above
    peaks, _ = find_peaks(lprof, prominence=0.2 * lprof.max()) if lprof.max() > 0 else (np.array([], int), {})
    py = ly[peaks] if peaks.size else np.array([])
    margin = 0.08 * gap
    below = py[(py > tmpl[-1]) & (py < tmpl[-1] + 2 * gap)] if trust_lines else np.array([])
    above = py[(py < tmpl[0]) & (py > tmpl[0] - 3 * gap)] if trust_lines else np.array([])
    origin = float(below.min()) if below.size else None
    front = float(above.max()) if above.size else None

    hw = half_width_frac * spacing
    rects = np.zeros((len(centers), len(tmpl), 4))
    clipped = []
    for k, (c, s) in enumerate(zip(centers, shifts)):
        xa, xb = c - hw, c + hw
        for b in range(len(tmpl)):
            y0, y1 = edges[b] + s, edges[b + 1] + s
            if b == len(tmpl) - 1 and origin is not None:
                lim = float(_line_y(origin, slope, [xa, xb], x_ref).min()) - margin
                if y1 > lim:
                    y1 = max(lim, y0 + 0.3 * gap); clipped.append(k + 1)
            if b == 0 and front is not None:
                lim = float(_line_y(front, slope, [xa, xb], x_ref).max()) + margin
                y0 = min(max(y0, lim), y1 - 0.3 * gap)
            rects[k, b] = (xa, xb, y0, y1)
    if clipped:
        notes.append(f"band-3 boxes shortened above the origin line in lane(s) {', '.join(map(str, clipped))}")
    if abs(tilt_deg) >= 1:
        notes.insert(0, f"plate tilted {tilt_deg:+.1f} degrees (corrected)")
    return Layout(rects=rects, lane_centers=centers, lane_spacing=spacing, notes=notes, tilt_deg=tilt_deg)


def imaging_gray(rgb: np.ndarray) -> np.ndarray:
    f = rgb.astype(np.float32)
    return 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]


def detect_rotation(rgb: np.ndarray, return_map: bool = False):
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
    hw = int(0.2 * d)
    lane_cols = np.zeros(r.shape[1], dtype=bool)
    for c in centers:
        lane_cols[max(0, int(c) - hw):int(c) + hw] = True
    # where the pigment is: rows of the summed lane-strip profile that carry real signal
    pos = np.clip(m - np.median(m), 0, None)[:, lane_cols]
    rows = ndi.gaussian_filter1d(pos.sum(axis=1), 4)
    strong = np.nonzero(rows > 0.3 * rows.max())[0]
    mag_y = float(np.median(strong))
    # solvent-front spots: brownish (warm) and dark pixels inside the lane strips
    brown = ((f[..., 0] - f[..., 2]) > 25) & (f[..., 0] > f[..., 1] + 8) & (f[..., 1] > f[..., 2] + 5) & (gray < 130)
    ys, _ = np.nonzero(brown[:, lane_cols])
    if ys.size > 200:
        flip = float(np.median(ys)) > mag_y
        notes.append("solvent-front spots %s the bands" % ("below" if flip else "above"))
    else:
        flip = False
        notes.append("no solvent-front spots found; orientation flip not verified")
    turns = (k + (2 if flip else 0)) % 4
    if return_map:                                     # magenta map of the upright photo, reused by detect_layout
        return turns, notes, np.ascontiguousarray(np.rot90(m, 2) if flip else m)
    return turns, notes
