"""LabJack single-channel voltage read — passive measurement only, no driving.

Matches how this circuit is actually designed to be used: the DUT drives a
signal, the LabJack (via a voltage divider) just measures it. Unlike the
toggle test, this never writes to the pin at all.
"""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_READ_SCRIPT = textwrap.dedent("""\
    import json

    result = {"status": "fail", "output": "", "error": None}
    try:
        from labjack import ljm

        handle = ljm.openS("T7", "ANY", "ANY")
        # Force single-ended (referenced to real GND, not some other floating
        # channel) -- without this a channel can default to reading
        # differentially against an unrelated pin, producing an arbitrary
        # value that has nothing to do with the actual voltage to ground
        # (which is what a multimeter shows).
        ljm.eWriteName(handle, f"{channel}_NEGATIVE_CH", 199)
        value = round(ljm.eReadName(handle, channel), 4)
        ljm.close(handle)

        result.update(status="pass", output=f"{channel} = {value}V (single-ended vs GND)")
    except ImportError:
        result.update(status="error", error="labjack-ljm not installed on bench machine")
    except Exception as exc:
        result.update(status="error", error=str(exc))

    print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str, channel: str, signal_label: str = "") -> TestResult:
    t0 = time.time()
    label_suffix = f" ({signal_label})" if signal_label else ""
    result = TestResult(
        test_name=f"LabJack Read {channel}{label_suffix}",
        device_name="LabJack T7",
        status="running",
    )
    try:
        script = f"channel = {channel!r}\n" + _READ_SCRIPT
        data = ssh_exec(ssh_host, ssh_user, script, timeout=15)
        result.input_desc = f"{channel}{label_suffix}"
        result.output_desc = data.get("output", "")
        result.status = data.get("status", "error")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
