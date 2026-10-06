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
    min_peak_prom: float = 1.0   # qTLC default
    divisor: float = 2.0         # qTLC default
    rotate: int | None = None    # degrees CCW, multiple of 90; None = detect automatically
    bg_kernel: int | None = None  # background kernel size in px; None -> round(diag/2) of the largest-band rectangle
    uint8_wrap: bool = True      # reproduce the web app's uint8 overflow quirk (see integrate.py)


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


def analyse_plate(path, params: Params | None = None, layout: Layout | None = None) -> PlateResult:
    params = params or Params()
    rgb = imaging.load_rgb(path)
    if params.rotate is None:
        turns, _ = detect_rotation(rgb)
        rgb = np.ascontiguousarray(np.rot90(rgb, turns))
    elif params.rotate % 360:
        rgb = np.ascontiguousarray(np.rot90(rgb, (params.rotate // 90) % 4))
    h, w, _ = rgb.shape
    layout = layout or detect_layout(rgb)
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
    masks = {}
    for k in range(n_lanes):
        for b in range(n_bands):
            xa, xb, ya, yb = layout.rects[k, b]
            sub = th[int(ya - y0):int(yb - y0), int(xa - x0):int(xb - x0)]
            values[b, k], masks[(k, b)], overflow[b, k] = integrate_band(
                sub, params.min_peak_prom, params.divisor, params.uint8_wrap)
    return PlateResult(Path(path).name, values, layout, params, (x0, y0, x1, y1), kernel, th, masks, overflow)
