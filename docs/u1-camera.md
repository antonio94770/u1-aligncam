# The U1 chamber camera: what is public, and what it implies

Desk research and simulation only (October 2026). Nothing here was measured on a real U1. Every
figure that is not quoted from a source below is an **estimate from a pinhole model**
and is labelled as such. The settings that follow from it remain
`TO VERIFY ON HARDWARE`.

## What public sources say

| Fact | Value | Source |
|---|---|---|
| Resolution | 2 MP, 1080p (1920 x 1080) | Snapmaker U1 FAQ [1]; Snapmaker blog [2]; Extended Firmware docs [3] |
| Interface | internal MIPI CSI camera | Extended Firmware docs [3]; community bridge [5] |
| Mounting | "located at the right inner panel of the machine" (fixed to the chassis, not the toolhead) | Snapmaker Wiki, camera replacement guide [4]; blog [2] |
| Lens | described only as "wide-angle"; no FOV or focal length published | blog [2] |
| Frame rate, stock firmware | around 1 Hz (FAQ); ~1 fps cloud, ~2 fps LAN (user reports) | [1]; forum [6] |
| Frame rate, Extended Firmware | 5, 15, 25 (default) or 30 fps, always 1080p ("required for AI detection") | [3] |
| Snapshot URL, Extended Firmware | `/webcam/snapshot.jpg` (Moonraker `[webcam case]`, aspect 16:9) | [3] |
| Snapshot URL, stock firmware + community bridge | `/webcam/?action=snapshot` | [5] |
| Camera controls | V4L2 controls exposed and persisted by the Extended Firmware; which ones exist depends on the sensor | [3], [7] |
| Lighting | chamber LED can be toggled from Snapmaker Orca; no brightness or position data | [2] |
| Replacement part | camera kit with a 110 mm FPC cable, 7 g | Snapmaker store [8] |

Not found in any public source: sensor model, field of view, focal length, focus
(fixed or not), the camera's height and angle, and how directional the chamber
light is.

## What the mounting position implies

A camera on the right side panel looks **across** the bed, not down at it. For the
measurement this matters in two ways (see [benchmark.md](benchmark.md)):

- Ground sampling distance grows with distance and with the viewing angle (the
  pattern is foreshortened along the line of sight).
- Above ~45 deg from the vertical the benchmark rejects most images, and above
  ~60 deg it rejects all of them.

`tools/u1_camera_gsd.py` sweeps the unknowns: camera height above the bed (50-200 mm),
clearance between the camera and the right edge of the bed (20-50 mm), horizontal
FOV (80-120 deg), with the camera aimed at the bed centre. Ideal pinhole, no
distortion, so the numbers are optimistic. Coarser of the X/Y GSD, in mm/px, and
viewing angle from the vertical:

| Pattern centre | Best case | Typical (h 100-150, FOV 100) | Worst case |
|---|---|---|---|
| Bed centre (135 mm from the right edge) | 0.27 mm/px, 46 deg | 0.38-0.55 mm/px, 46-62 deg | 1.3 mm/px, 75 deg |
| 70 mm from the right edge | 0.15 mm/px, 42 deg | 0.21-0.29 mm/px, 31-50 deg | 0.60 mm/px, 67 deg |
| 30 mm from the right edge | 0.10 mm/px, 45 deg | 0.12-0.20 mm/px, 18-39 deg | 0.36 mm/px, 22 deg |

*Pinhole estimate, not a measurement.*

### Rendered with the measurement in the loop

The `u1` and `u1_cal` cases of `aligncam bench` render these poses (random height
50-200 mm, clearance 20-50 mm, position along the panel, HFOV 80-120 deg, aimed at
the bed centre; both cases share the same seeds and poses) and run the full
measurement. Synthetic results, 360 tool-axis measurements per row; full tables in [benchmark.md](benchmark.md):

