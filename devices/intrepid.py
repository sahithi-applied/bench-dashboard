"""Intrepid NeoVI Fire 3 health check via SSH — verifies SocketCAN interfaces are enumerated."""
from __future__ import annotations

import textwrap

from devices.ssh_runner import ssh_exec

_CHECK_SCRIPT = textwrap.dedent("""\
    import json, subprocess, re

    result = dict(status="unknown", error=None, diagnosis=None, interfaces=[])

    r = subprocess.run(["ip", "link", "show"], capture_output=True, text=True)
    if r.returncode != 0:
        result.update(status="comm_error", error=r.stderr.strip(),
                      diagnosis="Failed to run ip link show on bench machine")
        print(json.dumps(result))
        import sys; sys.exit(0)

    pattern = re.compile(rf"[\\w]{{1,15}}_{serial_number}\\b", re.IGNORECASE)
    found = pattern.findall(r.stdout)
    interfaces = list(dict.fromkeys(found))

    if not interfaces:
        result.update(status="missing", error=f"No SocketCAN interfaces found for serial {serial_number!r}",
                      diagnosis="icsscand has not enumerated device — check Ethernet connection and icsscand service")
    else:
        up = [i for i in interfaces if f"{i}:" in r.stdout and "UP" in r.stdout[r.stdout.find(f"{i}:"):r.stdout.find(f"{i}:")+100]]
        result.update(status="ok", interfaces=interfaces)

    print(json.dumps(result))
""")


class IntrepidDevice:
    def __init__(self, serial_number: str, ssh_host: str, ssh_user: str) -> None:
        self._serial = serial_number.upper()
        self._host = ssh_host
        self._user = ssh_user

    def check(self, cfg: dict) -> dict:
        base = dict(
            name=cfg.get("name", f"Intrepid {self._serial}"),
            serial_number=self._serial,
            status="unknown", error=None, diagnosis=None, interfaces=[],
        )
        try:
            result = ssh_exec(
                self._host, self._user,
                f"serial_number = {self._serial!r}\n" + _CHECK_SCRIPT,
            )
            base.update(result)
        except Exception as exc:
            base.update(status="unknown", error=str(exc),
                        diagnosis="SSH failed — check connectivity to bench machine")
        return base
