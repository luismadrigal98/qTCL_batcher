"""Command line: `qtlc-batch run ...` (process photos) and `qtlc-batch benchmark ...` (compare with the Excel)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .benchmark import compare, load_quant
from .pipeline import Params, analyse_plate

LANE_NAMES = ["Standard 1", "Standard 2", "Standard 3"] + [f"Sample {i}" for i in range(1, 7)]


def _to_frame(res) -> pd.DataFrame:
    rows = []
    for b in range(res.values.shape[0]):
        for k in range(res.values.shape[1]):
            rows.append(dict(image=res.image, lane=LANE_NAMES[k] if k < len(LANE_NAMES) else f"Lane {k + 1}",
                             band=b + 1, value=res.values[b, k]))
    return pd.DataFrame(rows)


def cmd_run(args) -> int:
    params = Params(min_peak_prom=args.min_peak_prom, divisor=args.divisor, rotate=args.rotate)
    photos = sorted(Path(args.photos).glob("*.JPG")) + sorted(Path(args.photos).glob("*.jpg"))
    frames = []
    for p in photos:
        try:
            frames.append(_to_frame(analyse_plate(p, params)))
            print(f"ok    {p.name}")
        except Exception as e:  # keep going: one bad photo must not stop a batch
            print(f"FAIL  {p.name}: {e}", file=sys.stderr)
    if frames:
        pd.concat(frames).to_excel(args.out, index=False)
        print(f"wrote {args.out}")
    return 0 if frames else 1


def cmd_benchmark(args) -> int:
    params = Params(min_peak_prom=args.min_peak_prom, divisor=args.divisor)
    for plate in load_quant(args.xlsx):
        path = Path(args.photos) / plate.image_file
        try:
            res = analyse_plate(path, params)
        except Exception as e:
            print(f"{plate.plate:6s} {plate.image_file}  FAIL: {e}")
            continue
        c = compare(res.values, plate.values)
        print(f"{plate.plate:6s} {plate.image_file}  rank={c['rank_corr']:.3f}  pearson={c['pearson']:.3f}  "
              f"median ratio (mine/benchmark)={c['median_ratio']:.2f}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qtlc-batch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("benchmark", cmd_benchmark)):
        sp = sub.add_parser(name)
        sp.add_argument("--min-peak-prom", type=float, default=1.0)
        sp.add_argument("--divisor", type=float, default=2.0)
        sp.set_defaults(fn=fn)
        if name == "run":
            sp.add_argument("photos")
            sp.add_argument("--out", default="qtlc_results.xlsx")
            sp.add_argument("--rotate", type=int, default=None, help="degrees CCW (multiple of 90); default: auto-detect")
        else:
            sp.add_argument("xlsx")
            sp.add_argument("photos")
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
