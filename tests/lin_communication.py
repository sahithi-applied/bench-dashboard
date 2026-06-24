"""LIN communication test via PCAN-USB Pro FD using plin kernel driver.

Uses the same approach as core-stack plin.py:
- Checks plin kernel module is loaded
- Uses /dev/plin/* devices
- Runs linread to capture frames, linwrite to send
"""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_LIN_COMM_SCRIPT = textwrap.dedent("""\
    import json, subprocess, sys, time, glob
    from pathlib import Path

    result = {"status": "fail", "input": "", "output": "", "error": None}

    # Step 1: Check plin kernel module
    r = subprocess.run(["lsmod"], capture_output=True, text=True)
    if "plin" not in r.stdout:
        result.update(status="fail",
                      error="plin kernel module not loaded — run: sudo modprobe plin")
        print(json.dumps(result)); sys.exit(0)

    # Step 2: Find LIN device ports
    plin_dir = Path("/dev/plin")
    plin_ports = []
    if plin_dir.exists():
        plin_ports = sorted(plin_dir.iterdir())
    if not plin_ports:
        # Fallback: scan /dev/pcanusb* or /dev/pcanlin*
        plin_ports = [Path(p) for p in
                      glob.glob("/dev/pcanusb*") + glob.glob("/dev/pcanlin*")]

    if not plin_ports:
        result.update(status="fail",
                      error="No LIN ports found in /dev/plin/ or /dev/pcan* — "
                            "check PCAN-USB Pro FD device is connected")
        print(json.dumps(result)); sys.exit(0)

    port = str(plin_ports[0])
    result["input"] = f"LIN port: {port}  baudrate: {baudrate}  mode: slave"

    # Step 3: Start LIN slave on port
    r_start = subprocess.run(
        ["lin", "start", "slave", str(baudrate), port],
        capture_output=True, text=True, timeout=5
    )
    if r_start.returncode != 0 and "already" not in r_start.stderr.lower():
        result.update(status="fail",
                      error=f"lin start failed: {r_start.stderr.strip()}")
        print(json.dumps(result)); sys.exit(0)

    # Step 4: Set ID filter to accept all frames
    subprocess.run(["lin", "set", "id-filter", "all-opened", port],
                   capture_output=True, timeout=3)

    # Step 5: Read frames for timeout_s seconds
    try:
        r_read = subprocess.run(
            ["linread", "--timeout", str(int(timeout_s * 1000)), port],
            capture_output=True, text=True, timeout=timeout_s + 2
        )
        output = r_read.stdout.strip()
        if output:
            lines = [l for l in output.splitlines() if l.strip()]
            result.update(
                status="pass",
                output=f"Received {len(lines)} LIN frame(s): {lines[0]}" +
                       (f" ... (+{len(lines)-1} more)" if len(lines) > 1 else ""),
            )
        else:
            result.update(
                status="fail",
                output="No LIN frames received",
                error=f"No frames received in {timeout_s}s — "
                      "check LIN bus wiring and DUT is sending frames",
            )
    except subprocess.TimeoutExpired:
        result.update(status="fail",
                      error=f"linread timed out after {timeout_s}s — no LIN activity on bus")

    print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str = "dev",
        baudrate: int = 19200,
        timeout_s: int = 5) -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name="LIN Communication",
        device_name="PCAN-USB Pro FD — LIN",
        status="running",
    )
    try:
        script = (
            f"baudrate = {baudrate}\n"
            f"timeout_s = {timeout_s}\n"
            + _LIN_COMM_SCRIPT
        )
        data = ssh_exec(ssh_host, ssh_user, script, timeout=timeout_s + 15)
        result.status = data.get("status", "error")
        result.input_desc = data.get("input", "")
        result.output_desc = data.get("output", "")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
