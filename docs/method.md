# Method

## The pattern

Every tool prints, on a single layer, a set of **cells**. A cell measures one axis
of one tool and contains two gratings that sit side by side without overlapping,
like the two scales of a caliper:

- the **reference grating** printed by T0 (pitch `p0`, default 1.20 mm);
- the **measured grating** printed by Tn (pitch `pn = p0 * vernier_ratio`,
  default 1.14 mm).

The central line (index k = 0) of both gratings is commanded at the cell centre.
X cells use vertical lines (they measure X), Y cells use horizontal lines. There is
one X and one Y cell per tool, plus a **control cell** where T0 prints both halves.
Four corner disks and an orientation dot, all printed by T0, define the pattern
frame.

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
4. **Coarse position.** Centroid of the positive signal (envelope of the grating).
   It removes the one-pitch ambiguity of the phase.
5. **Phase fit.** Least squares on the profile averaged along the lines: quadratic
   trend (illumination) plus 4 harmonics of the known pitch. The phase of the
   fundamental gives the grating shift, unwrapped with the coarse position.
6. **Offset.** `offset = shift(Tn grating) - shift(T0 grating)`. Both gratings are
   a few millimetres apart and see the same homography and lens errors. Those
   errors largely cancel.
7. **Vernier cross-check.** The line centres are detected one by one;
   `d_k = u_n(k) - u_0(k) = offset + k (pn - p0)` gives an independent estimate and
   the coincidence index a human would read.
8. **Uncertainty.** Quadrature sum of: the scatter between 6 bands along the lines
   (or the least-squares covariance, whichever is larger), the scatter of
   individual line centres (per-line placement errors, which bands cannot see),
   and a systematic floor (`sigma_floor_mm`).

## Quality gates (reject instead of guessing)

| Reason | Trigger |
|---|---|
| `no_fiducials` | fiducial configuration not found |
| `fiducial_reprojection` | homography does not fit the 5 fiducials |
| `outside_image` | a cell is not fully visible |
| `low_contrast` | filament and bed colours too close |
| `low_snr` | periodic signal too weak |
| `high_uncertainty` | sigma above `max_sigma_mm` (default 0.02 mm) |
| `phase_vernier_mismatch` | the two estimators disagree by more than 0.03 mm |
| `out_of_range` | offset larger than `max_offset_mm` (unambiguous range) |
| `control_bias` | the T0-vs-T0 control cell does not read zero: all tools rejected |

## Known limitations

- The method measures **where the material ended up**, not where the nozzle was.
  Systematic extrusion effects that differ between tools (e.g. a partially
  clogged nozzle that deposits material off-centre) are measured as offset.
- Directional lighting shifts the apparent centre of a raised bead. The shift
  differs between filament colours, so it does not fully cancel. The simulator
  models this effect, and it is one reason for the systematic floor.
- Only XY. Z offsets need a different method.
- Plate warping and a pattern that is not flat would break the plane homography.
