"""StarTech hub port cycle test — toggle port OFF then ON, verify device re-enumerates."""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.hub import StarTechHub
from devices.ssh_runner import ssh_exec

_CHECK_DEVICE_SCRIPT = textwrap.dedent("""\
    import json
    from pathlib import Path
    devices_before = set(p.name for p in Path("/sys/bus/usb/devices").iterdir())
    print(json.dumps({"devices": list(devices_before)}))
""")


def run(ssh_host: str, ssh_user: str, serial_path: str,
        port_idx: int = 0) -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name=f"Hub Port Cycle P{port_idx}",
        device_name=f"StarTech Hub",
        status="running",
    )
    hub = StarTechHub(serial_path, ssh_host, ssh_user)
    try:
        # Get devices before toggle
        before = ssh_exec(ssh_host, ssh_user, _CHECK_DEVICE_SCRIPT, timeout=10)
        devices_before = set(before.get("devices", []))

        # Toggle port OFF
        hub.toggle(port_idx)
        time.sleep(1.0)

        # Toggle port ON
        hub.toggle(port_idx)
        time.sleep(2.0)

        # Get devices after
        after = ssh_exec(ssh_host, ssh_user, _CHECK_DEVICE_SCRIPT, timeout=10)
        devices_after = set(after.get("devices", []))

        new_devices = devices_after - devices_before
        lost_devices = devices_before - devices_after

        if lost_devices and not new_devices:
            result.status = "fail"
            result.output_desc = f"Device did not re-enumerate after port cycle — lost: {lost_devices}"
            result.error = "Device not detected after power restore"
        else:
            result.status = "pass"
            result.output_desc = f"Port toggled OFF → ON · device re-enumerated successfully"

        result.input_desc = f"Hub: {serial_path}  Port P{port_idx}  OFF → 1s → ON → 2s"
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
