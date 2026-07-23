"""Denkovi relay toggle test — set channel OFF then ON, verify state.

HTTP is proxied via SSH through the target machine because the Denkovi
lives on 192.168.1.x, which is only reachable from the bench.
"""
from __future__ import annotations
import time
from tests.base import TestResult
from devices.denkovi_relay import DenkoviRelaySSH


def run(ssh_host: str, ssh_user: str, denkovi_host: str,
        port: int = 80, password: str = "admin",
        channel_idx: int = 0) -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name=f"Denkovi Toggle Q{channel_idx + 1}",
        device_name=f"Denkovi {denkovi_host}",
        status="running",
    )
    relay = DenkoviRelaySSH(denkovi_host, ssh_host, ssh_user, port, password)
    steps = []
    try:
        # Read original state
        check = relay.check({"name": denkovi_host, "channels": []})
        if check.get("status") != "ok":
            result.status = "error"
            result.error = check.get("diagnosis") or check.get("error") or "Denkovi unreachable"
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        original = check["channel_states"][channel_idx]

        # Turn OFF
        relay.set_channel(channel_idx, False)
        time.sleep(0.3)
        state_off = relay.check({"name": denkovi_host, "channels": []})["channel_states"][channel_idx]
        off_ok = not state_off
        steps.append(f"→ OFF: {'OK' if off_ok else 'FAIL — stuck ON'}")

        # Turn ON
        relay.set_channel(channel_idx, True)
        time.sleep(0.3)
        state_on = relay.check({"name": denkovi_host, "channels": []})["channel_states"][channel_idx]
        on_ok = state_on
        steps.append(f"→ ON:  {'OK' if on_ok else 'FAIL — stuck OFF'}")

        # Restore original
        relay.set_channel(channel_idx, original)

        passed = off_ok and on_ok
        result.status = "pass" if passed else "fail"
        result.input_desc = f"Denkovi {denkovi_host} CH{channel_idx} via {ssh_host}"
        result.output_desc = "  ".join(steps)
        result.error = None if passed else "Channel not responding to HTTP commands"
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
