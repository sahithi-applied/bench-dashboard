"""Intrepid interface check — verify all expected SocketCAN interfaces are up."""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_IFACE_CHECK_SCRIPT = textwrap.dedent("""\
    import json, subprocess, re

    r = subprocess.run(["ip", "link", "show"], capture_output=True, text=True)
    pattern = re.compile(rf"(\\w+_{serial_number}):", re.IGNORECASE)
    found = list(dict.fromkeys(pattern.findall(r.stdout)))

    up = [i for i in found
          if re.search(rf"{re.escape(i)}:.*<[^>]*UP", r.stdout)]

    print(json.dumps({
        "found": found,
        "up": up,
        "total": len(found),
        "all_up": len(found) > 0 and len(up) == len(found),
    }))
""")


def run(ssh_host: str, ssh_user: str, serial_number: str,
        expected_count: int = 1) -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name="Intrepid Interfaces",
        device_name=f"Intrepid {serial_number}",
        status="running",
    )
    try:
        script = f"serial_number = {serial_number!r}\n" + _IFACE_CHECK_SCRIPT
        data = ssh_exec(ssh_host, ssh_user, script, timeout=10)

        found = data.get("found", [])
        up = data.get("up", [])

        result.input_desc = f"Serial: {serial_number}  Expected: ≥{expected_count} interface(s)"
        result.output_desc = (
            f"Found: {', '.join(found) or 'none'}  "
            f"UP: {', '.join(up) or 'none'}"
        )

        if not found:
            result.status = "fail"
            result.error = "No SocketCAN interfaces found — check icsscand service and Ethernet connection"
        elif len(found) < expected_count:
            result.status = "fail"
            result.error = f"Only {len(found)} interface(s) found, expected {expected_count}"
        elif len(up) < len(found):
            result.status = "fail"
            result.error = f"{len(found) - len(up)} interface(s) found but not UP"
        else:
            result.status = "pass"
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
