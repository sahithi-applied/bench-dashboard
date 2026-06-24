"""Denkovi relay toggle test — set channel OFF then ON via HTTP, verify state."""
from __future__ import annotations
import time
from tests.base import TestResult
from devices.denkovi_relay import DenkoviRelay


def run(host: str, port: int = 80, password: str = "admin",
        channel_idx: int = 0) -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name=f"Denkovi Toggle CH{channel_idx}",
        device_name=f"Denkovi {host}",
        status="running",
    )
    relay = DenkoviRelay(host, port, password)
    steps = []
    try:
        # Read original state
        import requests
        data = relay._get()
        original = relay._parse_states(data)[channel_idx]

        # Turn OFF
        relay.set_channel(channel_idx, False)
        time.sleep(0.3)
        state_off = relay._parse_states(relay._get())[channel_idx]
        off_ok = not state_off
        steps.append(f"→ OFF: {'OK' if off_ok else 'FAIL — stuck ON'}")

        # Turn ON
        relay.set_channel(channel_idx, True)
        time.sleep(0.3)
        state_on = relay._parse_states(relay._get())[channel_idx]
        on_ok = state_on
        steps.append(f"→ ON:  {'OK' if on_ok else 'FAIL — stuck OFF'}")

        # Restore original
        relay.set_channel(channel_idx, original)

        passed = off_ok and on_ok
        result.status = "pass" if passed else "fail"
        result.input_desc = f"Host: {host}  CH{channel_idx}"
        result.output_desc = "  ".join(steps)
        result.error = None if passed else "Channel not responding to HTTP commands"
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