| Placement | Accepted, no lens calibration | Accepted, calibrated lens | ... camera >= 100 mm high | P95 error | Pattern fully in frame |
|---|---:|---:|---:|---:|---:|
| Bed centre, 0 deg | 8 % | 7 % | 9-17 % | 11-13 um | 100 % |
| Bed centre, 90 deg | 17 % | 19 % | 20-23 % | 18-22 um | 100 % |
| **70 mm from the camera edge, 90 deg** | 17 % | **61 %** | **62-86 %** | 17-18 um | 63-75 % |
| 50 mm from the camera edge, 90 deg | 9 % | 33 % | 20-53 % | 14-27 um | 22-33 % |

Across all of them, accepted measurements with an error above 40 um: 0-0.3 %.
Rejections are dominated by `low_snr` (coarse GSD, blur) at the bed centre and by
`no_fiducials` (pattern partly outside the frame) near the edge.

Conclusions, all of them to be confirmed on a real printer:

1. **At the bed centre the U1 camera is very likely too coarse** (> 0.25 mm/px in
   every scenario tried, too oblique in most, at most 20 % accepted in the
   rendered benchmark). The default `center_on_bed_mm: [135, 135]` is wrong for
   the U1.
2. **Recommended starting point: rotated 90 deg, 70 mm from the camera edge**
   ([config/placements/u1-side.yaml](../config/placements/u1-side.yaml)). The
   pattern runs parallel to the panel, so its far end stays close to the camera.
   Closer (50 mm) is finer but often leaves the frame.
3. **Lens calibration is required on the U1.** Away from the bed centre the
   pattern sits near the edge of a wide-angle image, where uncorrected distortion
   is largest: 17 % accepted without calibration, 61 % with a perfect one on the
   same scenes.
   `aligncam calibrate-camera` (ChArUco board, [method.md](method.md#lens-calibration-aligncam-calibrate-camera))
   recovers the lens from photos; on the same scenes as the uncorrected case, a
   calibration from 15 synthetic board photos raises acceptance to 55 %
   ([benchmark.md](benchmark.md), synthetic).
4. **The real aim and FOV decide whether the pattern is in the frame.** Every
   simulated camera is aimed at the bed centre; a quarter to more than a
   third of the poses do not see the whole pattern at 70 mm. The placement must be tuned on
   a real snapshot.
5. **Focus is an open risk.** A fixed-focus wide-angle module set for the whole
   chamber may be soft at 50-100 mm. The benchmark tolerates blur up to about
   2 px sigma.
6. **The toolhead must be parked away** from the line of sight before the photo.
7. If the bed moves in Z (TO VERIFY ON HARDWARE), the bed height at photo time is
   a free parameter that changes the viewing angle and the distance.
8. If all of this fails, the plan-B nozzle camera
   ([plan-b-nozzle-camera.md](plan-b-nozzle-camera.md)) does not depend on the
   chamber camera at all.

## Sources

1. Snapmaker forum, "U1 FAQ (official info summary)":
   https://forum.snapmaker.com/t/u1-faq-official-info-summary/39648
2. Snapmaker blog, "3D Printer Cameras: AI, Uptime, and Remote Monitoring":
   https://www.snapmaker.com/blog/3d-printer-camera-guide/
3. SnapmakerU1 Extended Firmware (paxx12), Camera Support:
   https://snapmakeru1-extended-firmware.pages.dev/camera_support.html
4. Snapmaker Wiki, "U1 camera kit with FPC cable replacement guide":
   https://wiki.snapmaker.com/en/snapmaker_u1/troubleshooting/camera_replacement_guide
5. Snapmaker forum, "Snapmaker U1 Stock Camera in Fluidd - No Custom Firmware Required":
   https://forum.snapmaker.com/t/snapmaker-u1-stock-camera-in-fluidd-no-custom-firmware-required/43275
6. Snapmaker forum, "U1 Camera Framerate Really Low":
   https://forum.snapmaker.com/t/u1-camera-framerate-really-low/40774
7. paxx12/v4l2-mpp: https://github.com/paxx12/v4l2-mpp
8. Snapmaker store, camera kit with FPC cable for U1:
   https://us.snapmaker.com/products/camera-kit-with-fpc-cable-for-snapmaker-u1
