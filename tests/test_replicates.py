"""Grouping and averaging of repeat photos of the same plate."""
from types import SimpleNamespace

import numpy as np
import pytest

from qtlc_batcher.replicates import combine_plates, group_photos

CONC = np.array([500.0, 125.0, 31.25])
PW = lambda c: 900.0 * np.asarray(c, float) ** 0.73


def photo(image, plate=None, samples=(300, 150, 80, 40, 0, 200), scale=1.0, noise=0.0, seed=0, flags=()):
    rng = np.random.default_rng(seed)
    row = np.concatenate([PW(CONC), PW(np.maximum(samples, 1e-9)) * (np.asarray(samples) > 0)]) * scale
    vals = np.vstack([row] * 3) * np.exp(rng.normal(0, noise, (3, 9)))
    res = SimpleNamespace(values=vals, lod=np.full(3, 200.0 * scale))
    return dict(image=image, plate=plate, res=res, flags=list(flags), df=None)


def test_grouping_by_name_and_by_consecutive_match():
    items = [photo("IMG_1.JPG"), photo("IMG_2.JPG", scale=1.3, noise=0.02, seed=1),          # same plate, unnamed
             photo("IMG_3.JPG", samples=(5, 400, 10, 300, 20, 90)),                          # different plate
             photo("IMG_9.JPG", plate="AP7", samples=(60, 60, 60, 300, 300, 300)),
             photo("IMG_30.JPG", plate="AP7", samples=(60, 60, 60, 300, 300, 300), seed=2)]  # same name, far apart
    g = {tuple(x["images"]): x for x in group_photos(items)}
    assert ("IMG_1.JPG", "IMG_2.JPG") in g and g[("IMG_1.JPG", "IMG_2.JPG")]["plate"] is None
    assert ("IMG_3.JPG",) in g
    assert ("IMG_9.JPG", "IMG_30.JPG") in g and g[("IMG_9.JPG", "IMG_30.JPG")]["plate"] == "AP7"


def test_differently_named_photos_are_never_merged():
    items = [photo("IMG_1.JPG", plate="AP1"), photo("IMG_2.JPG", plate="AP2")]           # identical bands
    assert sorted(len(g["images"]) for g in group_photos(items)) == [1, 1]


def test_unnamed_photo_inherits_the_name_of_its_twin():
    items = [photo("IMG_1.JPG", plate="AP5"), photo("IMG_2.JPG", scale=0.8)]
    (g,) = group_photos(items)
    assert g["plate"] == "AP5" and g["images"] == ["IMG_1.JPG", "IMG_2.JPG"]


def test_average_recovers_concentrations_despite_exposure_differences():
    items = [photo("IMG_1.JPG", "AP1", scale=1.0), photo("IMG_2.JPG", "AP1", scale=1.6)]
    plates, groups = combine_plates(items, model="loglog")
    b2 = plates[plates.band == 2].set_index("sample_no")
    np.testing.assert_allclose(b2.calibrated[[1, 2, 3, 4, 6]], [300, 150, 80, 40, 200], rtol=1e-6)
    assert b2.calibrated[5] == 0.0 and b2.detected_in[5] == "0/2"                  # empty slot: not detected
    assert (b2.n_photos == 2).all()


def test_clean_twin_is_preferred_over_a_flagged_photo():
    bad = photo("IMG_2.JPG", "AP1", samples=(900, 900, 900, 900, 900, 900), flags=["CHECK: streak ..."])
    items = [photo("IMG_1.JPG", "AP1"), bad]
    plates, groups = combine_plates(items, model="loglog")
    assert groups.iloc[0].used == "IMG_1.JPG" and groups.iloc[0].left_out_flagged == "IMG_2.JPG"
    assert plates[plates.band == 2].set_index("sample_no").calibrated[1] == pytest.approx(300, rel=1e-6)
