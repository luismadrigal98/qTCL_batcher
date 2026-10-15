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


def test_end_to_end_synthetic_plate_flags_and_workbook(tmp_path):
    import pandas as pd
    from PIL import Image

    from qtlc_batcher.pipeline import Params, analyse_plate
    from qtlc_batcher.results import export_workbook, plate_tables, quality_flags

    rgb, truth, _ = synthetic_plate(-0.06)
    path = tmp_path / "plate.png"
    Image.fromarray(rgb).save(path)
    res = analyse_plate(path, Params(rotate=0))
    df, cal = plate_tables(res, "AP0", None, model="loglog")
    flags = quality_flags(res, cal, res.turns)
    assert all(f.startswith(("CHECK: ", "note: ")) for f in flags)
    assert any("plate tilted" in f for f in flags)                 # tilt reported as a note
    assert not any("streak" in f for f in flags)                    # clean synthetic lanes: no streak flag
    assert (res.values[0, 3:] < res.lod[0]).all()                   # samples have no band 1: below detection
    out = tmp_path / "sub" / "r.xlsx"
    export_workbook(out, [dict(image="plate.png", plate="AP0", res=res, df=df, cal=cal, flags=flags)], res.params)
    sheets = pd.read_excel(out, sheet_name=None)
    assert {"severity", "flag"} <= set(sheets["Flags"].columns)
    assert set(sheets["Tidy"]["calibration"]) == {"loglog"}


def test_detection_limit_drops_faint_standards_and_zeroes_undetected_samples():
    conc = np.array([500.0, 125.0, 31.25])
    pw = lambda c: 900.0 * c ** 0.73
    stds = pw(conc); stds[2] = 50.0                      # lowest standard lost (below detection)
    samples = np.array([pw(300.0), pw(150.0), 40.0, 0.0, pw(200.0), 10.0])
    v = np.vstack([np.concatenate([stds, samples])] * 3)
    cal = calibrate(v, model="loglog", lod=np.array([200.0] * 3))
    assert cal.n_standards_used[0] == 2                  # S31.25 left out of the fit
    np.testing.assert_allclose(cal.conc[0, [0, 1, 4]], [300.0, 150.0, 200.0], rtol=1e-6)
    assert list(cal.conc[0, [2, 3, 5]]) == [0.0, 0.0, 0.0]   # below the limit: not detected


def test_synthetic_plate_empty_band1_slots_are_not_detected(tmp_path):
    from PIL import Image

    from qtlc_batcher.pipeline import Params, analyse_plate
    from qtlc_batcher.results import plate_tables

    rgb, _, _ = synthetic_plate(0.0)
    Image.fromarray(rgb).save(tmp_path / "p.png")
    res = analyse_plate(tmp_path / "p.png", Params(rotate=0, min_peak_prom=3, uint8_wrap=False))
    df, _ = plate_tables(res, None, None, model="loglog")
    samples = df[df.sample_no.notna()]
    assert not samples[samples.band == 1].detected.any()       # samples have no band 1
    assert samples[samples.band.isin([2, 3])].detected.all()    # bands 2 and 3 are there
    assert (res.lod > 0).all() and res.blanks.shape == (3, 8)


def _paint_streaks(rgb, x_center, y_top, gap, seed=0):
    """Thin vertical pink stripes from the solvent-front region down through band 1 (like the real ones)."""
    rng = np.random.default_rng(seed)
    img = rgb.astype(np.float32)
    h, w, _ = img.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    for _ in range(5):
        cx = x_center + rng.uniform(-20, 20)
        decay = np.where(yy >= y_top, np.exp(-(yy - y_top) / (1.5 * gap)), 0.0)
        img += (rng.uniform(0.4, 0.9) * np.exp(-((xx - cx) ** 2) / (2 * 3.0 ** 2)) * decay)[..., None] * \
               np.array([-25.0, -95.0, -20.0])
    return np.clip(img, 0, 255).astype(np.uint8)


def test_streak_correction_only_where_detected_and_restores_band1(tmp_path):
    from PIL import Image

    from qtlc_batcher.pipeline import Params, analyse_plate
    from qtlc_batcher.results import plate_tables, quality_flags

    rgb, truth, _ = synthetic_plate(0.0)
    streaky = _paint_streaks(rgb, X0 + 5 * PITCH, BAND_Y - 120, GAP)          # lane 6 = sample 3
    Image.fromarray(streaky).save(tmp_path / "s.png")
    Image.fromarray(rgb).save(tmp_path / "c.png")
    clean = analyse_plate(tmp_path / "c.png", Params(rotate=0))
    off = analyse_plate(tmp_path / "s.png", Params(rotate=0, streak_correction=False), layout=clean.layout)
    on = analyse_plate(tmp_path / "s.png", Params(rotate=0), layout=clean.layout)
    corrected = [k for k in range(9) if np.isfinite(on.band1_raw[k])]
    assert corrected == [5]                                                    # only the streaked lane
    assert off.values[0, 5] > 5 * on.lod[0]                                    # the streak fakes a band 1 ...
    assert on.values[0, 5] < 0.3 * off.values[0, 5]                           # ... the correction removes most of it
    np.testing.assert_allclose(on.values[1:], off.values[1:])                  # bands 2-3 are never touched
    df, cal = plate_tables(on, None, None)
    flags = quality_flags(on, cal)
    assert any("streak removed" in f for f in flags)
    row = df[(df.lane_index == 6) & (df.band == 1)].iloc[0]
    assert row.streak_corrected and row.integral_before_streak_correction == off.values[0, 5]


def _add_band1(rgb, x_center, amp=0.6):
    """A real pelargonidin band in band-1 position of one sample lane."""
    img = rgb.astype(np.float32)
    h, w, _ = img.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    blob = amp * np.exp(-((xx - x_center) ** 2) / (2 * 22 ** 2) - ((yy - BAND_Y) ** 2) / (2 * 9 ** 2))
    return np.clip(img + blob[..., None] * np.array([-25.0, -95.0, -20.0]), 0, 255).astype(np.uint8)


def test_real_pelargonidin_under_a_streak_is_kept_and_flagged(tmp_path):
    from PIL import Image

    from qtlc_batcher.pipeline import Params, analyse_plate
    from qtlc_batcher.results import plate_tables, quality_flags

    rgb, _, _ = synthetic_plate(0.0)
    cx = X0 + 5 * PITCH
    with_band = _add_band1(rgb, cx)
    Image.fromarray(with_band).save(tmp_path / "truth.png")
    Image.fromarray(_paint_streaks(with_band, cx, BAND_Y - 120, GAP)).save(tmp_path / "streak.png")
    truth = analyse_plate(tmp_path / "truth.png", Params(rotate=0))
    res = analyse_plate(tmp_path / "streak.png", Params(rotate=0), layout=truth.layout)
    assert res.streak_call == {5: "present"}
    assert 0.5 * truth.values[0, 5] < res.values[0, 5] < 1.5 * truth.values[0, 5]
    df, cal = plate_tables(res, None, None)
    assert any(f.startswith("CHECK") and "pelargonidin is present" in f for f in quality_flags(res, cal))
