"""CAN bus electrical health check — pre-flash, no DUT traffic required.

can_alive listens passively for real DUT traffic, which needs firmware
actively transmitting. With no firmware yet, there's nothing to hear.
This instead brings the channel up, sends one test frame ourselves, and
checks the resulting bus error-state rather than requiring a response.

CAN requires another live node to ACK every frame -- with nothing else on
the bus, any send generates an ACK error; that's expected and doesn't mean
the wiring is bad. The real fault signal is reaching bus-off (a hard-fail
state that normally takes ~255 consecutive errors to reach) from a single
send attempt, which points to a short/open/missing termination rather than
just "nothing else is listening".
"""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_CAN_HEALTH_SCRIPT = textwrap.dedent("""\
    import json, subprocess, re

    result = {"status": "fail", "state": "", "tx_errors": None, "error": None}

    subprocess.run(["sudo", "ip", "link", "set", iface, "down"], capture_output=True)
    up = subprocess.run(
        ["sudo", "ip", "link", "set", iface, "up", "type", "can", "bitrate", str(bitrate)],
        capture_output=True, text=True)
    if up.returncode != 0:
        result["error"] = f"Failed to bring up {iface}: {up.stderr.strip()}"
        print(json.dumps(result))
    else:
        # Attempt a send -- with no other live node, this will not be ACKed,
        # which is expected. We're checking the resulting state, not whether
        # this succeeds.
        subprocess.run(["cansend", iface, "123#DEADBEEF"], capture_output=True, timeout=3)

        detail = subprocess.run(
            ["ip", "-details", "-statistics", "link", "show", iface],
            capture_output=True, text=True)
        out = detail.stdout

        state_match = re.search(r"can state\\s+(\\S+)", out)
        state = state_match.group(1) if state_match else "UNKNOWN"
        result["state"] = state

        # Header line "re-started bus-errors arbit-lost error-warn error-pass bus-off"
        # is followed by a line of matching numeric columns.
        header_match = re.search(
            r"re-started\\s+bus-errors\\s+arbit-lost\\s+error-warn\\s+error-pass\\s+bus-off\\s*\\n\\s*(\\d+)\\s+(\\d+)\\s+(\\d+)\\s+(\\d+)\\s+(\\d+)\\s+(\\d+)",
            out)
        if header_match:
            result["bus_errors"] = int(header_match.group(2))
            result["bus_off_count"] = int(header_match.group(6))

        if state == "BUS-OFF":
            result["status"] = "fail"
            result["error"] = (
                "Bus went BUS-OFF after a single send attempt -- this points to a "
                "physical fault (short, open, or missing termination), not just "
                "\\"nothing else is on the bus\\""
            )
        elif state in ("ERROR-ACTIVE", "ERROR-WARNING", "ERROR-PASSIVE"):
            result["status"] = "pass"
        else:
            result["status"] = "fail"
            result["error"] = f"Unexpected bus state: {state}"

        subprocess.run(["sudo", "ip", "link", "set", iface, "down"], capture_output=True)
        print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str, iface: str, bitrate: int = 500000) -> TestResult:
    t0 = time.time()
    result = TestResult(test_name=f"CAN Bus Health {iface}", device_name=iface, status="running")
    try:
        script = (
            f"iface = {iface!r}\n"
            f"bitrate = {bitrate}\n"
            + _CAN_HEALTH_SCRIPT
        )
        data = ssh_exec(ssh_host, ssh_user, script, timeout=15)
        state = data.get("state", "?")
        bus_errors = data.get("bus_errors")
        bus_off_count = data.get("bus_off_count")
        result.input_desc = f"{iface} @ {bitrate} bps, single send attempt"
        result.output_desc = (
            f"state={state}"
            + (f"  bus_errors={bus_errors} bus_off_count={bus_off_count}"
               if bus_errors is not None else "")
        )
        result.status = data.get("status", "error")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
