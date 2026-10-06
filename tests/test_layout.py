"""Layout regression tests on a synthetic plate that reproduces the failure seen on IMG_3162 (AP51):
a tilted plate whose sample lanes have no band 1, so a 3-band template can slip down by one band and
the bottom box then integrates the pencil origin line."""
import numpy as np
import pytest

from qtlc_batcher.layout import detect_layout
from qtlc_batcher.pipeline import rect_slice
from qtlc_batcher.results import calibrate

PITCH, X0, BAND_Y, GAP = 200, 150, 420, 60          # lane-0 band-1 row, band spacing (px)


def synthetic_plate(slope: float, seed: int = 0):
    rng = np.random.default_rng(seed)
    h, w = 900, 2000
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.empty((h, w, 3), np.float32)
    img[...] = (205, 205, 210)
    pink = np.array([-25.0, -95.0, -20.0])           # how a pigment band darkens R, G, B
    truth = []                                       # true band-centre rows per lane
    for k in range(9):
        cx = X0 + k * PITCH
        dy = slope * (cx - X0)
        rows = [BAND_Y + i * GAP + dy for i in range(3)]
        truth.append(rows)
        amps = [1.0, 0.8, 0.9] if k == 0 else [0.5, 0.45, 0.5] if k == 1 else [0.25, 0.22, 0.25] if k == 2 \
            else [0.0, 0.7, 0.6]                     # samples: no band 1 (the slip trap)
        for a, cy in zip(amps, rows):
            if a:
                blob = a * np.exp(-((xx - cx) ** 2) / (2 * 22 ** 2) - ((yy - cy) ** 2) / (2 * 9 ** 2))
                img += blob[..., None] * pink
    origin_dt = BAND_Y + 2 * GAP + 55                # origin pencil line, 55 px below band 3 (tilted)
    line = np.abs(yy - (origin_dt + slope * (xx - X0))) < 2.0
    img[line] = (95, 95, 100)
    front = np.abs(yy - (BAND_Y - 170 + slope * (xx - X0))) < 1.5
    img[front] = (190, 180, 150)
    img += rng.normal(0, 1.5, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8), np.array(truth), origin_dt


@pytest.mark.parametrize("slope", [0.0, -0.06, 0.05])          # level, ~-3.4 deg (like IMG_3162), ~+2.9 deg
def test_no_band_slip_on_tilted_plate(slope):
    rgb, truth, origin_dt = synthetic_plate(slope)
    lay = detect_layout(rgb)
    assert abs(lay.tilt_deg - np.rad2deg(np.arctan(slope))) < 0.5
    for k in range(9):
        for b in range(3):
            x0, x1, y0, y1 = lay.rects[k, b]
            assert y0 < truth[k, b] < y1, f"lane {k + 1} band {b + 1} centre outside its box (slipped)"
        # the band-3 box must stop above the origin line at both of its corners
        x0, x1, _, y1 = lay.rects[k, 2]
        line_top = min(origin_dt + slope * (x - X0) for x in (x0, x1))
        assert y1 < line_top, f"lane {k + 1}: band-3 box reaches the origin line"


def test_rect_slice_clamps_rectangles_outside_the_photo():
    # a box dragged above / left of the crop must not wrap around to the other edge
    assert rect_slice((-50, 30, -80, 20), (0, 0), (100, 200)) == (0, 20, 0, 30)
    assert rect_slice((150, 400, 90, 300), (100, 50), (100, 200)) == (40, 100, 50, 200)
    r0, r1, c0, c1 = rect_slice((500, 600, 500, 600), (0, 0), (100, 200))
    assert r1 - r0 == 0 and c1 - c0 == 0


def test_calibration_models_recover_their_own_curves():
    conc = np.array([500.0, 125.0, 31.25])
    samples = np.array([400.0, 200.0, 80.0, 40.0, 10.0, 300.0])
    lin = lambda c: 120.0 * c + 3000.0
    pw = lambda c: 900.0 * c ** 0.73
    v_lin = np.vstack([np.concatenate([lin(conc), lin(samples)])] * 3)
    v_pw = np.vstack([np.concatenate([pw(conc), pw(samples)])] * 3)
    np.testing.assert_allclose(calibrate(v_lin, model="linear").conc[0], samples, rtol=1e-9)
    np.testing.assert_allclose(calibrate(v_pw, model="loglog").conc[0], samples, rtol=1e-9)
    # samples outside the standards' range are marked
    out = calibrate(v_pw, model="loglog").outside[0]
    assert list(out) == [0, 0, 0, 0, -1, 0]
