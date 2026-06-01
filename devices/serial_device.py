"""Simple USB serial device presence check — Layer 1 only, no protocol needed."""
from __future__ import annotations

import textwrap

from devices.ssh_runner import ssh_exec

_CHECK_BODY = textwrap.dedent("""\
    import json, sys
    from pathlib import Path

    serial_by_id = Path("/dev/serial/by-id")
    result = dict(layer=0, status="unknown", error=None, diagnosis=None, path=None)

    if not serial_by_id.exists():
        result.update(layer=1, status="missing",
                      error="/dev/serial/by-id not found",
                      diagnosis="Hardware: no USB serial devices on system")
        print(json.dumps(result)); sys.exit(0)

    matches = [str(p) for p in serial_by_id.iterdir() if identifier in p.name]
    if not matches:
        result.update(layer=1, status="missing",
                      error=f"{identifier!r} not found in /dev/serial/by-id",
                      diagnosis="Hardware: device not detected — check USB cable and power")
        print(json.dumps(result)); sys.exit(0)

    result.update(layer=1, status="ok", path=matches[0])
    print(json.dumps(result))
""")


class SerialDevice:
    def __init__(self, identifier: str, ssh_host: str, ssh_user: str) -> None:
        self._identifier = identifier
        self._host = ssh_host
        self._user = ssh_user

    def check(self, cfg: dict) -> dict:
        try:
            result = ssh_exec(
                self._host, self._user,
                f"identifier = {self._identifier!r}\n" + _CHECK_BODY,
            )
        except Exception as exc:
            result = dict(layer=0, status="unknown", path=None,
                          error=str(exc), diagnosis="Script failed — see error for details")
        result["name"] = cfg["name"]
        result["identifier"] = self._identifier
        return result
