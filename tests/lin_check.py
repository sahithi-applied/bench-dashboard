"""LIN interface check via Intrepid NeoVI — verify LIN interfaces are enumerated by icsscand."""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_LIN_CHECK_SCRIPT = textwrap.dedent("""\
    import json, subprocess, re, sys

    result = {"status": "fail", "input": "", "output": "", "error": None}

    # icsscand creates LIN interfaces named lin_<SERIAL> or lslin_<SERIAL>
    r = subprocess.run(["ip", "link", "show"], capture_output=True, text=True)
    lin_re = re.compile(rf"((?:lin|lslin|swcan)\\w*_{serial_number}):", re.IGNORECASE)
    found = list(dict.fromkeys(lin_re.findall(r.stdout)))

    # Also check /sys/class/net for any lin interfaces with this serial
    import subprocess as sp
    try:
        nets = sp.run(["ls", "/sys/class/net"], capture_output=True, text=True)
        all_ifaces = nets.stdout.split()
        lin_ifaces = [i for i in all_ifaces
                      if serial_number.lower() in i.lower()
                      and any(i.startswith(p) for p in ("lin", "lslin", "swcan"))]
        found = list(dict.fromkeys(found + lin_ifaces))
    except Exception:
        pass

    result["input"] = f"Serial: {serial_number}  Checking for LIN interfaces via icsscand"

    if found:
        result.update(
            status="pass",
            output=f"LIN interfaces found: {', '.join(found)}",
        )
    else:
        # Check if icsscand is running at all
        r2 = subprocess.run(["systemctl", "is-active", "icsscand"],
                            capture_output=True, text=True)
        ics_status = r2.stdout.strip()
        if ics_status != "active":
            result.update(
                status="fail",
                output=f"icsscand status: {ics_status}",
                error="icsscand is not running — LIN interfaces cannot be enumerated",
            )
        else:
            result.update(
                status="fail",
                output="No LIN interfaces found in ip link show",
                error=f"No LIN interfaces for serial {serial_number} — "
                      "check Intrepid LIN cable and icsscand enumeration",
            )

    print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str, serial_number: str = "") -> TestResult:
    """Empty serial_number matches LIN interfaces from any device."""
    t0 = time.time()
    result = TestResult(
        test_name="LIN Interface Check",
        device_name=f"Intrepid {serial_number} — LIN" if serial_number else "LIN interfaces (any)",
        status="running",
    )
    try:
        script = f"serial_number = {serial_number!r}\n" + _LIN_CHECK_SCRIPT
        data = ssh_exec(ssh_host, ssh_user, script, timeout=10)
        result.status = data.get("status", "error")
        result.input_desc = data.get("input", "")
        result.output_desc = data.get("output", "")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
