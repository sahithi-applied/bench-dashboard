"""LabJack digital toggle test — drive a FIO/EIO pin HIGH then LOW for a hold
duration, so the corresponding DUT pin can be multimeter-probed independent
of any DUT firmware. Covers AIN0-13 (FIO0-7, EIO0-5); the CIO-based dividers
aren't analog/digital-addressable this way and are out of scope.
"""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_TOGGLE_SCRIPT = textwrap.dedent("""\
    import json, time

    result = {"status": "fail", "output": "", "error": None}
    try:
        from labjack import ljm

        # FIO0-7 and EIO0-5 are the same physical pins as AIN0-7/AIN8-13 --
        # reading the AIN alias back while driving the digital pin confirms
        # the LabJack's own output actually reached/held the commanded
        # level. It does NOT reveal the DUT-side (post-divider) voltage --
        # that's a separate physical node with no LabJack channel wired to
        # it, only visible via multimeter at the DUT connector.
        _fio_to_ain = {"FIO%d" % i: "AIN%d" % i for i in range(8)}
        _fio_to_ain.update({"EIO%d" % i: "AIN%d" % (i + 8) for i in range(6)})
        ain_name = _fio_to_ain.get(channel)

        handle = ljm.openS("T7", "ANY", "ANY")
        ljm.eWriteName(handle, channel, 1)

        high_reading = None
        if ain_name:
            try:
                high_reading = round(ljm.eReadName(handle, ain_name), 4)
            except Exception:
                high_reading = None

        time.sleep(duration_s)
        ljm.eWriteName(handle, channel, 0)
        ljm.close(handle)

        readback = (f", LabJack measured {high_reading}V on {ain_name} while driving"
                    if high_reading is not None else "")
        result.update(status="pass",
                      output=f"{channel} driven HIGH (3.3V) for {duration_s}s{readback}, then released LOW")
    except ImportError:
        result.update(status="error", error="labjack-ljm not installed on bench machine")
    except Exception as exc:
        result.update(status="error", error=str(exc))

    print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str, channel: str, signal_label: str = "",
        duration_s: int = 10) -> TestResult:
    t0 = time.time()
    label_suffix = f" ({signal_label})" if signal_label else ""
    result = TestResult(
        test_name=f"LabJack Toggle {channel}{label_suffix}",
        device_name="LabJack T7",
        status="running",
    )
    try:
        script = (
            f"channel = {channel!r}\n"
            f"duration_s = {duration_s}\n"
            + _TOGGLE_SCRIPT
        )
        data = ssh_exec(ssh_host, ssh_user, script, timeout=duration_s + 15)
        result.input_desc = f"{channel}{label_suffix} — hold HIGH {duration_s}s"
        result.output_desc = data.get("output", "")
        result.status = data.get("status", "error")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
