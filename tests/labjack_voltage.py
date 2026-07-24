"""LabJack T7 functional test — read analog inputs via LJM library on bench machine.

Reads AIN0-13, covering all voltage-divider inputs traced from the schematic
(Voltage_Divider_1-4/6-8, each an LJTick-Divider-4 reading a DUT rail like
HSD/eFuse/FHB/LSD outputs) that are actually analog-capable on a T7 --
Divider_5/9/10 use EIO6-7/CIO0-3, which are digital-only pins with no AIN
alias, so those aren't reachable this way regardless. Channel-to-signal
mapping was hand-traced and click-verified against the schematic PDF (see
LABJACK_AIN_MAP in tester.html); there's no core-stack-verified mapping for
this bench (only DAC0/DAC1 outputs are modeled there). Reports raw voltages
for manual inspection -- with no firmware flashed there's no known-good
baseline to assert pass/fail against.
"""
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

        handle = ljm.openS("T7", "ANY", "ANY")
        info = ljm.getHandleInfo(handle)
        device_type, conn_type, serial, ip, port, max_bytes = info

        readings = {}
        for ch in ["AIN0", "AIN1", "AIN2", "AIN3", "AIN4", "AIN5",
                   "AIN6", "AIN7", "AIN8", "AIN9", "AIN10", "AIN11",
                   "AIN12", "AIN13"]:
            try:
                # Force single-ended (vs real GND) -- without this a channel
                # can default to reading differentially against an unrelated
                # floating pin, giving a value with no relation to the actual
                # voltage to ground that a multimeter would show.
                ljm.eWriteName(handle, f"{ch}_NEGATIVE_CH", 199)
                val = ljm.eReadName(handle, ch)
                readings[ch] = round(val, 4)
            except Exception as e:
                readings[ch] = f"error: {e}"

        ljm.close(handle)

        result.update(
            status="pass",
            input=f"Device: T7  Serial: {serial}  ConnType: {conn_type}  IP: {ip}",
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
                          error="LabJack T7 not found — check USB/Ethernet connection and device power")
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
