"""CAN alive test — bring a channel up at a real bitrate and listen for live bus traffic.

Unlike can_loopback (which bridges two arbitrary channels that may not be
physically connected on a real DUT-wired bench), this validates a single
channel end-to-end: wiring, termination, bitrate match, and that the DUT
is actually transmitting. Bitrate default (500000) matches the arbitration
rate used for alive_test buses in core-stack's target_configurations.pkl.
"""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_CAN_ALIVE_SCRIPT = textwrap.dedent("""\
    import json, subprocess

    result = {"status": "fail", "frames": [], "error": None}

    subprocess.run(["sudo", "ip", "link", "set", iface, "down"], capture_output=True)
    up = subprocess.run(
        ["sudo", "ip", "link", "set", iface, "up", "type", "can", "bitrate", str(bitrate)],
        capture_output=True, text=True)
    if up.returncode != 0:
        result["error"] = f"Failed to bring up {iface}: {up.stderr.strip()}"
        print(json.dumps(result))
    else:
        dump = subprocess.run(
            ["candump", "-T", str(duration_s * 1000), "-n", "50", iface],
            capture_output=True, text=True)
        frames = [l for l in dump.stdout.splitlines() if l.strip()]
        result["frames"] = frames[:10]
        result["status"] = "pass" if frames else "fail"
        if not frames:
            result["error"] = (
                f"No CAN traffic observed on {iface} in {duration_s}s -- "
                "DUT may be unpowered, wrong bitrate, or bus not wired"
            )
        subprocess.run(["sudo", "ip", "link", "set", iface, "down"], capture_output=True)
        print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str, iface: str,
        bitrate: int = 500000, duration_s: int = 3) -> TestResult:
    t0 = time.time()
    result = TestResult(test_name=f"CAN Alive {iface}", device_name=iface, status="running")
    try:
        script = (
            f"iface = {iface!r}\n"
            f"bitrate = {bitrate}\n"
            f"duration_s = {duration_s}\n"
            + _CAN_ALIVE_SCRIPT
        )
        data = ssh_exec(ssh_host, ssh_user, script, timeout=duration_s + 10)
        frames = data.get("frames", [])
        result.input_desc = f"{iface} @ {bitrate} bps, listening {duration_s}s"
        result.output_desc = (
            f"{len(frames)} frame(s): " + "; ".join(frames[:3]) if frames else "no frames"
        )
        result.status = "pass" if data.get("status") == "pass" else "fail"
        result.error = data.get("error") if result.status == "fail" else None
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
