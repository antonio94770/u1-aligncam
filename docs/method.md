# Method

## The pattern

Every tool prints, on a single layer, a set of **cells**. A cell measures one axis
of one tool and contains three gratings that sit side by side without
overlapping, like the scales of a caliper: **T0 | Tn | T0**.

- two **reference gratings** printed by T0 (pitch `p0`, default 1.20 mm), one on
  each side;
- the **measured grating** printed by Tn in the middle (pitch
  `pn = p0 * vernier_ratio`, default 1.14 mm).

The central line (index k = 0) of every grating is commanded at the cell centre.

**Why a sandwich.** An early version used only two gratings (T0 above, Tn below).
On synthetic images with realistic uncorrected lens distortion (k1 around -0.1 to
-0.3, typical of wide-angle webcams), the two halves sit a few millimetres apart
and see a different distortion. A 5-point homography cannot remove that, and the
differential error reached tenths of a millimetre. With the reference averaged
from two gratings placed symmetrically around Tn, every error field that varies
linearly across the cell cancels: distortion, homography error, scale,
perspective. A camera calibration (`camera_matrix`/`dist_coeffs` in the config)
is still recommended, but it is no longer mandatory for moderate distortion.
X cells use vertical lines (they measure X), Y cells use horizontal lines. There is
one X and one Y cell per tool, plus a **control cell** where T0 prints both halves.
Four corner disks and an orientation dot, all printed by T0, define the pattern
frame.

**Placement and rotation.** The pattern can be placed anywhere on the bed
(`center_on_bed_mm`) and rotated by 0, 90, 180 or 270 degrees (`rotation_deg`), so
that it sits close to a camera on a side panel with its long side along the panel
(see [u1-camera.md](u1-camera.md)). Right angles keep every line parallel to a
printer axis. The measurement always works in the pattern frame; the offset
proposal rotates the result (and swaps the sigmas) back into printer X/Y.

**Oblique views.** A side camera sees near fiducials several times larger than far
ones. The fiducial search therefore allows corner disks to differ in area by up to
8x and compares the orientation dot with its neighbouring corner rather than with
the mean; the homography and the dot prediction still reject wrong matches.

Measuring along the direction *across* the lines has a useful side effect:
extrusion lag and ooze act *along* the line, so they barely affect the measured
coordinate. The ends of each line, where blobs form, are excluded.

## Why not a pure vernier or a moiré pattern?

| Option | Verdict |
|---|---|
| Classic vernier, read as "which pair of lines coincides" | Readable by eye, but quantised to `p0 - pn` and fragile when lines are blurred. Kept as a **cross-check**: the software also does this reading. |
| Moiré from overprinting two gratings | Rejected: two tools printing over each other risk nozzle drag, and the moiré contrast depends on bead height and opacity, which is hard to control on a real print. |
| **Phase fit of periodic gratings** | **Chosen** as the main estimator: every pixel of every line contributes, which gives sub-pixel precision. |

The vernier pitch difference does **not** magnify the signal for the camera. The two
gratings are side by side, not superimposed, so no optical beat forms in the image.
The camera still has to resolve the individual lines: the pitch must be at least about
2.5 pixels. The precision comes from averaging over many lines and pixels, not from
the vernier. The vernier keeps the pattern readable by a human with a loupe, and gives
a second, independent estimate.

## Measurement pipeline

1. **Fiducials.** Colour distance from the dominant (bed) colour, Otsu threshold,
   circular blobs. The four corner disks are chosen among the candidates as the
   convex quadrilateral whose homography predicts the orientation dot, which also
   fixes the corner order. Centres are refined with a background-subtracted
   weighted centroid. Homography from the 5 points; the RMS reprojection error is
   a quality gate.
2. **Rectification.** Each grating is resampled from the image on a regular mm grid
   (20 px/mm, bicubic) through the homography.
3. **Line signal.** The bed colour is the median of the two ends of the patch,
   which contain no lines. Pixels are projected onto the colour direction
   "bed → filament", so lines are positive whatever the colours. The tool identity
   comes from the layout, not from colour segmentation, which keeps the method
   working with similar filament colours.
4. **Period choice and coarse check.** The phase is known modulo one pitch. The
   T0 gratings are unwrapped around 0: T0 also printed the fiducials, so its
   shift is small by construction. The Tn grating is unwrapped around the T0
   reference, which limits the range to +/- half a pitch (`max_offset_mm` = 0.5).
   An independent coarse position, the midpoint of the grating edges found on the
   local modulation envelope, must agree, or the cell is rejected
   (`coarse_mismatch`). An earlier centroid-based version was biased by uneven
   lighting. In the benchmark it occasionally picked the wrong period for one
   reference, which produced accepted errors of about p/2 = 0.6 mm. Edge-based
   checking removed them.
5. **Phase fit.** Least squares on the profile averaged along the lines: quadratic
   trend (illumination) plus 4 harmonics of the known pitch. The phase of the
   fundamental gives the grating shift, unwrapped with the coarse position.
   Two extra terms model a linear drift of the fundamental (a local scale error),
   so the phase is read at the grating centre even when distortion stretches it.
6. **Offset.** `offset = shift(Tn) - (shift(T0 before) + shift(T0 after)) / 2`.
7. **Vernier cross-check.** The line centres are detected one by one;
   `d_k = u_n(k) - u_0(k) = offset + k (pn - p0)` gives an independent estimate and
   the coincidence index a human would read.
8. **Uncertainty.** Quadrature sum of: the scatter between 6 bands along the lines
   after removing a linear trend (a shear, cancelled by the sandwich), or the
   least-squares covariance if larger; the scatter of individual line centres
   after removing a quadratic trend (per-line placement errors, which bands cannot
   see); and a systematic floor (`sigma_floor_mm`).

