"""Plate map: which plant is on which lane of which plate (sheet "TLC" of the collaborators' workbook)."""
from __future__ import annotations

import pandas as pd

COLUMNS = ["parent", "plant", "plate", "sample_no", "meoh_ul", "flower_mg"]


def load_platemap(xlsx_path, sheet: str = "TLC") -> pd.DataFrame:
    """Read both blocks of the sheet: F2 population (A:F) and F1-and-parents (I:M, no 'Parent' column)."""
    raw = pd.read_excel(xlsx_path, sheet_name=sheet, header=None, skiprows=2)
    f2 = raw.iloc[:, 0:6].copy()
    f2.columns = COLUMNS
    f1 = raw.iloc[:, 8:13].copy()
    f1.columns = ["plant", "plate", "sample_no", "meoh_ul", "flower_mg"]
    f1["parent"] = "F1/parent"
    df = pd.concat([f2, f1[COLUMNS]], ignore_index=True)
    df["sample_no"] = pd.to_numeric(df["sample_no"], errors="coerce")   # a typo in one cell must not crash
    df = df.dropna(subset=["plate", "sample_no"])
    df["plate"] = df["plate"].astype(str).str.strip()
    df["sample_no"] = df["sample_no"].astype(int)
    df["plant"] = df["plant"].astype(str)
    for col in ("meoh_ul", "flower_mg"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_image_plate_map(path) -> dict[str, str]:
    """CSV/Excel with columns `image,plate` (image file name or IMG number) -> {image_file: plate}."""
    df = pd.read_csv(path) if str(path).lower().endswith(".csv") else pd.read_excel(path)
    out = {}
    for img, plate in zip(df["image"].astype(str), df["plate"].astype(str)):
        out[img.strip()] = plate.strip()
    return out
