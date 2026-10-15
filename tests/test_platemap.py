"""Plate map and photo -> plate list from CSV / TSV / Excel, located by header names."""
from pathlib import Path

import pandas as pd
import pytest

from qtlc_batcher.platemap import load_image_plate_map, load_platemap

XLSX = Path(__file__).parents[2] / "Phenotyping" / "GenotpedPigmentExtractionSamplesList.xlsx"


def test_csv_with_short_headers(tmp_path):
    f = tmp_path / "map.csv"
    f.write_text("plate,sample_no,plant,parent,meoh_ul,flower_mg\nAP1,1,p1,KA-Y,50,20.5\nAP1,2,p2,K3A1,75,18.2\n")
    df = load_platemap(f)
    assert df.shape == (2, 6) and list(df.sample_no) == [1, 2] and df.flower_mg.tolist() == [20.5, 18.2]


def test_semicolon_csv_with_workbook_headers_and_decimal_commas(tmp_path):
    f = tmp_path / "map.csv"
    f.write_text("Parent;Plant;Plate;Sample # on Plate;MeOH added (uL);Flower Mass (mg)\n"
                 "KA-Y;486-2;AP30;1;100;18,1\nKA-Y;485-2;AP30;2;25;24\n")
    df = load_platemap(f)
    assert df.plate.tolist() == ["AP30", "AP30"] and df.meoh_ul.tolist() == [100, 25] and df.flower_mg.tolist() == [18.1, 24]


def test_minimal_excel_table_with_title_rows_and_extra_columns(tmp_path):
    f = tmp_path / "map.xlsx"
    rows = [["My plates", None, None, None], [None, None, None, None],
            ["Lane", "Plate", "Plant ID", "Notes"], [3, "AP5", "x", "ok"], [4, "AP5", "y", ""]]
    pd.DataFrame(rows).to_excel(f, header=False, index=False)
    df = load_platemap(f)
    assert df.plate.tolist() == ["AP5", "AP5"] and df.sample_no.tolist() == [3, 4] and df.plant.tolist() == ["x", "y"]
    assert df.flower_mg.isna().all()                          # optional columns may be missing


def test_missing_sample_column_gives_a_clear_error(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("plate,plant\nAP1,p1\n")
    with pytest.raises(ValueError, match="sample"):
        load_platemap(f)


def test_image_plate_list_tolerates_other_headers_and_separators(tmp_path):
    f = tmp_path / "photos.csv"
    f.write_text("Photo;Plate\nIMG_1.JPG;AP1\nIMG_2.JPG;AP2\n")
    assert load_image_plate_map(f) == {"IMG_1.JPG": "AP1", "IMG_2.JPG": "AP2"}


@pytest.mark.skipif(not XLSX.exists(), reason="collaborators' workbook not available")
def test_collaborators_workbook_and_its_csv_export_give_the_same_map(tmp_path):
    a = load_platemap(XLSX)
    assert a.shape == (288, 6) and a.plate.nunique() == 48
    assert set(a.parent) >= {"KA-Y", "K3A1", "F1/parent"}
    raw = pd.read_excel(XLSX, sheet_name="TLC", header=None)
    raw.to_csv(tmp_path / "tlc.csv", header=False, index=False, sep=";", decimal=",")
    assert load_platemap(tmp_path / "tlc.csv").equals(a)
