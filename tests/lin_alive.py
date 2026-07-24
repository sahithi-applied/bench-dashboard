"""LIN master transmit test — pre-flash wiring sanity check via the neoVI.

Unlike CAN (broadcast bus, any node can talk any time), LIN is
master-polled: a slave never transmits spontaneously. With no DUT firmware
flashed yet, there's nothing to listen for, so this configures the neoVI
itself as LIN master and transmits a frame, then checks the resulting
error/status flags for signs of an electrical fault (short, open, bus
contention). It cannot prove the DUT side is wired correctly (nothing
there to answer), but it validates the neoVI's own transceiver drives the
bus cleanly, which is the wiring check that's actually possible pre-flash.

Runs via the compiled lin_active_test binary (uses libicsneo's C++ API
directly, bypassing icsscand's SocketCAN bridge, which has no LIN support).
icsscand is stopped for the duration to avoid two concurrent sessions
against the same device, then restarted immediately after.
"""
from __future__ import annotations
import json
import re
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_LIN_ALIVE_SCRIPT = """\
import json, subprocess

result = {"ok": False, "output": "", "error": None}
subprocess.run(["sudo", "systemctl", "stop", "icsscand.service"], capture_output=True)
try:
    proc = subprocess.run(
        ["sudo", "/usr/local/bin/lin_active_test", serial_number, str(frame_id)],
        capture_output=True, text=True, timeout=15)
    result["output"] = proc.stdout.strip()
    if proc.returncode != 0:
        result["error"] = proc.stderr.strip() or f"exit code {proc.returncode}"
    else:
        result["ok"] = True
finally:
    subprocess.run(["sudo", "systemctl", "start", "icsscand.service"], capture_output=True)

print(json.dumps(result))
"""


def run(ssh_host: str, ssh_user: str, serial_number: str, frame_id: int = 1) -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name=f"LIN Master Test (neoVI {serial_number})",
        device_name=f"neoVI {serial_number}",
        status="running",
    )
    try:
        script = (
            f"serial_number = {serial_number!r}\n"
            f"frame_id = {frame_id}\n"
            + _LIN_ALIVE_SCRIPT
        )
        data = ssh_exec(ssh_host, ssh_user, script, timeout=25)
        result.input_desc = f"neoVI {serial_number}, LIN frame ID={frame_id} (transmitted as master)"

        if not data.get("ok"):
            result.status = "error"
            result.error = data.get("error") or "lin_active_test failed to run"
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        output = data.get("output", "")
        m = re.search(r"RESULT_JSON:\s*(\{.*\})", output)
        if not m:
            result.status = "error"
            result.error = "Could not find RESULT_JSON in lin_active_test output"
            result.output_desc = output
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        summary = json.loads(m.group(1))
        transmit_ok = summary.get("transmit_accepted", False)
        messages_seen = summary.get("lin_messages_seen", 0)
        fault = summary.get("fault_detected", True)

        result.output_desc = (
            f"transmit_accepted={transmit_ok}  messages_seen={messages_seen}  "
            f"fault_detected={fault}"
        )

        if not transmit_ok:
            result.status = "fail"
            result.error = "transmit() was not accepted by the device"
        elif messages_seen == 0:
            result.status = "fail"
            result.error = "No LIN message echoed back after transmit -- device may not be reporting TX at all"
        elif fault:
            result.status = "fail"
            result.error = "Electrical fault flags set on transmit (short/open/bus contention) -- check wiring"
        else:
            result.status = "pass"
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
