"""End-to-end plate analysis: photo -> gray -> invert -> crop -> background -> per-band integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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
    return PlateResult(Path(path).name, values, layout, params, (x0, y0, x1, y1), kernel, th, masks, overflow, turns,
                       top_touch, blanks, lod)


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