## Lens calibration (`aligncam calibrate-camera`)

The fiducial homography only models a pinhole camera. On a wide-angle chamber
camera the pattern may sit near the image edge, where radial distortion bends the
gratings, so the synthetic U1 benchmark rejects most measurements without a lens
calibration. `calibrate-camera` fills `camera_matrix` and `dist_coeffs` in the
`camera` config section; `measure` then runs `cv2.undistort` before everything else.

**Board: ChArUco, not a plain checkerboard.** Every ChArUco corner is identified
by the ArUco markers next to it, so a photo where the board is only partly in the
frame still gives its visible corners. With a fixed camera the only way to observe
the image corners, where distortion is largest, is to hold the board there, and it
rarely fits entirely. A plain checkerboard is lost as soon as one inner corner is
hidden. The default board is 9 x 6 squares of 25 mm with 18 mm `DICT_5X5_100`
markers (225 x 150 mm, fits A4), configurable under `calibration.board`.

**Procedure.**

1. `aligncam calibration-board --out out/charuco.svg`, print at 100 % on matte
   paper and glue it to something flat (a stiff card or a glass plate). The print
   scale does not change the intrinsics, flatness does.
2. With the camera at the resolution and focus used for measuring, take 15-25
   snapshots. Move the board over the whole frame, especially the corners and
   edges, and tilt it 20-45 deg in different directions. Do not leave it flat on the
   bed: parallel views cannot separate the focal length from the distance.
3. `aligncam calibrate-camera PHOTOS_DIR --out out/camera.calib.yaml`, then measure
   with `-c config/camera.u1.template.yaml -c out/camera.calib.yaml`.

**Model.** `cv2.calibrateCamera` on the detected corners. The default distortion
model frees k1, k2, p1, p2 and fixes k3 = 0 (`distortion_model`: `k1`, `k1k2`,
`k1k2p`, `full`). Views with fewer than 8 corners are skipped; a view whose RMS is
more than 3 times the median (and above the RMS limit) is dropped once and the
calibration repeated.

**Quality gates.** Nothing is written when one fails; the command prints why and
exits with code 1.

| Reason | Trigger (default) |
|---|---|
| `too_few_views` | fewer than 10 usable views |
| `high_reprojection` | RMS reprojection error above 1.0 px |
| `poor_coverage` | corners in fewer than 60 % of an 8 x 5 grid over the image; the empty image corners are named |
| `low_tilt_spread` | board normals of all views within 20 deg of each other |
| `unstable_intrinsics` | OpenCV's std of fx or fy above 1 %, or of the principal point above 15 px |

**Synthetic validation.** `tests/test_calib.py` renders the board through a
`PinholeCamera` with a known k1 (`aligncam.sim.board`) and checks that the
calibration recovers k1 within 0.005 and the focal length within 0.5 %, that the
default model undistorts like the true lens (90 % of the image within 0.2 px after
a homography), and that each gate fires. The benchmark case `u1_rcal` measures the
same scenes as `u1` after undistorting them with a calibration recovered from 15
synthetic board photos ([benchmark.md](benchmark.md)).

**Limits.** A calibration is valid for one resolution and one focus setting. A lens
wider than the polynomial model can describe would need a fisheye model, which is
not supported. The simulator's own k1-only lens folds back near the image corners
of the widest simulated lenses; board views there are rendered as background, so
some synthetic calibrations of those lenses fail `poor_coverage`.

## Quality gates (reject instead of guessing)

| Reason | Trigger |
|---|---|
| `no_fiducials` | fiducial configuration not found |
| `fiducial_reprojection` | homography does not fit the 5 fiducials |
| `outside_image` | a cell is not fully visible |
| `low_contrast` | filament and bed colours too close |
| `polarity_mismatch` | one filament is lighter than the plate and the other darker (see below) |
| `shading_sensitive` | filament too close to the plate colour for its brightness, or T0 and Tn react very differently to bead shading |
| `low_snr` | periodic signal too weak |
| `high_uncertainty` | sigma above `max_sigma_mm` (default 0.02 mm) |
| `phase_vernier_mismatch` | the two estimators disagree by more than 0.03 mm |
| `coarse_mismatch` | the grating edges disagree with the phase reading by more than 0.35 pitch (wrong period) |
| `out_of_range` | offset larger than `max_offset_mm` (unambiguous range) |
| `control_bias` | the T0-vs-T0 control cell does not read zero: all tools rejected |

## Known limitations

- The method measures **where the material ended up**, not where the nozzle was.
  Systematic extrusion effects that differ between tools (e.g. a partially
  clogged nozzle that deposits material off-centre) are measured as offset.
- **Directional lighting is the main open risk.** A bead lit from one side has a
  bright flank and a dark flank, so its apparent centre moves. The amount and the
  direction depend on how the filament compares with the plate. For a filament
  lighter than the plate, the centre moves one way. For a darker one, it moves the
  other way. When T0 and Tn are on opposite sides of the plate brightness, the two
  shifts add up. In the simulator this gave errors up to about 0.1 mm, and the phase
  fit and the vernier both agreed with that wrong value. That is why
  `polarity_mismatch` rejects such pairs. With the same polarity, a residual bias
  remains and grows with the lighting directionality (see the `lighting` sweep in
  [benchmark.md](benchmark.md)). How directional the U1 chamber lighting is, is TO
  VERIFY ON HARDWARE. Possible mitigations on the roadmap: a lighting-bias
  calibration cell (the same tool printed on a base of a different colour, so the
  true offset is zero but the brightness ratio changes), or two photos taken under
  different lighting.
- Only XY. Z offsets need a different method.
- Plate warping and a pattern that is not flat would break the plane homography.
