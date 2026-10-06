from pathlib import Path

import numpy as np
import pytest

from qtlc_batcher.benchmark import load_quant
from qtlc_batcher.imaging import kernel_size_from_rect, tophat
from qtlc_batcher.integrate import integrate_band

XLSX = Path(__file__).parents[2] / "Phenotyping" / "GenotpedPigmentExtractionSamplesList.xlsx"


def _gaussian_band(h=60, w=20, peak=100.0, noise=0.0, seed=0):
    y = np.arange(h)[:, None]
    img = peak * np.exp(-((y - h / 2) ** 2) / (2 * 6.0 ** 2)) * np.ones((1, w))
    img = np.round(img + np.random.default_rng(seed).normal(0, noise, img.shape)).clip(0, 255)
    return img


def test_integrate_empty_region_is_zero():
    assert integrate_band(np.zeros((40, 10)))[0] == 0.0


def test_integrate_scales_with_signal_and_width():
    one, *_ = integrate_band(_gaussian_band(peak=100, w=10))
    two, *_ = integrate_band(_gaussian_band(peak=100, w=20))
    assert two == pytest.approx(2 * one)
    # linear in signal only without the server's uint8 overflow (which breaks it above ~127)
    assert integrate_band(_gaussian_band(peak=200, w=10), uint8_wrap=False)[0] > 1.8 * one
    assert integrate_band(_gaussian_band(peak=200, w=10), uint8_wrap=True)[0] < one   # quirk, as on the server


def test_divisor_widens_integration_limits():
    narrow, *_ = integrate_band(_gaussian_band(), divisor=2)
    wide, *_ = integrate_band(_gaussian_band(), divisor=4)
    assert wide > narrow


def test_min_peak_prom_suppresses_noise():
    noise_only = np.round(np.random.default_rng(1).normal(2, 1, (60, 30))).clip(0, 255)
    assert integrate_band(noise_only, min_peak_prom=1)[0] > 0
    assert integrate_band(noise_only, min_peak_prom=20)[0] == 0


def test_values_are_half_integers_on_integer_input():
    # the benchmark workbook only contains multiples of 0.5; trapezoid on integer pixels reproduces that
    v, *_ = integrate_band(_gaussian_band(noise=2.0))
    assert (v * 2) == pytest.approx(round(v * 2))


def test_tophat_removes_smooth_background_keeps_small_features():
    yy, xx = np.mgrid[0:200, 0:300]
    img = (60 + 0.1 * xx).astype(np.uint8)   # illumination gradient
    img[90:110, 140:160] += 80               # small bright blob, smaller than the kernel
    out = tophat(img, kernel_size=40)
    assert out[:40, :40].max() <= 4          # background gone
    assert out[95:105, 145:155].mean() > 60  # blob preserved


def test_kernel_size_rule_matches_web_app():
    # values measured against the server: (rect w, h) -> kernel size
    assert kernel_size_from_rect(36, 48) == 30
    assert kernel_size_from_rect(25, 40) == 24
    assert kernel_size_from_rect(50, 30) == 29
    assert kernel_size_from_rect(30, 45) == 27
    assert kernel_size_from_rect(21, 34) == 20
    assert kernel_size_from_rect(17, 17) == 12


def test_uint8_wrap_reproduces_server_quirk():
    from qtlc_batcher.integrate import integrate_column
    plateau = np.zeros(100, np.uint8); plateau[45:56] = 200       # 11 samples of 200
    assert integrate_column(plateau, uint8_wrap=True)[0] == 920    # server value (measured)
    assert integrate_column(plateau, uint8_wrap=False)[0] == 2200  # mathematically correct


def test_integrate_matches_server_reference_cases():
    # measured on www.qtlc.app with 1-px-wide synthetic columns (MinPeakProm=1, Divisor=2)
    from qtlc_batcher.integrate import integrate_column
    y = np.arange(100, dtype=float)
    gauss = lambda a, s: np.round(a * np.exp(-((y - 50) ** 2) / (2 * s * s))).astype(np.uint8)
    assert integrate_column(gauss(100, 6))[0] == 1231
    assert integrate_column(gauss(90, 6))[0] == 1107     # boundary sample just below threshold is included
    assert integrate_column(gauss(255, 6))[0] == 1338    # uint8 wrap region
    assert integrate_column(np.linspace(0, 100, 100).astype(np.uint8))[0] == 0   # monotonic ramp: no peak


@pytest.mark.skipif(not XLSX.exists(), reason="benchmark workbook not available")
def test_benchmark_loader_reads_all_plates():
    plates = load_quant(XLSX)
    assert len(plates) == 28
    assert plates[0].plate == "AP31" and plates[0].image_no == "3114"
    assert not np.isnan(np.stack([p.values for p in plates])).any()
    assert plates[0].values[0, 0] == 86429.5
