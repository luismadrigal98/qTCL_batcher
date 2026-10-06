# qTCL_batcher

Batch re-implementation of the **qTLC web app** (www.qtlc.app; Mac Fhionnlaoich et al., *J. Chem. Educ.* 2018) for the
anthocyanin TLC plates: 9 lanes (3 standards S500 / S125 / S31.25 + 6 samples) × 3 bands (top to bottom:
pelargonidin, cyanidin, delphinidin). Instead of one photo at a time with hand-drawn rectangles, every plate is
processed with the same rules, flagged when something looks off, and corrected in a GUI when needed.

Python package `qtlc_batcher` (`src/` layout): engine, command line (`qtlc-batch`) and PyQt5 GUI (`qtlc-gui`).

---

## 1. Install (once)

Put the **environment in your home directory**, not next to the code: the project lives on an NTFS-style drive
where virtual-environment symlinks can fail.

```bash
python3 -m venv ~/venvs/qtlc
source ~/venvs/qtlc/bin/activate
pip install --upgrade pip
cd "/mnt/1692B2EF92B2D28B/GRA/Anthocyanin_project/TCL Imaging/qTCL_batcher"
pip install -e ".[gui,dev]"
pytest -q                      # 13 tests should pass
```

Every later session starts with `source ~/venvs/qtlc/bin/activate`. (`-e` = editable: code changes apply immediately.)
Without the GUI libraries use `pip install -e .`. Works on Linux, macOS and Windows (`venv\Scripts\activate`).

## 2. Tell the program which plate each photo shows

The plate name (AP31 …) is handwritten on the plate and is **not** read from the photo. Provide it either way:

- a CSV with two columns, `image,plate` — see `examples/image_plates_benchmark.csv` (the 28 benchmark photos), or
- type it into the **Plate** column of the GUI.

With a plate name and the workbook's `TLC` sheet (`--platemap`), the output also carries plant, MeOH volume and flower mass.

## 3. Run it

### A. Command line (whole folder, no clicking)

```bash
source ~/venvs/qtlc/bin/activate
cd "/mnt/1692B2EF92B2D28B/GRA/Anthocyanin_project/TCL Imaging/qTCL_batcher"

qtlc-batch run ../Phenotyping/TLC_plate_photos \
    --out outputs/results.xlsx \
    --platemap ../Phenotyping/GenotpedPigmentExtractionSamplesList.xlsx \
    --image-plates examples/image_plates_benchmark.csv
```

Photos are processed in parallel (`--jobs`, default up to 4 cores, ~0.6 GB RAM each); the output folder is created if
missing. A failing photo is reported and skipped; the batch continues.
Useful options: `--min-peak-prom 5` `--divisor 2` `--rotate 180` (default: automatic) `--no-uint8-wrap` (see §5)
`--calibration loglog` (see §4) `--jobs 1` (one at a time, e.g. on a small laptop).

### B. GUI (review and correct)

```bash
qtlc-gui ../Phenotyping/TLC_plate_photos
```

1. **Process all** — rotation, lanes, bands and integration for every plate (runs in the background, progress bar at the bottom).
2. Click a plate in the left list. Centre: coloured boxes = band rectangles (orange band 1, green band 2, blue band 3),
   **red = exactly the pixels that were integrated**. Right: integrals (rows = bands, columns = S500, S125, S31.25, 1–6),
   calibrated samples, standard-curve R², and **Flags**.
3. Fix problems: **drag a box** to move it, **drag an edge** to resize it; the plate recomputes at once.
   Wheel = zoom, drag background = pan, *Fit* = reset the view, checkboxes toggle boxes / red overlay / processed image.
   *Re-detect layout* throws away your edits for that plate; the *Rotation* box shows and overrides the selected
   plate's rotation.
4. Type the plate names in the **Plate** column (or *Image→plate…* to load the CSV; *Plate map…* to load the workbook).
5. **Mark reviewed** when a plate is OK. Status: `pending` / `ok` / `flags` (look at it) / `reviewed`; `*` = rectangles edited by hand.
6. Changing MinPeakProm / Divisor / kernel / overflow option recomputes the **current plate only** (half a second after
   you stop typing) so you can explore. The *Calibration* box re-fits every plate at once (no image processing).
   Press **Re-process all** to apply the same values to every plate (keeps rotation and rectangles). *Export Excel…*
   warns if plates were computed with different parameters.
