"""LabJack T7 USB presence check — detects by USB vendor/product ID in sysfs."""
from __future__ import annotations

import textwrap

from devices.ssh_runner import ssh_exec

_VENDOR = "0cd5"
_PRODUCT_T7 = "0007"

_CHECK_SCRIPT = textwrap.dedent("""\
    import json
    from pathlib import Path

    result = dict(status="unknown", error=None, diagnosis=None)

    found = False
    for dev in Path("/sys/bus/usb/devices").iterdir():
        try:
            v = (dev / "idVendor").read_text().strip()
            p = (dev / "idProduct").read_text().strip()
            if v == vendor_id and p == product_id:
                found = True
                break
        except OSError:
            pass

    if found:
        result["status"] = "ok"
    else:
        result.update(status="missing",
                      error="LabJack T7 not found in USB device tree",
                      diagnosis="LabJack T7 not detected — check USB cable and power")

    print(json.dumps(result))
""")


class LabJackDevice:
    def __init__(self, ssh_host: str, ssh_user: str) -> None:
        self._host = ssh_host
        self._user = ssh_user

    def check(self, cfg: dict) -> dict:
        try:
            result = ssh_exec(
                self._host, self._user,
                f"vendor_id = {_VENDOR!r}\nproduct_id = {_PRODUCT_T7!r}\n" + _CHECK_SCRIPT,
            )
        except Exception as exc:
            result = dict(status="unknown", error=str(exc),
                          diagnosis="Script failed — see error for details")
        result["name"] = cfg["name"]
        result["identifier"] = f"USB {_VENDOR}:{_PRODUCT_T7}"
        result.setdefault("path", None)
        return result
