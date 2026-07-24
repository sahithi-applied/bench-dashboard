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

        state_match = re.search(r"state\\s+(\\S+)", out)
        state = state_match.group(1) if state_match else "UNKNOWN"
        result["state"] = state

        err_match = re.search(r"berr-counter\\s+tx\\s+(\\d+)\\s+rx\\s+(\\d+)", out)
        if err_match:
            result["tx_errors"] = int(err_match.group(1))
            result["rx_errors"] = int(err_match.group(2))

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
        tx_err = data.get("tx_errors")
        rx_err = data.get("rx_errors")
        result.input_desc = f"{iface} @ {bitrate} bps, single send attempt"
        result.output_desc = (
            f"state={state}"
            + (f"  tx_errors={tx_err} rx_errors={rx_err}" if tx_err is not None else "")
        )
        result.status = data.get("status", "error")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
