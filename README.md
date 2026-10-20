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
pytest -q                      # 21 tests should pass
```

Every later session starts with `source ~/venvs/qtlc/bin/activate`. (`-e` = editable: code changes apply immediately.)
Without the GUI libraries use `pip install -e .`. Works on Linux, macOS and Windows (`venv\Scripts\activate`).

## 2. Tell the program which plate each photo shows

The plate name (AP31 …) is handwritten on the plate and is **not** read from the photo. Provide it either way:

- a table with two columns, `image,plate` — see `examples/image_plates_benchmark.csv` (the 28 benchmark photos), or
- type it into the **Plate** column of the GUI (one name per group of repeat photos is enough).

**Plate map** (`--platemap`, GUI *Plate map…*): with a plate name, the output also carries plant, MeOH volume and flower
mass. Use a CSV/TSV/TXT or Excel file with these columns (only `plate` and `sample_no` are required; extra columns and
title rows are ignored) — see `examples/platemap_template.csv`:

```
plate,sample_no,plant,parent,meoh_ul,flower_mg
AP31,1,395-2,K3A1,50,20.9
```

Columns are recognised by name, so the collaborators' headers work as they are ("Plate", "Sample # on Plate", "MeOH
added (uL)", "Flower Mass (mg)", …), and so does the workbook itself or its `TLC` sheet saved as CSV (both side-by-side
blocks are read). Comma, semicolon or tab separators and decimal commas are all fine. Both tables accept the same
formats.

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
Defaults: MinPeakProm 3, Divisor 2, log-log calibration, correct integral, blank-based detection limit (§4, §6).
Options: `--min-peak-prom` `--divisor` `--rotate 180` (default: automatic) `--calibration linear` (qTLC paper's line)
`--web-app-overflow` (reproduce the web app's bug, §5) `--jobs 1` (one photo at a time, e.g. on a small laptop)
`--no-combine` (no Plates sheet, §4) `--no-streak-correction` (leave band-1 streaks in, §4).

### B. GUI (review and correct)

```bash
qtlc-gui ../Phenotyping/TLC_plate_photos
```

1. **Process all** — rotation, lanes, bands and integration for every plate (runs in the background, progress bar at the bottom).
2. Click a plate in the left list. Centre: coloured boxes = band rectangles (orange band 1, green band 2, blue band 3),
   **red = exactly the pixels that were integrated**. Right: integrals (rows = bands, columns = S500, S125, S31.25, 1–6;
   grey = below the plate's detection limit), calibrated samples, standard-curve R², detection limits, and **Flags**.
3. Fix problems: **drag a box** to move it, **drag an edge** to resize it; the plate recomputes at once.
   Tick **Edit all lanes of that band together** to apply one drag to the same band in every lane (keeps the tilt).
   Wheel = zoom, drag background = pan, *Fit* = reset the view, checkboxes toggle boxes / red overlay / processed image.
   *Re-detect layout* throws away your edits for that plate; the *Rotation* box shows and overrides the selected
   plate's rotation.
4. Type the plate names in the **Plate** column (or *Image→plate…* to load the CSV; *Plate map…* to load the workbook).
5. Status: `pending` / `ok` / `check` (has a CHECK flag) / `reviewed`; `*` = rectangles edited by hand.
   **Only plates to check** hides the rest; **Next to check ▶** (key **N**) jumps to the next one; **Mark reviewed** (key **R**).
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
| **Plates** | **the main result**: one row per plate × sample × band, repeat photos of a plate averaged: `calibrated`, `calibrated_per_mg`, plant / MeOH / flower mass, `n_photos`, `detected_in` (e.g. 2/2), `photo_spread` (disagreement between the photos) |
| **Groups** | which photos were treated as the same plate and why (same name / matching bands), which were used, which flagged ones were left out |
| **Tidy** | per photo: one row per lane × band: `integral`, `calibrated` (standard units), plant / `meoh_ul` / `flower_mg` (if plate map given), `calibrated_per_mg` = calibrated × MeOH(mL) ÷ flower mass(mg), `lod` / `detected`, `streak_corrected` / `streak_call` / `integral_before_streak_correction`, and the audit columns of §10 |
| **Integrals** | same layout as the collaborators' `Quant` sheet (band rows × 9 lanes) |
| **Flags** | every reason a plate deserves a look (below) |
| **Parameters** | MinPeakProm, Divisor, rotation, kernel, overflow option used |

**Repeat photos.** Many plates were photographed two to five times. Photos with the same plate name, and consecutive
photos whose sample lanes match (bands 2–3, r ≥ 0.80; on the current photos repeats correlate at ≥ 0.92 and different
plates at ≤ 0.53), are treated as one plate; an unnamed photo takes the name of its group. Each photo is calibrated on
its own standards and the photos are averaged (geometric mean for log-log). If some photos of a plate have a CHECK flag
and others do not, only the clean ones are used. Why: two photos of the same plate disagree by a median 18 % on a
sample's concentration; predicting a held-out photo, one other photo misses by 23 %, the average of the remaining
photos by 16 %. Photos of one plate taken far apart (e.g. AP51: IMG_3138 and IMG_3160–3163) are joined only through
the plate name. The 66 current photos are 42 plates (20 with repeats). **Photographing every plate twice** makes every
result more precise.

**Streaks in band 1.** Streaks are thin vertical stripes running from the solvent front down through band 1 (they barely
reach bands 2–3, which are never corrected). A lane is treated when the band-1 integration runs into the box's top edge
or the strip just above the box carries more signal than any clean plate shows. Two filters, each used for what it
does well (validated by painting realistic stripes onto clean lanes with known values):
- a *strict* per-column straight baseline decides whether pelargonidin is there: it removes ~99 % of a stripe from an
  empty slot. If what is left is below half the lowest standard, the call is "absent" (a note, nothing to review);
- otherwise a *gentle* filter (vertical-line opening) measures it: a real band under a stripe then reads −21 to −31 %
  instead of +42 % uncorrected. These lanes keep a CHECK flag.
On the current photos 98 sample lanes have a band-1 streak: 55 are resolved as "absent", 43 have pelargonidin under the
streak and are flagged. Agreement with the collaborators improves slightly with the correction (Pearson 0.887 → 0.897).

**Detection limit.** Each plate gets a limit of detection per band: empty boxes of the same size are integrated in the
blank plate between neighbouring lanes, and LOD = blank mean + 3 SD (the usual analytical definition). The Tidy sheet has
`lod` and `detected`; a sample below the LOD is reported with `calibrated` = 0 (not detected), and a standard below it is
left out of that band's curve. On the current photos the faintest standard (S31.25) clears its plate's LOD in 90 % of
cases, while 166 of 217 empty band-1 slots in clean sample lanes fall below it.

**Calibration** (per plate, per band, on the plate's own standards 500 / 125 / 31.25), two models:

- `loglog` (default): power law, log(signal) vs log(concentration).
- `linear` (what the qTLC paper does): straight line, inverted for the samples; values below the intercept → 0.

**The standards are not linear.** S500/S125 signal ratio ≈ 2.4–2.9 instead of 4 (response exponent ≈ 0.73), in the
collaborators' numbers and in ours. Predicting the middle standard from the outer two, a straight line is off by 16–18 %
(bias 0.82–0.84); the power law by 2–4 % (0.96–0.98). The `calibration` column records the model used.

**Flags** (Flags sheet, sorted CHECK first; GUI panel). A plate's status is `check` only if it has a CHECK flag.

| Severity | Flag | Meaning / what to do (see §10) |
|---|---|---|
| CHECK | *streak in band 1 of sample(s) …: pelargonidin is present under it* | value corrected for the streak but uncertain: check the box |
| note | *streak removed from band 1 of sample(s) …: no pelargonidin under it* | resolved automatically |
| CHECK | *layout: lane(s) … bands far from the other lanes' positions* | that sample lane runs higher/lower than the rest (overloaded sample, streak, uneven front) |
| CHECK | *standards are not decreasing S500 > S125 > S31.25* | boxes misplaced on the standards, or a bad plate |
| CHECK | *standard curve slope <= 0* / *R² < 0.90* | the band's calibration is unusable / weak |
| CHECK | *uint8 overflow* | only with `--web-app-overflow`: the web-app value is too low (§5) |
| note | *standard curve R² 0.90–0.95* | weak but usable curve |
| note | *a standard is below the detection limit; curve fitted through 2 standards* | that band's curve has two points only |
| note | *extrapolated above the S500 standard* | sample stronger than the top standard: outside the calibrated range |
| note | *layout: plate tilted N degrees (corrected)* / *band-3 boxes shortened above the origin line* | informational |
| note | *photo was rotated* / *rectangles were edited manually* | as named |

## 5. Known bug in the qTLC web app (documented; reproducible on request)

While reverse-engineering the server (synthetic test images with known pixel values) we found that **www.qtlc.app
under-integrates bright bands**. The trapezoid pair sums `v[i] + v[i+1]` are computed in **8-bit unsigned arithmetic**:
whenever two vertically adjacent pixels of the background-corrected image sum to ≥ 256, 256 is silently lost.

- **Trigger:** band pixels above ≈ 127 after inversion + background removal. Typical bands are below that; very strong
  bands (e.g. concentrated standards, pelargonidin in rich samples) can exceed it.
- **Effect:** the value is *lower* than the true integral, by up to ≈ 35–60 % in the cases seen, and not monotonic in
  signal (a synthetic peak of 200 integrates to *less* than one of 100). Only the affected columns are wrong.
- **Extent in your data:** at the web app's default settings, 8 of the 756 values in the benchmark workbook (4 plates, 1 standard lane) are affected.
  Since collaborators used the web app, those historical numbers carry the bug.
- **Behaviour here:** by default the integral is **correct** (no overflow). `--web-app-overflow` (CLI) or *Reproduce
  web-app uint8 overflow bug* (GUI) reproduces the bug, only to compare with old web-app numbers; then affected cells are
  shaded orange in the GUI and listed in the **Flags** sheet.

Everything else (gray conversion, top-hat background, peak/Divisor integration) matches the server exactly:
27/27 band values of a real plate and ~240 synthetic columns are identical (`tests/test_server_parity.py`).

## 6. Defaults differ from the web app on purpose

Batch defaults: **MinPeakProm 3** (web app: 1), Divisor 2, log-log calibration, correct integral, detection limit.
MinPeakProm decides which pixel columns count as signal. Too low and the faint haze in empty slots is integrated; too high
and the edges of every faint band are discarded, so faint bands (the S31.25 standard, low-pigment samples) are
under-read and the calibration bends. Measured on all 66 photos:

| MinPeakProm | S31.25 / S125 (expected ≈ 0.35) | curves whose S31.25 is lost | empty-slot noise (band 1) |
|---|---|---|---|
| 1 | 0.34 | 4 / 198 | ~1,800 |
| 3 | 0.33 | 16 / 198 | ~1,550 |
| 5 | 0.06 | **135 / 198** | ~65 |

MinPeakProm 5 (the v0.1/v0.2 default) silenced empty slots but destroyed the lowest standard; 3 keeps it, and the
remaining empty-slot noise is handled by the detection limit (§4) instead. Web-app parity is one option away
(`--min-peak-prom 1 --calibration linear --web-app-overflow`).

## 7. How the automatic layout works

Rotation (0/90/180/270) from lane-comb contrast and the position of the brown solvent-front spots (upside-down photos are
common: 33 of 66 in the current set). Nine lanes by fitting a regular comb to a "magenta-ness" map `(R+B)/2 − G`
(ignores brown spots and pencil marks; large pink objects such as a pencil are suppressed). Bands from the summed standard
lanes. **Plate tilt** is measured from the straight pencil lines (origin line, solvent front) by a projection-profile
search (±5°). Every lane with pigment is then measured within ±0.45 band spacings (never enough to mistake a band for
its neighbour); a robust trend through those lanes absorbs any remaining tilt or "smile" of the front, and each lane is
refined ±0.2 band spacings around that trend (empty lanes follow the trend). A lane whose top band is empty (most samples)
therefore cannot slip down by a whole band. Boxes are axis-aligned like in the web app; their
limits sit midway between neighbouring bands and are clipped so they stop above the origin line and below the solvent
front. The background kernel is the web-app rule applied to the largest-band rectangle: round(diagonal / 2) pixels.

## 8. Validation against the collaborators' Excel

The Excel is *not* ground truth: each person drew their own rectangles and chose their own thresholds. Absolute integrals
differ by a roughly constant factor, so the comparison uses concentrations **calibrated on each plate's own three
standards** (scale-free; no detection limit applied). `qtlc-batch benchmark`, 28 plates, 504 calibrated sample values,
fully automatic:

| Version / settings | Pearson | Spearman | median ratio |
|---|---|---|---|
| v0.1 (MinPeakProm 5, linear) | 0.88 | 0.86 | 1.04 |
| v0.2 layout fix (MinPeakProm 5, linear) | 0.95 | 0.89 | 1.05 |
| current defaults viewed with a linear curve (MinPeakProm 3) | 0.93 | 0.90 | 1.02 |
| current defaults (MinPeakProm 3, log-log) | 0.89 | 0.88 | 1.03 |

Agreement with a noisy human dataset cannot choose the calibration model: log-log scores lower here partly because it
amplifies faint-band noise on *both* sides. The middle-standard test (§4) and the faint-standard table (§6) are the
evidence for the defaults.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `qtlc-batch: command not found` | `source ~/venvs/qtlc/bin/activate` first |
| `venv` creation fails inside the project folder | create it in `~` as in §1 |
| Boxes one band too low/high on a tilted plate | fixed in v0.2 (tilt + trend model); if it recurs, the *far from the other lanes* flag names the lane — drag the boxes |
| Streaks: band 1 shows signal in samples that look pelargonidin-free | the *streak* flag; see §10 |
| GUI does not start (Linux, "could not load the Qt platform plugin xcb") | install the missing xcb library named in the message, usually `sudo apt install libxcb-xinerama0`; on a headless server use `QT_QPA_PLATFORM=offscreen` |
| `could not determine plate orientation / no lane comb` | photo has no usable lanes; process it in the GUI with *Rotation* set by hand, or crop the photo |
| Plate flagged "standards are not decreasing" | boxes are misplaced (or the plate is mislabelled): fix in the GUI |
| Numbers differ from the web page | check parameters (MinPeakProm 3 here, 1 there), the calibration model, and the overflow option |

## 10. Fixing what remains: the review pass

Everything that software can decide reliably is automatic: repeat photos are averaged (a flagged photo is replaced by
its clean twin), and streaks are removed where the call is clear. On the current photos that leaves **18 of 42 plates**
where every usable photo still has a CHECK flag; those need eyes:

1. `qtlc-gui ../Phenotyping/TLC_plate_photos` → *Process all* → tick **Only plates to check** → press **N**.
2. Read the Flags panel, then fix by flag:
   - **pelargonidin present under a streak**: the value is already corrected; look whether the box sits on a real
     band (to see the raw streak, untick *Remove streaks…* and tick it again afterwards, otherwise the export warns about
     mixed settings). To tighten it, tick *Edit all lanes of that band together* and drag the **top edge of a band-1
     box** down to where the band (not the smear) starts.
   - **lane far from the others**: drag that lane's boxes onto its bands (untick *Edit all lanes* first).
   - **standards not decreasing / slope ≤ 0 / R² < 0.90**: look at the three standard lanes; fix misplaced boxes. If the
     standards themselves look wrong (smeared, missing, front too close), the plate cannot be calibrated: re-run it.
3. **R** marks the plate reviewed, **N** goes to the next one; *Save session…* now and then; *Export Excel…* at the end.

**Editing and your data.** An edit changes the numbers of the plate you edit (that is the point), but it brings human
judgement back, so keep it controlled:

- Edits only move boxes; MinPeakProm, Divisor, background and calibration stay identical for every plate.
- Every export keeps an **audit trail**: `integral_auto` / `calibrated_auto` (what the automatic boxes give with the same
  settings) next to the edited values, and `box_edited` per box. Run your downstream analysis both ways (or without the
  edited plates) to show the edits do not drive the result.
- Editing a **standard** box changes the calibration, hence `calibrated` for every sample on that plate; edit standards
  only when a box is clearly misplaced.
- Tick **Blind review (hide numbers)** while placing boxes, so the values cannot influence where you draw them; decide
  from the image only, with fixed rules (box edge at the dip between smear and band; empty slot if no band is visible).
- One person reviewing all plates is more consistent than several; if several, have them overlap on a few plates and
  compare.
- The session file stores both the automatic and the edited boxes, so every edit is reproducible and reversible
  (*Re-detect layout* restores the automatic boxes for a plate).

**Plates to re-run in the lab** rather than fix in software: short runs where the bands sit under the solvent front
(AP41 IMG_3126/3127, AP57 IMG_3145/3146), the curved-front plate AP54 (IMG_3141/3142), and the heavily smeared plates
(AP40 IMG_3123–3125, AP65–AP75 IMG_3152–3159) if their band-1 values matter. Smearing and tailing on TLC usually come from
overloaded spots or particles in the extract, so less extract per spot or a clarified (centrifuged) extract is the
usual remedy; check this against your own protocol.

## 11. Limitations / next steps

- Lane count and band count are fixed at 9 × 3 in the automatic detector.
- Plate name is not read from the photo (groups of repeat photos only need one name; unnamed groups are listed in the
  Groups sheet).
- Streak correction is validated on painted stripes and on 3 clean lanes with real pelargonidin; more lanes with known
  pelargonidin under a streak would sharpen the "present" values.
- Calibration assumes the 4-fold standard series 500 / 125 / 31.25 (units of the standards) and a linear fit per band.
- Layouts that fail detection are flagged, not silently accepted.

## Citation

If you use this software, please cite in your work the following:

@article{macfhionnlaoichToolkitQuantifyTarget2018,
  title = {A {{Toolkit}} to {{Quantify Target Compounds}} in {{Thin-Layer-ChromatographyExperiments}}},
  author = {Mac Fhionnlaoich, Niamh and Ibsen, Stuart and Serrano, Luis A. and Taylor, Alaric and Qi, Runzhang and Guldin, Stefan},
  year = 2018,
  month = dec,
  journal = {Journal of Chemical Education},
  volume = {95},
  number = {12},
  pages = {2191--2196},
  issn = {0021-9584, 1938-1328},
  doi = {10.1021/acs.jchemed.8b00144},
  urldate = {2026-10-20},
  abstract = {Abstract             Thin-layer chromatography (TLC) is one of the basic analytical procedures in chemistry and allows the demonstration of various chemical principles in an educational setting. An often-overlooked aspect of TLC is the capability to quantify isolated target compounds in an unknown sample. Here, we present a suitable route to implement quantitative analysis in a lesson plan. We~provide both a stand-alone software and an online webapp that allow students to obtain quantitative information from a developed TLC plate and present two suitable experiments, namely, the absorbance-based quantification of the colorant Sudan IV and the fluorescence-based quantification of rhodamine 6G, a fluorophore widely used in biotechnology. Students conduct TLC experiments following established protocols,~take pictures of their TLC plates with mobile phones, and subsequently quantify the~different compounds in the separate bands they observe.},
  copyright = {https://pubs.acs.org/page/policy/authorchoice\_ccby\_termsofuse.html},
  langid = {english}
}