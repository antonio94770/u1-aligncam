"""Minimal Moonraker HTTP client.

Only standard, documented Moonraker endpoints are used (file upload, print
start, object query, webcam list and snapshot). Nothing here is specific to the
Snapmaker U1; anything U1-specific stays in the config templates.

The client deliberately has **no** method to change tool offsets. Offsets are
written only through :mod:`aligncam.printer.offsets`, after user confirmation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

TERMINAL_STATES = {"complete", "error", "cancelled"}


class MoonrakerError(RuntimeError):
    pass


@dataclass
class Webcam:
    name: str
    snapshot_url: str


class MoonrakerClient:
    def __init__(
        self,
        base_url: str = "http://localhost:7125",
        client: httpx.Client | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = client or httpx.Client(base_url=self.base_url, timeout=timeout_s)

    # -- helpers ---------------------------------------------------------------

    def _get(self, path: str, **params) -> dict:
        r = self._http.get(path, params=params or None)
        return self._result(r)

    def _post(self, path: str, **kwargs) -> dict:
        r = self._http.post(path, **kwargs)
        return self._result(r)

    @staticmethod
    def _result(r: httpx.Response) -> dict:
        if r.status_code >= 400:
            raise MoonrakerError(
                f"{r.request.method} {r.request.url.path}: HTTP {r.status_code} {r.text[:200]}"
            )
        data = r.json()
        return data.get("result", data)

    # -- read-only -------------------------------------------------------------

    def server_info(self) -> dict:
        return self._get("/server/info")

    def print_state(self) -> dict:
        res = self._get("/printer/objects/query", print_stats="state,filename,message")
        return res["status"]["print_stats"]

    def webcams(self) -> list[Webcam]:
        res = self._get("/server/webcams/list")
        return [Webcam(w.get("name", ""), w.get("snapshot_url", "")) for w in res["webcams"]]

    def snapshot(self, snapshot_url: str | None = None) -> bytes:
        """JPEG/PNG bytes from the webcam. The URL may be absolute or relative to the host."""
        if snapshot_url is None:
            cams = self.webcams()
            if not cams:
                raise MoonrakerError("no webcam configured in Moonraker")
            snapshot_url = cams[0].snapshot_url
        r = self._http.get(snapshot_url)
        if r.status_code >= 400 or not r.content:
            raise MoonrakerError(f"snapshot failed: HTTP {r.status_code}")
        return r.content

    # -- printing --------------------------------------------------------------

    def upload_gcode(self, filename: str, gcode: str) -> str:
        files = {"file": (filename, gcode.encode("utf-8"), "text/plain")}
        res = self._post("/server/files/upload", files=files, data={"root": "gcodes"})
        item = res.get("item", {})
        return item.get("path", filename)

    def start_print(self, filename: str) -> None:
        self._post("/printer/print/start", params={"filename": filename})

    def wait_for_print(self, timeout_s: float = 3600.0, poll_s: float = 2.0) -> str:
        """Block until the print reaches a terminal state; return that state."""
        deadline = time.monotonic() + timeout_s
        while True:
            state = self.print_state().get("state", "")
            if state in TERMINAL_STATES:
                return state
            if time.monotonic() > deadline:
                raise MoonrakerError(f"print did not finish within {timeout_s:.0f} s ({state})")
            time.sleep(poll_s)
