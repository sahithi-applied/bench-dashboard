"""LabJack T7 functional test — read analog inputs via LJM library on bench machine."""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_LABJACK_SCRIPT = textwrap.dedent("""\
    import json, sys

    result = {"status": "fail", "input": "", "output": "", "error": None}

    try:
        from labjack import ljm

        handle = ljm.openS("T7", "USB", "ANY")
        info = ljm.getHandleInfo(handle)
        device_type, conn_type, serial, ip, port, max_bytes = info

        readings = {}
        for ch in ["AIN0", "AIN1", "AIN2", "AIN3"]:
            try:
                val = ljm.eReadName(handle, ch)
                readings[ch] = round(val, 4)
            except Exception as e:
                readings[ch] = f"error: {e}"

        ljm.close(handle)

        result.update(
            status="pass",
            input=f"Device: T7  Serial: {serial}  Connection: USB",
            output="  ".join(f"{k}={v}V" for k, v in readings.items()
                             if not str(v).startswith("error")),
        )
    except ImportError:
        result.update(status="error",
                      error="labjack-ljm not installed on bench machine")
    except Exception as exc:
        msg = str(exc)
        if "No LabJack" in msg or "Could not find" in msg:
            result.update(status="fail",
                          error="LabJack T7 not found — check USB connection and device power")
        else:
            result.update(status="error", error=msg)

    print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str = "dev") -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name="LabJack Voltage Read",
        device_name="LabJack T7",
        status="running",
    )
    try:
        data = ssh_exec(ssh_host, ssh_user, _LABJACK_SCRIPT, timeout=15)
        result.status = data.get("status", "error")
        result.input_desc = data.get("input", "")
        result.output_desc = data.get("output", "")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
