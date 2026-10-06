"""Per-band signal integration, reproducing the qTLC web app (www.qtlc.app) exactly.

Rule (established by black-box probing of the server with synthetic images, 64+ cases, 0 mismatches):

  The band rectangle is split into 1-px-wide vertical slices (columns). For each column `v`:
    1. find peaks with prominence >= MinPeakProm (scipy/MATLAB `findpeaks` semantics); none -> 0;
    2. take the tallest peak, m = v[peak]; threshold = m / Divisor;
    3. walk outward from the peak while v > threshold (strict) and keep the first sample <= threshold
       on each side (or the array end) as the integration limits;
    4. integrate over [lo, hi] with the trapezoid rule, sum_i (v[i] + v[i+1]) / 2.
  The band value is the sum over columns.

Server quirk (reproduced when `uint8_wrap=True`): the pair sums v[i] + v[i+1] are formed in 8-bit
unsigned arithmetic, so any two adjacent pixels summing to >= 256 silently lose 256. This corrupts
bands whose background-corrected pixels exceed ~127. It is kept as an option so results can be
compared with historical web-app numbers; use `uint8_wrap=False` for the mathematically correct integral.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks


def integrate_column(v: np.ndarray, min_peak_prom: float = 1.0, divisor: float = 2.0,
                     uint8_wrap: bool = True):
    """Integrate one 1-D profile. Returns (value, lo, hi, overflowed); lo/hi are None when no peak."""
    peaks, _ = find_peaks(v.astype(np.float64), prominence=min_peak_prom)
    if peaks.size == 0:
        return 0.0, None, None, False
    i = int(peaks[np.argmax(v[peaks])])
    thr = v[i] / divisor
    lo = i
    while lo > 0 and v[lo] > thr:
        lo -= 1
    hi = i
    n = len(v)
    while hi < n - 1 and v[hi] > thr:
        hi += 1
    seg = v[lo:hi + 1]
    if seg.size < 2:
        return 0.0, lo, hi, False
    exact = seg[:-1].astype(np.int64) + seg[1:].astype(np.int64)
    overflow = bool((exact >= 256).any())
    pair = (seg[:-1].astype(np.uint8) + seg[1:].astype(np.uint8)).astype(np.float64) if uint8_wrap \
        else exact.astype(np.float64)
    return float(pair.sum() / 2), lo, hi, overflow


def integrate_band(sub: np.ndarray, min_peak_prom: float = 1.0, divisor: float = 2.0,
                   uint8_wrap: bool = True):
    """Integrate one band region (2-D: rows = migration axis, cols = across the lane).

    Returns (value, mask, n_overflow_cols). `mask` marks the integrated pixels (the "red overlay");
    `n_overflow_cols` counts columns where the server's uint8 wrap-around would have changed the sum
    (only those columns differ between uint8_wrap True/False).
    """
    sub = np.clip(np.round(sub), 0, 255).astype(np.uint8)
    h, w = sub.shape
    mask = np.zeros((h, w), dtype=bool)
    total, n_over = 0.0, 0
    for c in range(w):
        val, lo, hi, over = integrate_column(sub[:, c], min_peak_prom, divisor, uint8_wrap)
        if lo is None:
            continue
        total += val
        mask[lo:hi + 1, c] = True
        n_over += over
    return total, mask, n_over
