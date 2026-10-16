import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from aligncam.printer.fake_moonraker import FakePrinterState, create_app
from aligncam.printer.moonraker import MoonrakerClient, MoonrakerError
from aligncam.printer.offsets import (
    Confirmation,
    ConfirmationError,
    DryRunWriter,
    build_proposal,
)
from aligncam.printer.workflow import run_calibration


@pytest.fixture
def fake():
    state = FakePrinterState(print_seconds=0.05)
    client = MoonrakerClient("http://fake", client=TestClient(create_app(state)))
    return state, client


def test_client_basic_endpoints(fake):
    _, client = fake
    assert client.server_info()["klippy_state"] == "ready"
    assert client.print_state()["state"] == "standby"
    assert client.webcams()[0].snapshot_url.startswith("/webcam")
    assert len(client.snapshot()) > 1000


def test_start_unknown_file_fails(fake):
    _, client = fake
    with pytest.raises(MoonrakerError):
        client.start_print("missing.gcode")


def test_workflow_recovers_hidden_offsets(fake):
    state, client = fake
    r = run_calibration(client, poll_s=0.02)
    assert r.measurement.status == "ok"
    for t, (dx, dy) in state.hidden_offsets_mm.items():
        to = r.measurement.tools[t]
        assert abs(to.dx_mm - dx) < 0.03 and abs(to.dy_mm - dy) < 0.03
    assert "aligncam_pattern.gcode" in state.files
    # The workflow must never send any G-code command by itself.
    assert state.scripts == []


def test_snapshot_before_print_is_rejected(fake):
    _, client = fake
    r = run_calibration(client, print_pattern=False)
    assert r.measurement.status == "rejected"
    assert r.proposal.corrections == ()


def test_proposal_signs_and_confirmation(fake):
    _, client = fake
    r = run_calibration(client, poll_s=0.02)
    p = r.proposal
    for c in p.corrections:
        assert c.delta_x_mm == pytest.approx(-c.measured_dx_mm)
    writer = DryRunWriter()
    with pytest.raises(ConfirmationError):
        writer.apply(p, Confirmation("not-the-digest", "test"))
    res = writer.apply(p, Confirmation(p.digest(), "test", tools=(1,)))
    assert res.dry_run and not res.applied
    assert res.lines[0].startswith("T1:") and not any(ln.startswith("T2:") for ln in res.lines)


def test_empty_tool_selection_applies_nothing(fake):
    _, client = fake
    p = run_calibration(client, poll_s=0.02).proposal
    res = DryRunWriter().apply(p, Confirmation(p.digest(), "test", tools=()))
    assert res.lines[0].startswith("No significant correction")


def test_large_correction_refused(layout):
    from aligncam.measure.estimate import Measurement, ToolOffset

    m = Measurement(
        "ok",
        [],
        [],
        None,
        [],
        {1: ToolOffset(1, "ok", [], 0.45, 0.0, 0.005, 0.005)},
    )
    p = build_proposal(m, max_correction_mm=0.3)
    assert p.corrections == () and 1 in p.skipped
