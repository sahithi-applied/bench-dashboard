"""Ethernet device reachability test — ping target IP from bench machine."""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_PING_SCRIPT = textwrap.dedent("""\
    import json, subprocess

    r = subprocess.run(
        ["ping", "-c", "3", "-W", "2", target_ip],
        capture_output=True, text=True
    )
    loss_line = [l for l in r.stdout.splitlines() if "packet loss" in l]
    rtt_line  = [l for l in r.stdout.splitlines() if "rtt" in l or "round-trip" in l]

    print(json.dumps({
        "reachable": r.returncode == 0,
        "summary": loss_line[0].strip() if loss_line else r.stderr.strip(),
        "rtt": rtt_line[0].strip() if rtt_line else "",
    }))
""")


def run(ssh_host: str, ssh_user: str,
        target_ip: str, device_name: str = "") -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name="Ethernet Reachability",
        device_name=device_name or target_ip,
        status="running",
    )
    try:
        script = f"target_ip = {target_ip!r}\n" + _PING_SCRIPT
        data = ssh_exec(ssh_host, ssh_user, script, timeout=15)

        result.input_desc = f"Ping {target_ip} × 3 from bench machine"
        result.output_desc = data.get("summary", "") + (
            f"  {data.get('rtt', '')}" if data.get("rtt") else ""
        )
        result.status = "pass" if data.get("reachable") else "fail"
        if not data.get("reachable"):
            result.error = f"Device at {target_ip} not reachable — check Ethernet cable and device power"
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
