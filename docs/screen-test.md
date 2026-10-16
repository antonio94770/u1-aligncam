# Real-camera test with the pattern on a screen

A test that needs no printer: a tablet shows the pattern with known tool offsets, a
real camera photographs it, and `aligncam measure --truth` compares the result with
the offsets that were displayed.

**What it tests:** a real lens, focus, exposure, sensor noise, JPEG compression,
real viewing angles, and whether the quality gates accept or reject real photos.
With the ChArUco board on the same screen it also tests `calibrate-camera` on a
real lens.

**What it does not test:** the look of extruded filament on a textured plate (bead
shape, gloss, colour of real filament), real printing errors, the U1 camera and its
pose. A screen emits light and can add reflections and moiré. Report results as
**"real camera, pattern on a screen"**, never as a printed-pattern or U1 result.

## First results (2026-10-16)

iPad Air (4th generation) screen, iPhone 13 Pro Max (1x and 0.5x cameras), dark room,
one pattern with offsets of 96-289 um. Real camera, pattern on a screen; not a print
and not the U1.

| Pattern | Photos | Tool values accepted | Mean bias, worst tool-axis | Largest error |
|---|---:|---:|---:|---:|
| Coloured tools | 20 | 52 of 60 | 30 um (T2 y) | 52 um |
| All tools white (`--mono`) | 6 | 18 of 18 | 6 um (T1 x) | 11 um |

- The first fiducial detector found the pattern in 1 of 14 photos: it took the black
  surroundings as the plate. The local-contrast fallback (`measure/fiducials.py`)
  finds it in 13 of 14.
- With white tools the photo-to-photo scatter (2-3 um) is below the reported
  uncertainty (about 7 um). With coloured tools the error is dominated by a bias that
  repeats in every photo, so the uncertainty does not cover it.
- The colour bias is not yet attributed: screen subpixels (32 um apart at 264 ppi) or
  the lens' chromatic aberration (T1 changed sign between the 1x and 0.5x cameras).
- With the plate overexposed to light grey (automatic exposure) contrast drops;
  the photo taken with exposure lowered by about 4 EV had a largest error of 2 um.

## Why the screen size does not matter

The measurement fits a homography to the five fiducials. A uniformly scaled pattern
is indistinguishable from the same pattern seen from farther away, so offsets come
out in pattern millimetres whatever the displayed size. `screen-target` shows the
pattern at actual size when it fits, otherwise shrinks it to fit (tested in
`tests/test_screen.py`).

Offsets are snapped to whole screen pixels, so every tool's lines are exact
pixel-shifted copies and the truth does not depend on anti-aliasing. On a 264 ppi
screen one pixel is 96 um, so the random offsets take values in 96 um steps.

## 1. Generate the images

Example for an iPad Air (4th generation): 2360 x 1640 px, 264 ppi.

```bash
aligncam screen-target --width-px 2360 --height-px 1640 --ppi 264 --out out/screen-ipad --count 3
```

This writes `pattern_01.png` ... with their truth files `pattern_01.json` ..., and
`charuco.png`. The pattern fits at actual size (about 172 x 97 mm with its border);
the board is shown at 88 % of its printed size, which does not affect a lens
calibration.

For the colour-bias check of step 4 also generate a white version with the same
offsets (same seed):

```bash
aligncam screen-target --width-px 2360 --height-px 1640 --ppi 264 --out out/screen-ipad-mono --count 3 --mono
```

Copy the PNG files to the tablet (AirDrop, Files, cable). Keep the JSON files on the
computer and do not look at them before measuring if you want a blind test.

## 2. Set up the tablet

- Open a PNG in Photos, tap once to hide the interface. Do **not** zoom: an image
  with exactly the screen resolution is then shown pixel for pixel. If the image
  appears with borders or cropped, the resolution passed to `screen-target` does
  not match the screen.
- Hold the tablet in landscape.
- Turn off True Tone and Night Shift, set auto-lock to never, brightness about 50-70 %.
- Lay it flat on a table, away from windows and lamps that reflect on the glass.

## 3. Set up the phone camera (iPhone)

- Settings > Camera > Formats > **Most Compatible** (JPEG instead of HEIC).
- Settings > Camera > **Macro Control** on, then turn macro off in the camera app,
  so the phone does not silently switch lens at close range.
- Main test: the 1x camera. Shoot every photo in the same orientation (landscape),
  without zoom, flash off.
- Wide-angle test (closer to a wide chamber camera): the 0.5x ultra-wide camera.
  Settings > Camera > **Lens Correction** off, so iOS does not undistort the photos
  itself; then the lens must be calibrated (step 5).
- In a dark room the phone exposes for the screen and the dark "plate" comes out
  light grey. Before each series, tap and hold on the pattern to lock exposure and
  focus (AE/AF LOCK), then drag the sun icon down until the plate looks dark grey
  again and the white lines are not blown out.
- The phone always applies its own processing (sharpening, HDR). That is part of what
  this test measures.

## 4. Photograph the pattern

For each pattern take a series that covers:

| Series | Distance | Angle from vertical | Expectation |
|---|---|---|---|
| A | pattern fills about half of the frame width | 0-10 deg | accepted |
| B | same | 25-35 deg | accepted |
| C | same | 45-55 deg | accepted or rejected with a reason |
| D | pattern fills about a fifth of the frame width | 0-20 deg | coarse GSD: likely rejected (`low_snr`) |

Also rotate the phone around the pattern between shots. If moiré stripes are
visible, change the distance slightly.

**Colour-bias check.** A colour screen lights red, green and blue with separate
subpixels about a third of a pixel apart (32 um at 264 ppi), which may bias
coloured lines against white ones. Repeat series A and B three times with the same
offsets: the colour pattern, the white (`--mono`) pattern, and the colour pattern
with the tablet turned by 180 deg. A bias that disappears in white comes from colour
(screen subpixels or the lens' chromatic aberration), not from the geometry of the
measurement. For the 180 deg series turn the rotation lock **off**, so that the
image turns back upright: only then do the subpixels move relative to the pattern,
and a screen-made bias flips sign.

## 5. Calibrate the lens (needed for the ultra-wide camera)

Show `charuco.png` on the tablet and take 15-20 photos with the same camera, the
same zoom and the same orientation as the pattern photos: tilt the phone in
different directions (up to about 45 deg), and across the set place the board in
every part of the frame, including the corners. Then:

```bash
aligncam calibrate-camera photos/board --out out/iphone-uw.calib.yaml --report out/iphone-uw.calib.json
```

It refuses (exit code 1, with reasons) if the views are too few, too similar or do
not cover the frame; take more photos where it says.

## 6. Measure

```bash
aligncam measure photos/pattern_01_a1.jpg --truth out/screen-ipad/pattern_01.json --out out/meas/pattern_01_a1
```

With a calibrated lens add `-c out/iphone-uw.calib.yaml`. The output lists, for each
tool, the estimate, its uncertainty, `status` and `error_vs_truth_mm`;
`out/meas/pattern_01_a1_overlay.png` shows what was detected.

Record for every photo: the camera (1x / 0.5x), the approximate angle and distance,
the status and reasons, and the errors. Rejected photos are a result too: the goal
is that every accepted measurement is within about 40 um of the truth, and that bad
photos are rejected rather than measured wrong.
