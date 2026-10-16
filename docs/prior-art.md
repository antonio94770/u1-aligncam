# Prior art

| Project | Sensor | What is measured | Notes |
|---|---|---|---|
| [kTAMV](https://github.com/TypQxQ/kTAMV) | upward-looking USB camera on the bed | nozzle tip position per tool | Klipper; the closest relative of plan B ([plan-b-nozzle-camera.md](plan-b-nozzle-camera.md)) |
| TAMV (Jubilee project) | upward-looking USB camera | nozzle tip position | the original "Tool Alignment Machine Vision" for the Jubilee toolchanger |
| duet-tool-align | upward-looking camera | nozzle tip position | the same principle on Duet/RepRapFirmware |
| Bambu Lab H2D "high-precision nozzle offset calibration" | on-board vision | a printed test pattern | public information only says that a test model is printed and read by vision; no technical documentation is available, so nothing here is based on it |
| Snapmaker U1 built-in calibration | calibration sensor | tool offsets | the manufacturer specifies an alignment better than 0.04 mm |

## What AlignCam adds

- It uses **only the camera already in the chamber**: no extra hardware.
- It measures the **printed result**, which is what the user sees. This also
  catches errors that a nozzle-tip measurement cannot see.
- It is designed and validated **before hardware access**, with an open
  simulator and a benchmark that reports precision and, above all, the rate of
  wrong-but-accepted results.
- It **never writes offsets automatically**. Every correction is shown to the
  user, who must confirm it.
