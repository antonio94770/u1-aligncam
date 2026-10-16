# Plan B: upward-looking nozzle camera (design only, not implemented)

If the chamber camera turns out too coarse, too blurry or too badly lit for the
printed pattern (see [benchmark.md](benchmark.md) for the limits on synthetic
data), a second measurement mode can reuse most of this code base with a
different sensor: a USB microscope on the bed, looking **up** at the nozzles.
This is the approach of [kTAMV](https://github.com/TypQxQ/kTAMV) on Klipper, and of
TAMV (Jubilee) and duet-tool-align on other firmware.

## Principle

1. A cheap USB microscope (typically 640x480 to 1920x1080, a few micrometres per
   pixel at focus) is fixed on the bed with its optical axis vertical.
2. The printer moves each tool over the camera. The software finds the nozzle tip
   (a bright or dark circle) and reads its centre in pixels.
3. The pixel-to-mm scale and the rotation are calibrated by moving one tool by
   known XY steps (for example a small grid of +/-0.5 mm moves) and fitting an
   affine transform.
4. For each tool Tn, the XY move that brings the nozzle centre to the same pixel as
   T0 is the tool offset.

## Trade-offs against the printed pattern

| | Printed pattern (plan A) | Nozzle camera (plan B) |
|---|---|---|
| Extra hardware | none, uses the chamber camera | USB microscope + mount |
| What it measures | where the material lands | where the nozzle tip is |
| Precision limit | camera GSD, lighting, print quality | microscope optics, nozzle cleanliness |
| Sensitive to extrusion/ooze/lighting colour bias | yes | no |
| Sensitive to dirty nozzles, reflections | no | yes |
| Uses filament, takes a print | yes (one layer) | no |

The two modes are complementary. Plan B measures the geometry of the machine,
plan A measures the result on the part. A large difference between them would point
to an extrusion problem rather than to an offset problem.

## How it would fit into this repository

- `aligncam/nozzle/` (new): nozzle detection (Hough circles + sub-pixel
  edge fit, rejection when more than one plausible circle or low contrast), the
  affine pixel-to-mm calibration and the per-tool offset estimation with
  uncertainty from repeated captures.
- `aligncam/printer/`: the same Moonraker client, plus motion commands to move
  the tool over the camera. Every motion uses only `G0`/`G1` at a safe height,
  and the camera position is a configured value (TO VERIFY ON HARDWARE).
- The offset is written only through the same `OffsetWriter` interface, after
  user confirmation.
- The simulator can be extended with a synthetic nozzle image (ring with
  specular highlight, debris, defocus) to test detection the same way plan A is
  tested.

## Risks to check on hardware

- Room under the gantry for a microscope at the right focus distance.
- Whether the U1 tool-change and parking positions allow moving every tool over a
  single fixed point.
- Lighting of the nozzle tip (ring light on the microscope).
