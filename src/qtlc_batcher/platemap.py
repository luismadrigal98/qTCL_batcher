"""Plate map (which plant is on which lane of which plate) and the photo -> plate list.

Both are read from CSV / TSV / TXT or Excel (.xlsx/.xlsm). Columns are found by their header, not their position:

    plate, sample_no, plant, parent, meoh_ul, flower_mg      (only `plate` and `sample_no` are required)

The collaborators' headers work as they are ("Plate", "Sample # on Plate", "MeOH added (uL)", "Flower Mass (mg)",
"Plant", "Parent"), as does the `TLC` sheet of their workbook, which holds two side-by-side blocks (F2 population, and
F1 and parents without a Parent column; such a block is labelled "F1/parent"). Text files may be comma, semicolon or
tab separated, with decimal points or decimal commas.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

COLUMNS = ["parent", "plant", "plate", "sample_no", "meoh_ul", "flower_mg"]
TEXT_EXT = {".csv", ".tsv", ".txt"}
NO_PARENT_LABEL = "F1/parent"
ALIASES = {
    "plate": {"plate", "platename", "plateid", "tlcplate"},
    "sample_no": {"sampleno", "sample", "samplenumber", "sampleonplate", "samplenoonplate", "lane", "laneno",
                  "position"},
    "plant": {"plant", "plantid", "individual", "sampleid"},
    "parent": {"parent", "cross", "population", "family"},
    "meoh_ul": {"meohul", "meohaddedul", "meoh", "methanolul", "volumeul", "meohvolumeul", "extractionvolumeul"},
    "flower_mg": {"flowermg", "flowermassmg", "flowermass", "massmg", "tissuemg", "freshmassmg", "flowerweightmg"},
}
IMAGE_ALIASES = {"image", "photo", "file", "filename", "img", "picture", "imagefile"}


def _norm(x) -> str:
    return re.sub(r"[^a-z0-9]", "", str(x).lower())


def _empty(cell) -> bool:
    return cell is None or (isinstance(cell, float) and pd.isna(cell)) or str(cell).strip() == ""


def _canonical(cell, aliases=ALIASES):
    if _empty(cell):
        return None
    n = _norm(cell)
    for name, al in aliases.items():
        if n in al:
            return name
    return None


def _raw_tables(path, sheet=None):
    """Header-less tables to search: the text file, or the workbook's sheets (requested / `TLC` sheet first)."""
    path = Path(path)
    if path.suffix.lower() in TEXT_EXT:
        yield pd.read_csv(path, header=None, sep=None, engine="python", dtype=str, encoding="utf-8-sig",
                          skip_blank_lines=False)
        return
    xl = pd.ExcelFile(path)
    names = list(xl.sheet_names)
    first = [n for n in (sheet, "TLC") if n in names]
    for name in first + [n for n in names if n not in first]:
        yield pd.read_excel(xl, sheet_name=name, header=None, dtype=object)


def _parse_blocks(raw: pd.DataFrame):
    """Find the header row (first row naming both a plate and a sample column) and read every block of adjacent
    columns around each plate column. Returns a DataFrame with the canonical columns, or None."""
    for h in range(min(15, len(raw))):
        header = list(raw.iloc[h])
        canon = [_canonical(c) for c in header]
        if "plate" not in canon or "sample_no" not in canon:
            continue
        blocks = []
        for p in [i for i, c in enumerate(canon) if c == "plate"]:
            lo = p
            while lo > 0 and not _empty(header[lo - 1]):
                lo -= 1
            hi = p
            while hi < len(header) - 1 and not _empty(header[hi + 1]):
                hi += 1
            cols = {}
            for j in range(lo, hi + 1):
                if canon[j] and canon[j] not in cols:
                    cols[canon[j]] = j
            if "sample_no" not in cols:
                continue
            part = pd.DataFrame({name: raw.iloc[h + 1:, j].to_numpy() for name, j in cols.items()})
            if "parent" not in part.columns:
                part["parent"] = NO_PARENT_LABEL if len(blocks) else None
            blocks.append(part)
        if blocks:
            return pd.concat(blocks, ignore_index=True)
    return None


def _number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce")


def load_platemap(path, sheet: str | None = None) -> pd.DataFrame:
    """Plate map from CSV/TSV/TXT or Excel -> DataFrame[parent, plant, plate, sample_no, meoh_ul, flower_mg]."""
    df = None
    for raw in _raw_tables(path, sheet):
        df = _parse_blocks(raw)
        if df is not None:
            break
    if df is None:
        raise ValueError(f"{Path(path).name}: no header row with a plate column and a sample (lane) column was found. "
                         "Expected columns: plate, sample_no, plant, parent, meoh_ul, flower_mg "
                         "(see examples/platemap_template.csv)")
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[COLUMNS].copy()
    df["plate"] = df["plate"].map(lambda v: None if _empty(v) else str(v).strip())
    df["sample_no"] = _number(df["sample_no"])               # a typo in one cell must not crash
    df = df.dropna(subset=["plate", "sample_no"])
    df["sample_no"] = df["sample_no"].astype(int)
    for col in ("plant", "parent"):
        df[col] = df[col].map(lambda v: "" if _empty(v) else str(v).strip())
    for col in ("meoh_ul", "flower_mg"):
        df[col] = _number(df[col])
    df = df[~df.duplicated(["plate", "sample_no"], keep="first")]   # one row per lane, or the join duplicates data
    return df.reset_index(drop=True)


def load_image_plate_map(path) -> dict[str, str]:
    """CSV/TSV/TXT or Excel with an image column (image / photo / file ...) and a plate column -> {image: plate}."""
    for raw in _raw_tables(path):
        for h in range(min(15, len(raw))):
            canon = [None if _empty(c) else "image" if _norm(c) in IMAGE_ALIASES
                     else _canonical(c, {"plate": ALIASES["plate"]}) for c in raw.iloc[h]]
            if "image" in canon and "plate" in canon:
                i, p = canon.index("image"), canon.index("plate")
                return {str(img).strip(): str(plate).strip()
                        for img, plate in zip(raw.iloc[h + 1:, i], raw.iloc[h + 1:, p])
                        if not _empty(img) and not _empty(plate)}
    raise ValueError(f"{Path(path).name}: needs a header with an image column (image / photo / file) and a plate column")
