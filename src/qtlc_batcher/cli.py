"""Command line: `qtlc-batch run ...` (process photos) and `qtlc-batch benchmark ...` (compare with the Excel)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .benchmark import calibrated_agreement, load_quant
from .pipeline import Params, analyse_plate
from .platemap import load_image_plate_map, load_platemap
from .results import export_workbook, plate_tables, quality_flags

PHOTO_PATTERNS = ("*.JPG", "*.jpg", "*.jpeg", "*.JPEG", "*.png", "*.PNG", "*.tif", "*.tiff")


def _params(args) -> Params:
    return Params(min_peak_prom=args.min_peak_prom, divisor=args.divisor,
                  rotate=getattr(args, "rotate", None), uint8_wrap=not args.no_uint8_wrap)


def _photos(folder) -> list[Path]:
    seen, out = set(), []
    for pat in PHOTO_PATTERNS:
        for p in sorted(Path(folder).glob(pat)):
            if p.name.lower() not in seen:
                seen.add(p.name.lower())
                out.append(p)
    return sorted(out)


def cmd_run(args) -> int:
    params = _params(args)
    img_plate = load_image_plate_map(args.image_plates) if args.image_plates else {}
    platemap = load_platemap(args.platemap) if args.platemap else None
    items = []
    for p in _photos(args.photos):
        plate = img_plate.get(p.name) or img_plate.get(p.stem) or img_plate.get(p.stem.replace("IMG_", ""))
        try:
            res = analyse_plate(p, params)
            df, cal = plate_tables(res, plate, platemap)
        except Exception as e:  # one bad photo must not stop a batch
            print(f"FAIL  {p.name}: {e}", file=sys.stderr)
            items.append(dict(image=p.name, plate=plate, error=str(e)))
            continue
        fl = quality_flags(res, cal, res.turns)
        items.append(dict(image=p.name, plate=plate, res=res, df=df, cal=cal, flags=fl))
        print(f"ok    {p.name}" + (f"  [{len(fl)} flag(s)]" if fl else ""))
    if not any("res" in it for it in items):
        return 1
    export_workbook(args.out, items, params)
    print(f"wrote {args.out}")
    return 0


def cmd_benchmark(args) -> int:
    params = _params(args)
    ref, mine = [], []
    for plate in load_quant(args.xlsx):
        try:
            res = analyse_plate(Path(args.photos) / plate.image_file, params)
        except Exception as e:
            print(f"{plate.plate:6s} {plate.image_file}  FAIL: {e}")
            continue
        ref.append(plate.values)
        mine.append(res.values)
        a = calibrated_agreement([res.values], [plate.values])
        print(f"{plate.plate:6s} {plate.image_file}  calibrated r={a['pearson']:.3f}  rank={a['spearman']:.3f}")
    a = calibrated_agreement(mine, ref)
    print(f"\nall plates: calibrated sample concentrations, {a['n']} values: pearson {a['pearson']:.3f}, "
          f"spearman {a['spearman']:.3f}, median ratio auto/benchmark {a['median_ratio']:.2f}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qtlc-batch", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("benchmark", cmd_benchmark)):
        sp = sub.add_parser(name)
        sp.add_argument("--min-peak-prom", type=float, default=5.0, help="qTLC MinPeakProm (web-app default 1; batch default 5)")
        sp.add_argument("--divisor", type=float, default=2.0, help="qTLC Divisor (default 2)")
        sp.add_argument("--no-uint8-wrap", action="store_true",
                        help="integrate correctly instead of reproducing the web app's uint8 overflow bug")
        sp.set_defaults(fn=fn)
        if name == "run":
            sp.add_argument("photos", help="folder with plate photos")
            sp.add_argument("--out", default="qtlc_results.xlsx")
            sp.add_argument("--rotate", type=int, default=None, help="degrees CCW (multiple of 90); default: auto-detect")
            sp.add_argument("--platemap", help="workbook with the `TLC` sheet (plant / MeOH / flower mass per lane)")
            sp.add_argument("--image-plates", help="CSV/Excel with columns image,plate (which plate each photo shows)")
        else:
            sp.add_argument("xlsx", help="workbook with the benchmark `Quant` sheet")
            sp.add_argument("photos")
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
