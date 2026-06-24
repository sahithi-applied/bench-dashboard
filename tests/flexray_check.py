"""FL3x FlexRay analyzer check — verify reachability and HTTP API response."""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_FLEXRAY_SCRIPT = textwrap.dedent("""\
    import json, subprocess, socket, urllib.request

    result = {"status": "fail", "input": "", "output": "", "error": None}
    result["input"] = f"FL3x at {device_ip}  interface: {interface}"

    # Step 1: Check network interface exists on bench
    r = subprocess.run(["ip", "link", "show", interface], capture_output=True, text=True)
    if r.returncode != 0:
        result.update(status="fail",
                      error=f"Network interface {interface!r} not found on bench machine")
        print(json.dumps(result)); import sys; sys.exit(0)

    iface_up = "UP" in r.stdout
    if not iface_up:
        subprocess.run(["sudo", "ip", "link", "set", interface, "up"],
                       capture_output=True)
        import time; time.sleep(0.5)

    # Step 2: Ping FL3x device
    ping = subprocess.run(["ping", "-c", "3", "-W", "2", "-I", interface, device_ip],
                          capture_output=True, text=True)
    ping_ok = ping.returncode == 0
    loss = [l for l in ping.stdout.splitlines() if "packet loss" in l]
    ping_summary = loss[0].strip() if loss else ping.stderr.strip()

    if not ping_ok:
        result.update(status="fail", output=ping_summary,
                      error=f"FL3x at {device_ip} not reachable — check Ethernet cable and device power")
        print(json.dumps(result)); import sys; sys.exit(0)

    # Step 3: HTTP API check (FL3x has a web interface)
    http_ok = False
    http_info = ""
    try:
        req = urllib.request.urlopen(f"http://{device_ip}/", timeout=3)
        http_ok = req.status == 200
        http_info = f"HTTP {req.status}"
    except Exception as e:
        http_info = f"HTTP: {e}"

    steps = [f"Ping: OK ({ping_summary})", f"HTTP: {'OK' if http_ok else 'No response — ' + http_info}"]
    result.update(
        status="pass" if ping_ok else "fail",
        output="  ·  ".join(steps),
    )
    print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str,
        device_ip: str = "192.168.1.15",
        interface: str = "enp0s20f0u1u1c2") -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name="FlexRay Analyzer Check",
        device_name=f"FL3x FlexRay ({device_ip})",
        status="running",
    )
    try:
        script = (
            f"device_ip = {device_ip!r}\n"
            f"interface = {interface!r}\n"
            + _FLEXRAY_SCRIPT
        )
        data = ssh_exec(ssh_host, ssh_user, script, timeout=20)
        result.status = data.get("status", "error")
        result.input_desc = data.get("input", "")
        result.output_desc = data.get("output", "")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
