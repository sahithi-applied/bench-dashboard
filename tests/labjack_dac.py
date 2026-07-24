"""LabJack DAC analog voltage output test — drive DAC0/DAC1 to a real analog
voltage (not just digital high/low) for a hold duration, for multimeter
verification on the DUT side.
"""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_DAC_SCRIPT = textwrap.dedent("""\
    import json, time

    result = {"status": "fail", "output": "", "error": None}
    try:
        from labjack import ljm

        handle = ljm.openS("T7", "ANY", "ANY")
        ljm.eWriteName(handle, dac_channel, voltage)
        time.sleep(duration_s)
        ljm.eWriteName(handle, dac_channel, 0.0)
        ljm.close(handle)

        result.update(status="pass",
                      output=f"{dac_channel} output {voltage}V for {duration_s}s, then reset to 0V")
    except ImportError:
        result.update(status="error", error="labjack-ljm not installed on bench machine")
    except Exception as exc:
        result.update(status="error", error=str(exc))

    print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str, dac_channel: str, voltage: float = 3.3,
        signal_label: str = "", duration_s: int = 10) -> TestResult:
    t0 = time.time()
    label_suffix = f" ({signal_label})" if signal_label else ""
    result = TestResult(
        test_name=f"LabJack DAC {dac_channel}{label_suffix}",
        device_name="LabJack T7",
        status="running",
    )
    try:
        script = (
            f"dac_channel = {dac_channel!r}\n"
            f"voltage = {voltage}\n"
            f"duration_s = {duration_s}\n"
            + _DAC_SCRIPT
        )
        data = ssh_exec(ssh_host, ssh_user, script, timeout=duration_s + 15)
        result.input_desc = f"{dac_channel}{label_suffix} @ {voltage}V — hold {duration_s}s"
        result.output_desc = data.get("output", "")
        result.status = data.get("status", "error")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
