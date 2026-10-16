# U1 AlignCam

**Camera-based measurement of the XY offsets between the four tool heads of the
Snapmaker U1.** Every tool prints a small vernier/grating pattern. The chamber
camera photographs it, and computer vision estimates where each tool actually
printed relative to T0. The tool then *proposes* a correction. It never writes one
by itself.

> **Status: pre-hardware prototype.** All results in this repository come from
> **synthetic images** with known ground truth. Nothing has been tested on a real
> U1 yet. See [docs/hardware-todo.md](docs/hardware-todo.md).

![Synthetic photo with detection overlay](docs/images/overlay_example.jpg)
*Synthetic image (simulated camera, 0.15 mm/px, 20° tilt) with the detection overlay:
fiducials (cyan circles), detected line centres, per-cell offsets.*

## The problem

Multi-colour prints on a toolchanger need every tool to print in exactly the same
place. Snapmaker specifies a tool-to-tool alignment better than 0.04 mm, obtained
with a calibration sensor. Colour misalignment is still common enough to have its
own troubleshooting page in the Snapmaker wiki. AlignCam aims to give users an
independent check of the result **on the actual print**, using only the camera
that is already in the chamber.

## Approach (short)

1. **Pattern.** For each tool and axis, T0 and Tn print two gratings side by side
   (1.20 / 1.14 mm pitch, a vernier). Four T0 disks act as fiducials. A T0-vs-T0
   control cell measures the bias of the method itself.
2. **Measurement.** Fiducials → homography → rectified gratings → colour-agnostic
   line signal → least-squares **phase fit** (sub-pixel), checked against an
   independent **line-by-line vernier reading**.
3. **Safety.** Every result carries an uncertainty. A measurement that fails a
   quality gate is **rejected** with a reason code instead of returning a number.

Details and the reasons behind the design choices: [docs/method.md](docs/method.md).

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; source .venv/bin/activate elsewhere
pip install -e ".[dev]"

aligncam pattern --out out/pattern                       # G-code + SVG preview
aligncam simulate --out out/sim --random-offsets-mm 0.2  # synthetic photo + ground truth
aligncam measure out/sim.png --truth out/sim.json --out out/meas
```

Hardware-dependent settings live in [`config/`](config). Every unknown is marked
`TO VERIFY ON HARDWARE`.

## Repository layout

```
config/          YAML configs (pattern, printer template, camera template)
src/aligncam/    geometry, pattern (G-code), sim (synthetic camera), measure, cli
tests/           pytest suite, run in CI on Linux and Windows
docs/            method, hardware TODO list
```

## Roadmap

- [x] M1: vernier pattern, G-code generator with configurable macro templates, image simulator
- [x] M2: measurement pipeline with uncertainty and rejection, tests
- [ ] M3: Monte Carlo benchmark (precision vs. resolution, blur, noise, contrast), plots
- [ ] M4: Moonraker client + fake Moonraker, dry-run offset writer, web demo
- [ ] M5: polish, demo GIF, plan B (upward-looking nozzle camera, kTAMV-style) design

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
