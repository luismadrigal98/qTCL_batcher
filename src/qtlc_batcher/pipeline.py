"""End-to-end plate analysis: photo -> gray -> invert -> crop -> background -> per-band integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import imaging
from .integrate import integrate_band
from .layout import Layout, detect_layout, detect_rotation


@dataclass
class Params:
    min_peak_prom: float = 3.0   # qTLC's own default is 1; 5 loses the faintest standard (README section 6)
    divisor: float = 2.0         # qTLC default
    rotate: int | None = None    # degrees CCW, multiple of 90; None = detect automatically
    bg_kernel: int | None = None  # background kernel size in px; None -> round(diag/2) of the largest-band rectangle
    uint8_wrap: bool = False     # True = reproduce the web app's uint8 overflow bug (parity with old numbers)
    streak_correction: bool = True   # remove vertical streaks from band 1 in lanes where a streak is detected


@dataclass
class PlateResult:
    image: str
    values: np.ndarray                     # [band, lane]
    layout: Layout
    params: Params
    crop: tuple[int, int, int, int]        # x0, y0, x1, y1 in full-image coordinates
    bg_kernel: int
    processed: np.ndarray = field(repr=False, default=None)   # inverted, background-corrected crop
    masks: dict = field(repr=False, default_factory=dict)     # (lane, band) -> bool mask of integrated pixels
    overflow_cols: np.ndarray = field(repr=False, default=None)  # [band, lane] columns hit by the uint8 wrap
    turns: int = 0                         # 90-degree CCW turns applied to the photo
    top_touch: np.ndarray = field(repr=False, default=None)   # [band, lane] share of integrated columns whose
                                                              # integration reaches the box's top edge (streaks)
    blanks: np.ndarray = field(repr=False, default=None)      # [band, gap] integrals of empty boxes between lanes
    lod: np.ndarray = field(repr=False, default=None)         # [band] limit of detection = blank mean + 3 SD
    band1_raw: np.ndarray = field(repr=False, default=None)   # [lane] band-1 value before streak correction (NaN = none)
    streak_call: dict = field(repr=False, default_factory=dict)  # lane -> "absent" / "present" after streak removal


def rect_slice(rect, origin, shape):
    """Integer row/column slice of `rect` = (x0, x1, y0, y1) inside an array whose (0, 0) is at image `origin`,
    clamped to the array. (Unclamped, a rectangle dragged past the photo edge gives negative indices, which numpy
    silently wraps around to the opposite edge.)"""
    xa, xb, ya, yb = rect
    ox, oy = origin
    h, w = shape
    r0, r1 = min(max(int(ya - oy), 0), h), min(max(int(yb - oy), 0), h)
    c0, c1 = min(max(int(xa - ox), 0), w), min(max(int(xb - ox), 0), w)
    return r0, r1, c0, c1


def analyse_plate(path, params: Params | None = None, layout: Layout | None = None) -> PlateResult:
    params = params or Params()
    rgb = imaging.load_rgb(path)
    m = None
    if params.rotate is None:
        turns, _, m = detect_rotation(rgb, return_map=True)
    else:
        turns = (params.rotate // 90) % 4
    if turns:
        rgb = np.ascontiguousarray(np.rot90(rgb, turns))
    h, w, _ = rgb.shape
    layout = layout or detect_layout(rgb, m=m)
    x0, y0, x1, y1 = layout.bounds()
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    inv = imaging.invert(imaging.to_gray_u8(rgb[y0:y1, x0:x1]))
    if params.bg_kernel is None:
        rx0, rx1, ry0, ry1 = layout.rects[0, -1]      # largest band: bottom band of the first standard
        kernel = imaging.kernel_size_from_rect(rx1 - rx0, ry1 - ry0)
    else:
        kernel = params.bg_kernel
    th = imaging.tophat(inv, kernel)

    n_lanes, n_bands = layout.rects.shape[:2]
    values = np.zeros((n_bands, n_lanes))
    overflow = np.zeros((n_bands, n_lanes), dtype=int)
    top_touch = np.zeros((n_bands, n_lanes))
    masks = {}
    for k in range(n_lanes):
        for b in range(n_bands):
            r0, r1, c0, c1 = rect_slice(layout.rects[k, b], (x0, y0), th.shape)
            sub = th[r0:r1, c0:c1]
            if sub.size == 0:                        # rectangle entirely outside the photo
                masks[(k, b)] = np.zeros((0, 0), bool)
                continue
            values[b, k], masks[(k, b)], overflow[b, k] = integrate_band(
                sub, params.min_peak_prom, params.divisor, params.uint8_wrap)
            cols = masks[(k, b)].any(axis=0)
            if cols.any():   # a real band is a blob inside the box; a streak from the front enters at the top
                top_touch[b, k] = float((masks[(k, b)][0] & cols).sum() / cols.sum())
    blanks = blank_integrals(th, layout.rects, (x0, y0), params)
    lod = blanks.mean(axis=1) + 3 * blanks.std(axis=1) if blanks.size else np.zeros(n_bands)
    band1_raw, streak_call = np.full(n_lanes, np.nan), {}
    if params.streak_correction:
        present_above = max(0.5 * values[0, 2], lod[0])     # half the lowest standard (or the LOD, if higher)
        above = np.array([above_box_level(th, layout.rects[k], (x0, y0)) for k in range(n_lanes)])
        for k in streak_lanes(values, top_touch, above):
            strict, gentle = baseline_band1(th, layout.rects[k], (x0, y0)), destreak_band1(th, layout.rects[k], (x0, y0))
            if strict is None or gentle is None:
                continue
            band1_raw[k] = values[0, k]
            v, m, o = integrate_band(strict, params.min_peak_prom, params.divisor, params.uint8_wrap)
            if v < present_above:                            # strict filter: the streak was all there was
                streak_call[k] = "absent"
            else:                                            # pigment under the streak: measure with the gentle filter
                streak_call[k] = "present"
                v, m, o = integrate_band(gentle, params.min_peak_prom, params.divisor, params.uint8_wrap)
            values[0, k], masks[(k, 0)], overflow[0, k] = v, m, o
    return PlateResult(Path(path).name, values, layout, params, (x0, y0, x1, y1), kernel, th, masks, overflow, turns,
                       top_touch, blanks, lod, band1_raw, streak_call)


STREAK_TOP_TOUCH, STREAK_MIN_VALUE, STREAK_LINE, STREAK_ABOVE_LEVEL = 0.15, 1000.0, 1.6, 4.0


def above_box_level(th, lane_rects, origin, frac: float = 0.25) -> float:
    """Typical processed signal in the strip just above the band-1 box (median over columns of the column medians).
    Clean plates: median 2, never above 4; lanes with a streak: median 5."""
    gap = float(np.median(np.diff(lane_rects[:, 2])))
    b0, _, c0, c1 = rect_slice(lane_rects[0], origin, th.shape)
    a0 = max(0, b0 - int(round(frac * gap)))
    if a0 >= b0 or c1 <= c0:
        return 0.0
    return float(np.median(np.median(th[a0:b0, c0:c1].astype(float), axis=0)))


def streak_lanes(values, top_touch, above=None, n_standards: int = 3):
    """Sample lanes with a streak running into the band-1 box from the solvent front: either the band-1
    integration reaches the box's top edge (streak over an empty slot), or the strip just above the box carries more
    signal than any clean plate shows (streak over a real band, which otherwise inflates it silently)."""
    out = []
    for k in range(n_standards, values.shape[1]):
        if values[0, k] <= STREAK_MIN_VALUE:
            continue
        if top_touch[0, k] >= STREAK_TOP_TOUCH or (above is not None and above[k] > STREAK_ABOVE_LEVEL):
            out.append(k)
    return out


def destreak_band1(th, lane_rects, origin):
    """Band-1 box of one lane with vertical streaks removed. Streaks are thin vertical stripes running down from the
    solvent front; bands are wide and short. A 1-px-wide vertical line of 1.6 band spacings fits inside a stripe but
    not inside a band, so the morphological opening with it is the streak, and subtracting it leaves the band.
    This *gentle* filter keeps most of a real band (about -13 % without a stripe, -31 % under one) but leaves part of a
    stripe that starts close above the box, so it is used to MEASURE pelargonidin once `baseline_band1` has shown
    that some is there. Returns the cleaned band-1 box, or None."""
    gap = float(np.median(np.diff(lane_rects[:, 2])))
    xa, xb = lane_rects[0, 0], lane_rects[0, 1]
    r0, r1, c0, c1 = rect_slice((xa, xb, 0, lane_rects[-1, 3]), origin, th.shape)   # front .. band-3 bottom
    if r1 <= r0 or c1 <= c0:
        return None, None
    strip = np.ascontiguousarray(th[r0:r1, c0:c1]).astype(np.uint8)
    line = np.ones((max(3, int(round(STREAK_LINE * gap))), 1), np.uint8)
    clean = strip - cv2.morphologyEx(strip, cv2.MORPH_OPEN, line)          # opening <= strip: no underflow
    b0, b1, _, _ = rect_slice(lane_rects[0], origin, th.shape)
    return clean[b0 - r0:b1 - r0]


def baseline_band1(th, lane_rects, origin, above: float = 0.25, below: float = 0.12):
    """*Strict* filter: per-column straight baseline from just above the band-1 box to the valley between band 1 and
    band 2 (classic densitometric baseline), subtracted. Removes ~99 % of a streak from an empty band-1 slot (stripes
    painted into synthetic photos) but also ~40-55 % of a real band, so it is used only to DECIDE whether pelargonidin
    is present under a streak. Returns the cleaned band-1 box, or None."""
    gap = float(np.median(np.diff(lane_rects[:, 2])))
    b0, b1, c0, c1 = rect_slice(lane_rects[0], origin, th.shape)
    a0 = max(0, b0 - int(round(above * gap)))
    v0, v1 = max(b0, b1 - int(round(below * gap))), min(th.shape[0], b1 + int(round(below * gap)))
    if b1 <= b0 or c1 <= c0 or a0 >= b0 or v1 <= v0:
        return None
    box = th[b0:b1, c0:c1].astype(float)
    top = np.median(th[a0:b0, c0:c1].astype(float), axis=0)
    bot = np.median(th[v0:v1, c0:c1].astype(float), axis=0)
    yt, yb = (a0 + b0) / 2 - b0, (v0 + v1) / 2 - b0
    y = np.arange(b1 - b0)[:, None]
    base = top[None, :] + (bot - top)[None, :] * (y - yt) / max(yb - yt, 1.0)
    return np.clip(np.round(box - np.clip(base, 0, None)), 0, 255)


def blank_integrals(th, rects, origin, params) -> np.ndarray:
    """Integrals of empty boxes (same size and rows as the neighbouring band boxes) placed midway between
    adjacent lanes, i.e. the plate's response to no pigment, processed exactly like the real boxes."""
    n_lanes, n_bands = rects.shape[:2]
    out = np.zeros((n_bands, max(n_lanes - 1, 0)))
    for j in range(n_lanes - 1):
        for b in range(n_bands):
            a, c = rects[j, b], rects[j + 1, b]
            cx, hw = (a[0] + a[1] + c[0] + c[1]) / 4, (a[1] - a[0]) / 2
            r0, r1, c0, c1 = rect_slice((cx - hw, cx + hw, (a[2] + c[2]) / 2, (a[3] + c[3]) / 2), origin, th.shape)
            if r1 > r0 and c1 > c0:
                out[b, j] = integrate_band(th[r0:r1, c0:c1], params.min_peak_prom, params.divisor,
                                           params.uint8_wrap)[0]
    return out
