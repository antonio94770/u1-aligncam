# The U1 chamber camera: what is public, and what it implies

Desk research only (October 2026). Nothing here was measured on a real U1. Every
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

Conclusions, all of them to be confirmed on a real printer:

1. **At the bed centre the U1 camera is very likely too coarse** (> 0.25 mm/px in
   every scenario tried, and too oblique in most). The current default
   `center_on_bed_mm: [135, 135]` is probably wrong for the U1.
2. **Near the camera side the target is plausible.** A pattern 30-70 mm from the
   right edge reaches 0.1-0.3 mm/px in most scenarios, i.e. inside or close to the
   0.15-0.25 mm/px that the benchmark accepts.
3. **The pattern should run parallel to the camera panel.** The current layout is
   about 160 x 75 mm with its long side along X, which points it away from a
   camera on the right. Rotated by 90 deg, the far end of the pattern stays close
   to the camera, and its centre can sit about 40-70 mm from the right edge.
   The layout cannot be rotated yet.
4. **Focus is an open risk.** A fixed-focus wide-angle module set for the whole
   chamber may be soft at 50-100 mm. The benchmark tolerates blur up to about
   2 px sigma.
5. **The toolhead must be parked away** from the line of sight before the photo.
6. If the bed moves in Z (TO VERIFY ON HARDWARE), the bed height at photo time is
   a free parameter that changes the viewing angle and the distance.
7. If all of this fails, the plan-B nozzle camera
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
