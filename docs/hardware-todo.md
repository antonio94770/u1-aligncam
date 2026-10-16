# Hardware TODO: what must be verified on a real Snapmaker U1

Nothing in this repository has been tested on a real U1 yet. This list tracks every
assumption marked `TO VERIFY ON HARDWARE`.

## Printer

| Item | Where | Current placeholder |
|---|---|---|
| Bed size and usable area | `config/printer.u1.template.yaml` `bed_size_mm` | 270 x 270 mm |
| Number of tools | `tool_count` | 4 |
| Nozzle diameter | `nozzle_diameter_mm` | 0.4 mm |
| Start sequence (homing, mesh, purge) | `gcode_start` | `M140`/`G28`/`M190` |
| Tool-change macro and arguments | `gcode_tool_change` | `T{n}` + `M109` |
| Whether the tool-change macro primes/wipes | `gcode_tool_change`, `prime_lines` | prime line per tool |
| End sequence | `gcode_end` | lift, heaters off |
| **Where tool offsets are stored and how they are applied** | `printer/offsets.py` (abstract) | not implemented, by design |
| Sign convention of the offset correction | `offset_correction_sign` | -1 |
| Pattern position on the bed (camera field of view) | `config/pattern.default.yaml` `center_on_bed_mm` | bed centre; probably near the right edge on the U1, see [u1-camera.md](u1-camera.md) |

## Camera

| Item | Where | Current placeholder |
|---|---|---|
| Snapshot URL in Moonraker | `config/camera.u1.template.yaml` `snapshot_url` | `/webcam/?action=snapshot` |
| Resolution | `resolution_px` | 1920 x 1080 |
| Pose: distance, tilt, field of view | used only to interpret the benchmark | right inner side panel (public, [u1-camera.md](u1-camera.md)); height, angle and FOV unknown |
| GSD and viewing angle where the pattern is printed | `tools/u1_camera_gsd.py` (pinhole estimate) | likely > 0.25 mm/px at bed centre |
| Focus at 50-100 mm (fixed-focus wide-angle?) | benchmark `blur` sweep | unknown |
| Where to park the toolhead for the photo | workflow | unknown |
| Lens distortion (calibration) | `camera_matrix`, `dist_coeffs` | none |
| Camera distance to the pattern | `Scenario.camera_distance_mm` (simulator only) | 250 mm |
| Exposure/lighting control (chamber LEDs) | not handled yet | - |
| **How directional the chamber lighting is** | benchmark `lighting` sweep | 0-0.3 shading strength |

## Print

| Item | Notes |
|---|---|
| First-layer quality of 0.45 mm lines at 1.14-1.2 mm pitch | lines must not merge |
| Contrast of common filaments on the U1 plate | plate colour/texture unknown |
| Bias of the control cell on real prints | gives the real systematic floor |
| Colour-dependent lighting bias | compare a T0-only print photographed under the chamber lights against the expected zero; check `polarity_mismatch` advice |
