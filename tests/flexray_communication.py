"""FlexRay communication test via FL3X device.

Mirrors core-stack FL3XDevice (flexray_helpers.py):
- Runs fl3x_hil_tests C binary with libHwCom.so
- Test 1: "Device Connectivity" — quick 5s reachability check
- Test 2: "FlexRay Analyzer Read Frames" — full capture sequence (30s)
  Sequence: ConfigReq -> StartReq -> ConfigChannelReq -> ConfigFilterReq
            -> StartChannelReq -> Capture UDP -> Stop
"""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_FL3X_SCRIPT = textwrap.dedent("""\
    import json, subprocess, sys, glob, os
    from pathlib import Path

    result = {"status": "fail", "input": "", "output": "", "error": None}

    DEVICE_IP   = device_ip
    DEVICE_PORT = device_port
    CHANNEL     = channel
    TEST_NAME   = test_name
    TIMEOUT     = timeout_s

    result["input"] = (f"FL3X: {DEVICE_IP}:{DEVICE_PORT}  "
                       f"channel: {CHANNEL}  test: {TEST_NAME!r}")

    # Find fl3x_hil_tests binary — check common Nix/Bazel locations
    binary_candidates = (
        glob.glob("/nix/store/*/bin/fl3x_hil_tests")
        + glob.glob("/nix/store/*/fl3x_hil_tests")
        + glob.glob(os.path.expanduser("~/.cache/bazel/**/fl3x_hil_tests"),
                    recursive=True)
        + ["/usr/local/bin/fl3x_hil_tests"]
    )
    binary = next((b for b in binary_candidates if Path(b).is_file()), None)

    if not binary:
        result.update(status="error",
                      error="fl3x_hil_tests binary not found — "
                            "build with: bazel build //firmware/hil_tests/lib/fl3x:fl3x_hil_tests")
        print(json.dumps(result)); sys.exit(0)

    # Find libHwCom.so
    lib_candidates = (
        glob.glob("/nix/store/*/lib/libHwCom.so")
        + glob.glob("/nix/store/*/libHwCom.so")
        + glob.glob(os.path.expanduser(
            "~/.cache/bazel/**/libHwCom.so"), recursive=True)
    )
    lib_dir = str(Path(lib_candidates[0]).parent) if lib_candidates else ""

    env = os.environ.copy()
    if lib_dir:
        env["LD_LIBRARY_PATH"] = f"{lib_dir}:{env.get('LD_LIBRARY_PATH', '')}"

    cmd = [
        binary,
        "--ip", DEVICE_IP,
        "--port", str(DEVICE_PORT),
        "--channel", str(CHANNEL),
        "--test", TEST_NAME,
        "--verbose",
    ]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=TIMEOUT, env=env)
        if r.returncode == -11:  # SIGSEGV — device unreachable
            result.update(status="fail",
                          output=r.stdout.strip(),
                          error=f"FL3X device not reachable at {DEVICE_IP}:{DEVICE_PORT} "
                                "(SIGSEGV) — check Ethernet cable and device power")
        elif r.returncode != 0:
            result.update(status="fail",
                          output=r.stdout.strip(),
                          error=r.stderr.strip() or f"Binary exited with code {r.returncode}")
        else:
            result.update(status="pass", output=r.stdout.strip())
    except subprocess.TimeoutExpired:
        result.update(status="fail",
                      error=f"Test timed out after {TIMEOUT}s — "
                            "check FL3X device is powered and FlexRay bus is active")

    print(json.dumps(result))
""")

CONNECTIVITY_TEST = "Device Connectivity"
ANALYZER_TEST = "FlexRay Analyzer Read Frames"


def run_connectivity(ssh_host: str, ssh_user: str = "dev",
                     device_ip: str = "192.168.1.15",
                     device_port: int = 1500,
                     channel: int = 1) -> TestResult:
    """Quick 5s connectivity check."""
    return _run(ssh_host, ssh_user, CONNECTIVITY_TEST,
                device_ip, device_port, channel, timeout_s=10)


def run_analyzer(ssh_host: str, ssh_user: str = "dev",
                 device_ip: str = "192.168.1.15",
                 device_port: int = 1500,
                 channel: int = 1) -> TestResult:
    """Full FlexRay frame capture test — requires active FlexRay bus (DUT present)."""
    return _run(ssh_host, ssh_user, ANALYZER_TEST,
                device_ip, device_port, channel, timeout_s=35)


def _run(ssh_host: str, ssh_user: str, test_name: str,
         device_ip: str, device_port: int, channel: int,
         timeout_s: int) -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name=f"FlexRay {test_name}",
        device_name=f"FL3X ({device_ip})",
        status="running",
    )
    try:
        script = (
            f"device_ip = {device_ip!r}\n"
            f"device_port = {device_port}\n"
            f"channel = {channel}\n"
            f"test_name = {test_name!r}\n"
            f"timeout_s = {timeout_s}\n"
            + _FL3X_SCRIPT
        )
        data = ssh_exec(ssh_host, ssh_user, script, timeout=timeout_s + 10)
        result.status = data.get("status", "error")
        result.input_desc = data.get("input", "")
        result.output_desc = data.get("output", "")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
