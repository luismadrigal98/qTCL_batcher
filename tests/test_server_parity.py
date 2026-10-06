"""Regression tests: our engine must reproduce www.qtlc.app exactly (numbers measured on the live server)."""
import json
from pathlib import Path

import numpy as np
import pytest

from qtlc_batcher import imaging
from qtlc_batcher.integrate import integrate_band, integrate_column

DATA = Path(__file__).parent / "data"
PHOTO = Path(__file__).parents[2] / "Phenotyping" / "TLC_plate_photos" / "IMG_3114.JPG"


def test_columns_match_server_for_all_reference_cases():
    cases = json.loads((DATA / "server_reference_columns.json").read_text())
    assert len(cases) > 200
    bad = []
    for c in cases:
        v = integrate_column(np.array(c["col"], dtype=np.uint8), c["mpp"], c["div"], uint8_wrap=True)[0]
        if abs(v - c["server"]) > 1e-6:
            bad.append((c["mpp"], c["div"], v, c["server"]))
    assert not bad, f"{len(bad)} of {len(cases)} columns differ from the server, e.g. {bad[:3]}"


@pytest.mark.skipif(not PHOTO.exists(), reason="plate photo not available")
def test_real_plate_matches_server_exactly():
    """IMG_3114 with fixed rectangles: all 27 band values identical to the web app's table."""
    payload = json.loads((DATA / "plate3114_payload.json").read_text())
    server = np.array(json.loads((DATA / "plate3114_server_result.json").read_text())["result"]).T   # [band, lane]
    c, bg = payload["cropRect"], payload["backgroundRect"]
    rgb = imaging.load_rgb(PHOTO)
    inv = imaging.invert(imaging.to_gray_u8(rgb[c["y"]:c["y"] + c["h"], c["x"]:c["x"] + c["w"]]))
    th = imaging.tophat(inv, imaging.kernel_size_from_rect(bg["w"], bg["h"]))
    mine = np.zeros_like(server)
    for k, lane in enumerate(payload["laneAndBandList"]):
        lr = lane["laneRect"]
        for b, br in enumerate(lane["bandRectList"]):
            x, y = lr["x"] + br["x"], lr["y"] + br["y"]
            mine[b, k] = integrate_band(th[y:y + br["h"], x:x + br["w"]], payload["minPeakProm"], payload["divisor"])[0]
    assert np.array_equal(mine, server)
