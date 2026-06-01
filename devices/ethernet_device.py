"""Ethernet device check — verifies interface presence and device reachability via SSH."""
from __future__ import annotations

import textwrap

from devices.ssh_runner import ssh_exec

_CHECK_SCRIPT = textwrap.dedent("""\
    import json, subprocess, sys

    result = dict(status="unknown", error=None, diagnosis=None)

    r = subprocess.run(["ip", "link", "show", interface],
                       capture_output=True, text=True)
    if r.returncode != 0:
        result.update(status="missing",
                      error=f"Interface {interface!r} not found",
                      diagnosis="USB ethernet adapter not detected — check USB connection")
        print(json.dumps(result)); sys.exit(0)

    if not device_ip:
        result["status"] = "ok"
        print(json.dumps(result)); sys.exit(0)

    r2 = subprocess.run(["ping", "-c", "1", "-W", "2", device_ip],
                        capture_output=True, text=True)
    if r2.returncode != 0:
        result.update(status="comm_error",
                      error=f"Device not reachable at {device_ip}",
                      diagnosis=f"Adapter present but device not responding at {device_ip}")
    else:
        result["status"] = "ok"

    print(json.dumps(result))
""")


class EthernetDevice:
    def __init__(self, ssh_host: str, ssh_user: str) -> None:
        self._host = ssh_host
        self._user = ssh_user

    def check(self, cfg: dict) -> dict:
        interface = cfg["interface"]
        device_ip = cfg.get("device_ip", "")
        try:
            result = ssh_exec(
                self._host, self._user,
                f"interface = {interface!r}\ndevice_ip = {device_ip!r}\n" + _CHECK_SCRIPT,
                timeout=15,
            )
        except Exception as exc:
            result = dict(status="unknown", error=str(exc),
                          diagnosis="Script failed — see error for details")
        result["name"] = cfg["name"]
        result["identifier"] = interface
        result.setdefault("path", device_ip or None)
        return result
