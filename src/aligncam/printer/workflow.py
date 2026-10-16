"""Calibration workflow: print the pattern, photograph it, measure, propose.

The workflow stops at the proposal. Applying a correction is a separate,
explicit step (see :mod:`aligncam.printer.offsets`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from aligncam.config import AppConfig
from aligncam.geometry import Layout, build_layout
from aligncam.measure import Measurement, measure_image
from aligncam.pattern import generate_gcode
from aligncam.printer.moonraker import MoonrakerClient, MoonrakerError
from aligncam.printer.offsets import OffsetProposal, build_proposal

PATTERN_FILENAME = "aligncam_pattern.gcode"


@dataclass
class WorkflowResult:
    image_bgr: np.ndarray
    measurement: Measurement
    proposal: OffsetProposal
    layout: Layout


def decode_image(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode the image")
    return img


def run_calibration(
    client: MoonrakerClient,
    cfg: AppConfig | None = None,
    print_pattern: bool = True,
    poll_s: float = 2.0,
    timeout_s: float = 3600.0,
    progress: Callable[[str], None] = lambda msg: None,
) -> WorkflowResult:
    cfg = cfg or AppConfig()
    layout = build_layout(cfg.pattern)
    if print_pattern:
        progress("generating G-code")
        gcode = generate_gcode(layout, cfg.printer)
        progress("uploading")
        path = client.upload_gcode(PATTERN_FILENAME, gcode)
        progress("printing")
        client.start_print(path)
        state = client.wait_for_print(timeout_s=timeout_s, poll_s=poll_s)
        if state != "complete":
            raise MoonrakerError(f"print ended in state '{state}'")
    progress("taking snapshot")
    img = decode_image(client.snapshot(cfg.camera.snapshot_url))
    progress("measuring")
    m = measure_image(img, layout, cfg.measure, cfg.camera)
    proposal = build_proposal(m, cfg.printer.offset_correction_sign)
    return WorkflowResult(img, m, proposal, layout)
