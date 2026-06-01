"""Run a Python script on the bench via SSH and return parsed JSON output."""
import base64
import json
import subprocess


def ssh_exec(host: str, user: str, script: str, timeout: int = 15,
             sudo_user: str | None = None,
             python_interpreter: str = "python3") -> dict:
    encoded = base64.b64encode(script.encode()).decode()
    python_cmd = f"import base64; exec(base64.b64decode('{encoded}').decode())"
    if sudo_user:
        cmd = f"sudo -u {sudo_user} {python_interpreter} -c \"{python_cmd}\""
    else:
        cmd = f"{python_interpreter} -c \"{python_cmd}\""
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", f"{user}@{host}", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0 or not r.stdout.strip():
        detail = r.stderr.strip() or r.stdout.strip() or "no output from remote script"
        raise RuntimeError(detail)
    return json.loads(r.stdout.strip())
