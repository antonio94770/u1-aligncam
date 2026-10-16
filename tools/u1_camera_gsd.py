"""Estimate the ground sampling distance (GSD) the U1 chamber camera could reach.

    python tools/u1_camera_gsd.py

Known (see docs/u1-camera.md for sources): 1920 x 1080 sensor, mounted on the right
inner side panel. Unknown and swept here: the camera height above the bed surface, its
horizontal clearance from the right edge of the bed, its horizontal field of view and
where it points. Ideal pinhole, no lens distortion: a real wide-angle lens makes the
image edges worse than these numbers, so treat them as optimistic.

For each bed point the local GSD is computed from the projection Jacobian along bed X
and bed Y, and the coarser of the two is reported, together with the viewing angle from
the vertical (the benchmark rejects most images beyond ~45 deg). The rendered version of these
poses, with the measurement in the loop, is the "u1" case of ``aligncam bench``.
"""

from __future__ import annotations

import itertools
import math

import numpy as np

from aligncam.sim.camera import look_at
from aligncam.sim.u1 import BED_MM

WIDTH_PX, HEIGHT_PX = 1920, 1080


def local_gsd(cam_pos, aim, hfov_deg, point_xy, step_mm=0.5):
    """Coarser of the X/Y GSD (mm/px) at a bed point, its view angle, and visibility."""
    R = look_at(cam_pos, aim, np.array([0.0, 0.0, 1.0]))
    f = (WIDTH_PX / 2) / math.tan(math.radians(hfov_deg) / 2)

    def project(p):
        c = R @ (np.array([p[0], p[1], 0.0]) - cam_pos)
        return np.array([f * c[0] / c[2] + WIDTH_PX / 2, f * c[1] / c[2] + HEIGHT_PX / 2]), c[2]

    uv, depth = project(point_xy)
    visible = depth > 0 and 0 <= uv[0] < WIDTH_PX and 0 <= uv[1] < HEIGHT_PX
    gsd = []
    for d in ((step_mm, 0.0), (0.0, step_mm)):
        uv2, _ = project((point_xy[0] + d[0], point_xy[1] + d[1]))
        gsd.append(step_mm / np.linalg.norm(uv2 - uv))
    ray = np.array([point_xy[0], point_xy[1], 0.0]) - cam_pos
    view_deg = math.degrees(math.acos(-ray[2] / np.linalg.norm(ray)))
    return max(gsd), view_deg, visible


def main() -> None:
    heights = (50.0, 100.0, 150.0, 200.0)  # camera above the bed surface, mm
    clearances = (20.0, 50.0)  # camera to the right edge of the bed, mm
    hfovs = (80.0, 100.0, 120.0)
    # Pattern centre: bed centre, and 70 / 30 mm from the right edge (camera side).
    pattern_x = (BED_MM / 2, BED_MM - 70.0, BED_MM - 30.0)
    y_mid = BED_MM / 2

    print("Camera aimed at the bed centre (whole-bed framing, e.g. for timelapse / AI):")
    print("h_mm clear_mm hfov | " + " | ".join(f"x={x:.0f}: gsd view" for x in pattern_x))
    for h, c, hfov in itertools.product(heights, clearances, hfovs):
        cam = np.array([BED_MM + c, y_mid, h])
        cells = []
        for x in pattern_x:
            g, v, vis = local_gsd(cam, np.array([BED_MM / 2, y_mid, 0.0]), hfov, (x, y_mid))
            cells.append(f"{g:5.2f} {v:3.0f}{'' if vis else '*'}")
        print(f"{h:4.0f} {c:8.0f} {hfov:4.0f} | " + " | ".join(cells))
    print("(* = outside the frame)")


if __name__ == "__main__":
    main()