7. **Export Excel…** writes the workbook; **Save session… / Load session…** keep rotations, plate names, edits and parameters.

### C. Compare with the collaborators' Excel

```bash
qtlc-batch benchmark ../Phenotyping/GenotpedPigmentExtractionSamplesList.xlsx ../Phenotyping/TLC_plate_photos
```

## 4. What you get

`results.xlsx` sheets:

| Sheet | Content |
|---|---|
| **Tidy** | one row per lane × band: `integral`, `calibrated` (standard units), plant / `meoh_ul` / `flower_mg` (if plate map given), `calibrated_per_mg` = calibrated × MeOH(mL) ÷ flower mass(mg) |
| **Integrals** | same layout as the collaborators' `Quant` sheet (band rows × 9 lanes) |
| **Flags** | every reason a plate deserves a look (below) |
| **Parameters** | MinPeakProm, Divisor, rotation, kernel, overflow option used |

**Calibration** (per plate, per band, on the plate's own standards 500 / 125 / 31.25), two models:

- `linear` (default; what the qTLC paper does): straight line, inverted for the samples; values below the intercept → 0.
- `loglog`: power law, log(signal) vs log(concentration). **The standards are not linear**: on the 28 benchmark plates the
  S500/S125 signal ratio is 2.44 instead of 4 (response exponent ≈ 0.73), and a straight line through S500 and S31.25
  under-predicts the middle standard by 18 % (power law: 4 %). The straight line therefore biases mid-range samples and
  clips some visible ones to 0. Choose with `--calibration` / the GUI *Calibration* box; the `calibration` column records it.

**Flags** (Flags sheet / GUI panel):

| Flag | Meaning / what to do |
|---|---|
| *layout: plate tilted N degrees (corrected)* | informational; boxes follow the tilt |
| *layout: lane(s) … band position at the search limit* | that lane's bands sit higher/lower than the tilt predicts (streaks, uneven run) — check those boxes |
| *layout: band-3 boxes shortened above the origin line* | bands run close to the pencil origin line; boxes stop above it |
| *streak from the solvent front runs into the band-1 box* | the sample smears vertically; band 1 (pelargonidin) may be smear — tighten the box |
| *standards are not decreasing S500 > S125 > S31.25* | boxes misplaced on the standards, or a bad plate |
| *standard curve slope <= 0* / *R² < 0.95* | the band's calibration is unusable / weak |
| *a standard has no signal; power law fitted through 2 standards* | `loglog` only |
| *extrapolated above the S500 standard* | sample stronger than the top standard: outside the calibrated range |
| *uint8 overflow* (§5), *photo was rotated*, *rectangles were edited manually* | as named |

## 5. Known bug in the qTLC web app (reproduced here; documented on purpose)

While reverse-engineering the server (synthetic test images with known pixel values) we found that **www.qtlc.app
under-integrates bright bands**. The trapezoid pair sums `v[i] + v[i+1]` are computed in **8-bit unsigned arithmetic**:
whenever two vertically adjacent pixels of the background-corrected image sum to ≥ 256, 256 is silently lost.

- **Trigger:** band pixels above ≈ 127 after inversion + background removal. Typical bands are below that; very strong
  bands (e.g. concentrated standards, pelargonidin in rich samples) can exceed it.
- **Effect:** the value is *lower* than the true integral, by up to ≈ 35–60 % in the cases seen, and not monotonic in
  signal (a synthetic peak of 200 integrates to *less* than one of 100). Only the affected columns are wrong.
- **Extent in your data:** at the web app's default settings, 8 of the 756 values in the benchmark workbook (4 plates, 1 standard lane) are affected.
  Since collaborators used the web app, those historical numbers carry the bug.
- **Behaviour here:** by default the bug is **reproduced** (`uint8_wrap` on) so results are comparable with web-app
  numbers. Affected cells are shaded orange in the GUI and listed in the **Flags** sheet. To get the mathematically
  correct integral use `--no-uint8-wrap` (CLI) or untick *Reproduce web-app uint8 overflow* (GUI) and re-process.
- For new analyses, prefer the corrected integral, or at least check the flagged cells.

Everything else (gray conversion, top-hat background, peak/Divisor integration) matches the server exactly:
27/27 band values of a real plate and ~240 synthetic columns are identical (`tests/test_server_parity.py`).

## 6. Defaults differ from the web app on purpose

Batch default **MinPeakProm = 5** (web app: 1), Divisor 2. With 1, the faint smear above empty band-1 slots is integrated
(hundreds to thousands of units); the collaborators tuned the value until empty bands read ~0 (see
`TLC_Imaging_Documents/qTLC notes.docx`). MinPeakProm 5 with narrower rectangles gave the best agreement with their
Excel. The engine accepts any value, so web-app parity is one option away (`--min-peak-prom 1`).

## 7. How the automatic layout works

Rotation (0/90/180/270) from lane-comb contrast and the position of the brown solvent-front spots (upside-down photos are
common: 33 of 66 in the current set). Nine lanes by fitting a regular comb to a "magenta-ness" map `(R+B)/2 − G`
(ignores brown spots and pencil marks; large pink objects such as a pencil are suppressed). Bands from the summed standard
lanes. **Plate tilt** is measured from the straight pencil lines (origin line, solvent front) by a projection-profile
search (±5°); every lane is placed on that tilt and then corrected by at most a quarter of the band spacing, so a lane whose
top band is empty (most samples) can never slip down by a whole band. Boxes are axis-aligned like in the web app; their
limits sit midway between neighbouring bands and are clipped so they stop above the origin line and below the solvent
front. The background kernel is the web-app rule applied to the largest-band rectangle: round(diagonal / 2) pixels.

## 8. Validation against the collaborators' Excel

The Excel is *not* ground truth: each person drew their own rectangles and chose their own thresholds. Absolute integrals
differ by a roughly constant factor, so the comparison uses concentrations **calibrated on each plate's own three
standards** (scale-free). Current `qtlc-batch benchmark` result (28 plates, 504 calibrated sample values, default
parameters, fully automatic, no manual correction): **Pearson 0.88, Spearman 0.86, median ratio auto/benchmark 1.04**;
24 of 28 plates have r ≥ 0.90. The weaker plates (AP31 0.81, AP35 0.43, AP42 0.39, AP38 0.26) are mostly band 3, where the
S31.25 standard is nearly empty and the 3-point calibration is ill-conditioned; band 2 agrees closely there
(AP38: 143.3 vs 146.9, 308.5 vs 310.3). Band 1 is mostly empty in samples, so its agreement is dominated by noise.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `qtlc-batch: command not found` | `source ~/venvs/qtlc/bin/activate` first |
| `venv` creation fails inside the project folder | create it in `~` as in §1 |
| Boxes one band too low/high on a tilted plate | fixed in v0.2 (tilt model); if it recurs, the flag *search limit* names the lane — drag the boxes |
| Streaks: band 1 shows signal in samples that look pelargonidin-free | the *streak* flag; drag the band-1 box down to the real band edge |
| GUI does not start (Linux, "could not load the Qt platform plugin xcb") | install the missing xcb library named in the message, usually `sudo apt install libxcb-xinerama0`; on a headless server use `QT_QPA_PLATFORM=offscreen` |
| `could not determine plate orientation / no lane comb` | photo has no usable lanes; process it in the GUI with *Rotation* set by hand, or crop the photo |
| Plate flagged "standards are not decreasing" | boxes are misplaced (or the plate is mislabelled): fix in the GUI |
| Numbers differ from the web page | check parameters (MinPeakProm 5 here, 1 there) and the overflow option |

## 10. Limitations / next steps

- Lane count and band count are fixed at 9 × 3 in the automatic detector.
- Plate name is not read from the photo.
- Calibration assumes the 4-fold standard series 500 / 125 / 31.25 (units of the standards) and a linear fit per band.
- Layouts that fail detection are flagged, not silently accepted.
