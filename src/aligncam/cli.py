"""Command-line interface: ``aligncam --help``."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import typer

from aligncam.config import AppConfig, load_config
from aligncam.geometry import build_layout

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Camera-based tool-offset measurement for the Snapmaker U1.",
)

ConfigOpt = typer.Option(None, "--config", "-c", help="YAML config file(s); later files win.")


def _config(paths: list[Path] | None) -> AppConfig:
    return load_config(*paths) if paths else AppConfig()


@app.command()
def pattern(
    out: Path = typer.Option(Path("out/pattern"), help="Output stem (.gcode and .svg)."),
    config: list[Path] | None = ConfigOpt,
) -> None:
    """Generate the calibration pattern G-code and an SVG preview."""
    from aligncam.pattern import generate_gcode, layout_svg

    cfg = _config(config)
    layout = build_layout(cfg.pattern)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".gcode").write_text(generate_gcode(layout, cfg.printer), encoding="utf-8")
    out.with_suffix(".svg").write_text(layout_svg(layout, cfg.printer), encoding="utf-8")
    x0, y0, x1, y1 = layout.bbox()
    bx0, by0, bx1, by1 = layout.bed_bbox()
    typer.echo(
        f"pattern {x1 - x0:.1f} x {y1 - y0:.1f} mm, rotated {cfg.pattern.rotation_deg} deg, "
        f"on bed X {bx0:.1f}..{bx1:.1f} Y {by0:.1f}..{by1:.1f} -> {out}.gcode, {out}.svg"
    )


@app.command()
def simulate(
    out: Path = typer.Option(Path("out/sim"), help="Output stem (.png and .json ground truth)."),
    seed: int = 0,
    gsd: float = typer.Option(0.15, help="Ground sampling distance, mm per pixel."),
    tilt: float = typer.Option(20.0, help="Camera tilt from vertical, degrees."),
    roll: float = 0.0,
    blur: float = typer.Option(0.6, help="Gaussian blur sigma in pixels."),
    random_offsets_mm: float = typer.Option(0.0, help="If > 0, draw random tool errors."),
    config: list[Path] | None = ConfigOpt,
) -> None:
    """Render a synthetic photo of the printed pattern with known tool errors."""
    import numpy as np

    from aligncam.sim import Scenario, random_offsets
    from aligncam.sim import simulate as run_sim
    from aligncam.sim.degrade import Degradation

    cfg = _config(config)
    layout = build_layout(cfg.pattern)
    sc = Scenario(
        seed=seed,
        gsd_mm_per_px=gsd,
        tilt_deg=tilt,
        roll_deg=roll,
        degradation=Degradation(blur_sigma_px=blur),
    )
    if random_offsets_mm > 0:
        rng = np.random.default_rng(seed + 1_000_003)
        sc.offsets_mm = random_offsets(rng, list(cfg.pattern.tools), random_offsets_mm)
    img, js = run_sim(sc, layout).save(out)
    typer.echo(f"{img} ({js} has the ground truth)")


@app.command()
def measure(
    image: Path,
    out: Path | None = typer.Option(None, help="Output stem for .json and _overlay.png."),
    truth: Path | None = typer.Option(None, help="Ground-truth JSON from `simulate`."),
    config: list[Path] | None = ConfigOpt,
) -> None:
    """Measure the tool offsets in a photo. Never writes anything to the printer."""
    from aligncam.measure import measure_image
    from aligncam.measure.overlay import draw_overlay

    cfg = _config(config)
    layout = build_layout(cfg.pattern)
    img = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if img is None:
        raise typer.BadParameter(f"cannot read {image}")
    m = measure_image(img, layout, cfg.measure, cfg.camera)
    result = m.to_dict()
    if truth is not None:
        gt = json.loads(truth.read_text(encoding="utf-8"))["offsets_mm"]
        for name, t in result["tools"].items():
            if name in gt and t["dx_mm"] is not None:
                t["error_vs_truth_mm"] = [
                    round(t["dx_mm"] - gt[name][0], 4),
                    round(t["dy_mm"] - gt[name][1], 4),
                ]
    typer.echo(json.dumps(result["tools"], indent=2))
    typer.echo(f"status: {m.status}" + (f"  warnings: {m.warnings}" if m.warnings else ""))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        cv2.imwrite(str(out.parent / f"{out.name}_overlay.png"), draw_overlay(img, layout, m))


@app.command("calibration-board")
def calibration_board(
    out: Path = typer.Option(Path("out/charuco.svg"), help="Output SVG (physical mm size)."),
    config: list[Path] | None = ConfigOpt,
) -> None:
    """Write the printable ChArUco board used by `calibrate-camera`."""
    from aligncam.calib import board_svg

    b = _config(config).calibration.board
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(board_svg(b), encoding="utf-8")
    typer.echo(
        f"{out}: {b.squares_x} x {b.squares_y} squares of {b.square_mm:g} mm, "
        f"markers {b.marker_mm:g} mm ({b.dictionary}). Print at 100 % and glue it flat."
    )


@app.command("screen-target")
def screen_target(
    width_px: int = typer.Option(..., help="Screen width in pixels (native resolution)."),
    height_px: int = typer.Option(..., help="Screen height in pixels."),
    ppi: float = typer.Option(..., help="Screen pixel density (pixels per inch)."),
    out: Path = typer.Option(Path("out/screen"), help="Output folder."),
    count: int = typer.Option(3, help="Number of patterns, each with other random offsets."),
    seed: int = typer.Option(0, help="Seed of the random offsets."),
    mono: bool = typer.Option(False, help="Draw every tool in white (no subpixel colour bias)."),
    config: list[Path] | None = ConfigOpt,
) -> None:
    """Images to show full screen on a tablet or phone and photograph with a real camera.

    Writes pattern_NN.png with known tool offsets plus pattern_NN.json (use it with
    `measure --truth`), and charuco.png for `calibrate-camera`. Results from these
    photos are "real camera, pattern on a screen", not a real print.
    """
    import numpy as np

    from aligncam.screen import board_on_screen, pattern_on_screen
    from aligncam.sim import random_offsets

    cfg = _config(config)
    layout = build_layout(cfg.pattern)
    size = (width_px, height_px)
    rng = np.random.default_rng(seed)
    tools = [t for t in layout.print_order if t != 0]
    out.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        offsets = random_offsets(rng, tools, 0.3)
        shot, shown = pattern_on_screen(layout, offsets, size, ppi, mono=mono)
        stem = out / f"pattern_{i:02d}"
        cv2.imwrite(str(stem.with_suffix(".png")), shot.image)
        truth = {
            "offsets_mm": {f"T{t}": [round(v, 6) for v in shown[t]] for t in sorted(shown)},
            "screen": {"width_px": width_px, "height_px": height_px, "ppi": ppi},
            "px_per_mm": shot.px_per_mm,
            "displayed_scale": shot.scale,
            "mono": mono,
            "note": "real camera, pattern on a screen; not a real print",
        }
        stem.with_suffix(".json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    board = board_on_screen(cfg.calibration.board, size, ppi)
    cv2.imwrite(str(out / "charuco.png"), board.image)
    typer.echo(
        f"{out}: {count} patterns at {shot.scale * 100:.0f} % of actual size "
        f"(1 screen px = {1 / shot.px_per_mm * 1000:.0f} um), charuco.png at "
        f"{board.scale * 100:.0f} %. Show them full screen, without zoom."
    )


@app.command("calibrate-camera")
def calibrate_camera(
    photos: Path = typer.Argument(..., help="Folder with photos of the ChArUco board."),
    out: Path = typer.Option(Path("out/camera.calib.yaml"), help="YAML snippet to write."),
    report: Path | None = typer.Option(None, help="Optional JSON report with every metric."),
    model: str | None = typer.Option(None, help="Distortion model: k1, k1k2, k1k2p, full."),
    config: list[Path] | None = ConfigOpt,
) -> None:
    """Calibrate the camera lens from board photos and write camera_matrix/dist_coeffs.

    Exits with code 1 and the reasons when a quality gate fails; nothing is written then.
    """
    from aligncam.calib import calibrate_folder

    cfg = _config(config).calibration
    if model is not None:
        if model not in ("k1", "k1k2", "k1k2p", "full"):
            raise typer.BadParameter(f"unknown distortion model {model!r}")
        cfg = cfg.model_copy(update={"distortion_model": model})
    try:
        res = calibrate_folder(photos, cfg)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    for name, why in res.skipped_views.items():
        typer.echo(f"skipped {name}: {why}")
    for w in res.warnings:
        typer.echo(f"warning: {w}")
    typer.echo(
        f"{len(res.used_views)} views, RMS {res.rms_px:.3f} px, coverage "
        f"{res.coverage * 100:.0f} %, tilt spread {res.tilt_spread_deg:.0f} deg"
    )
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(res.to_dict(), indent=2), encoding="utf-8")
    if not res.ok:
        for reason, msg in zip(res.reasons, res.messages, strict=True):
            typer.echo(f"rejected ({reason}): {msg}", err=True)
        raise typer.Exit(1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(res.to_yaml(), encoding="utf-8")
    typer.echo(f"status: ok -> {out} (use it as an extra -c file after the camera template)")


@app.command()
def bench(
    out: Path = typer.Option(Path("out/bench"), help="Directory for results.csv."),
    cases: int = typer.Option(60, help="Images per sweep point."),
    workers: int | None = typer.Option(None, help="Worker processes (default: CPUs - 1)."),
    seed: int = 0,
) -> None:
    """Run the Monte Carlo benchmark on synthetic images (takes minutes)."""
    from aligncam.bench.runner import run_benchmark

    path = run_benchmark(out, cases, workers, seed)
    typer.echo(f"results: {path}")


@app.command("bench-report")
def bench_report(
    results: Path = typer.Option(Path("out/bench/results.csv")),
    docs: Path = typer.Option(Path("docs")),
) -> None:
    """Build plots and docs/benchmark.md from benchmark results."""
    from aligncam.bench.report import build_report

    build_report(results, docs, docs / "images")
    typer.echo(f"wrote {docs / 'benchmark.md'} and plots in {docs / 'images'}")


@app.command()
def serve(
    port: int = 8080,
    host: str = typer.Option("127.0.0.1", help="Use 0.0.0.0 to expose on the network."),
    moonraker: str | None = typer.Option(None, help="Moonraker URL, e.g. http://printer:7125"),
    fake: bool = typer.Option(False, help="Use an in-process fake Moonraker (no printer)."),
    config: list[Path] | None = ConfigOpt,
) -> None:
    """Start the web demo. Offsets are only ever written by the dry-run writer."""
    import uvicorn

    from aligncam.printer.moonraker import MoonrakerClient
    from aligncam.web.app import create_app

    cfg = _config(config)
    client = None
    if fake:
        from fastapi.testclient import TestClient

        from aligncam.printer.fake_moonraker import create_app as fake_app

        client = MoonrakerClient(
            "fake://in-process", client=TestClient(fake_app(pattern=cfg.pattern))
        )
    elif moonraker:
        client = MoonrakerClient(moonraker)
    typer.echo(f"AlignCam demo on http://{host}:{port}")
    uvicorn.run(create_app(cfg, client), host=host, port=port, log_level="warning")


@app.command("fake-moonraker")
def fake_moonraker(port: int = 7125, print_seconds: float = 3.0) -> None:
    """Run a fake Moonraker with hidden tool errors, for development without a printer."""
    import uvicorn

    from aligncam.printer.fake_moonraker import FakePrinterState, create_app

    st = FakePrinterState(print_seconds=print_seconds)
    typer.echo(f"fake Moonraker on http://127.0.0.1:{port} (hidden errors: {st.hidden_offsets_mm})")
    uvicorn.run(create_app(st), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    app()
