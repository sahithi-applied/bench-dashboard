"""BuildKite agent status, stop, and start via SSH."""
from __future__ import annotations

import os
import textwrap

import requests

from devices.ssh_runner import ssh_exec

BUILDKITE_ORG = "mosaic"
BUILDKITE_PIPELINE = "core-stack-fw-hil-tests-only"

_STATUS_SCRIPT = textwrap.dedent("""\
    import json, subprocess

    result = dict(service="unknown", busy=False, job=None, error=None)

    r = subprocess.run(["systemctl", "is-active", service_name],
                       capture_output=True, text=True)
    result["service"] = r.stdout.strip()

    if result["service"] == "active":
        r2 = subprocess.run(["pgrep", "-u", "bk", "-f", "buildkite-agent.*bootstrap"],
                            capture_output=True, text=True)
        if r2.returncode == 0:
            result["busy"] = True
            pid = r2.stdout.strip().splitlines()[0].strip()
            build_num = "unknown"
            try:
                env_r = subprocess.run(["sudo", "cat", f"/proc/{pid}/environ"],
                                       capture_output=True)
                env = {}
                for entry in env_r.stdout.split(bytes([0])):
                    if b"=" in entry:
                        k, _, v = entry.partition(b"=")
                        env[k] = v
                build_num = env.get(b"BUILDKITE_BUILD_NUMBER", b"unknown").decode()
            except Exception:
                pass
            result["job"] = {"build": build_num}

    print(json.dumps(result))
""")

_BK_DEBUG_SCRIPT = textwrap.dedent("""\
    import json, subprocess
    r = subprocess.run([command], capture_output=True, text=True)
    print(json.dumps({"success": r.returncode == 0, "stderr": r.stderr.strip() or None}))
""")

_DEBUG_START_SCRIPT = textwrap.dedent("""\
    import json, subprocess
    r1 = subprocess.run(['bk-debug-start'], capture_output=True, text=True)
    r2 = subprocess.run(['bk-agent-service', 'start'], capture_output=True, text=True)
    print(json.dumps({
        "success": r1.returncode == 0 and r2.returncode == 0,
        "stderr": ((r1.stderr + r2.stderr).strip()) or None,
    }))
""")

_WAIT_FOR_ACTIVE_SCRIPT = textwrap.dedent("""\
    import json, subprocess, time, sys

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = subprocess.run(["systemctl", "is-active", service_name],
                           capture_output=True, text=True)
        if r.stdout.strip() == "active":
            print(json.dumps({"ready": True}))
            sys.exit(0)
        time.sleep(1)
    print(json.dumps({"ready": False,
                      "error": f"Agent did not become active within {timeout_s}s"}))
""")


BK_DEBUG_USER = "bk-debug"


class BuildkiteAgent:
    def __init__(self, service: str, agent_name: str, queue: str,
                 ssh_host: str, ssh_user: str) -> None:
        self._service = service
        self.agent_name = agent_name
        self.queue = queue
        self._host = ssh_host
        self._user = ssh_user  # dev — used for status checks only

    def status(self) -> dict:
        try:
            result = ssh_exec(
                self._host, self._user,
                f"service_name = {self._service!r}\n" + _STATUS_SCRIPT,
            )
        except Exception as exc:
            result = dict(service="unknown", busy=False, job=None, error=str(exc))
        result["agent_name"] = self.agent_name
        result["queue"] = self.queue
        return result

    def stop(self) -> dict:
        """Remove bench from CI queue: ssh bk-debug@host, run bk-debug-start."""
        return ssh_exec(
            self._host, BK_DEBUG_USER,
            f"command = 'bk-debug-start'\n" + _BK_DEBUG_SCRIPT,
            timeout=60,
        )

    def start(self) -> dict:
        """Put bench back in CI queue: ssh bk-debug@host, run bk-debug-stop."""
        return ssh_exec(
            self._host, BK_DEBUG_USER,
            f"command = 'bk-debug-stop'\n" + _BK_DEBUG_SCRIPT,
            timeout=60,
        )

    def run_test(self, hil_pipeline: str = "vos", branch: str = "main",
                 agent_ready_timeout: int = 30) -> dict:
        """Put bench in debug mode, wait for agent to be active, then trigger build."""
        start_result = ssh_exec(self._host, BK_DEBUG_USER, _DEBUG_START_SCRIPT, timeout=60)
        if not start_result.get("success"):
            raise RuntimeError(f"bk-debug-start failed: {start_result.get('stderr')}")

        wait_result = ssh_exec(
            self._host, self._user,
            f"service_name = {self._service!r}\ntimeout_s = {agent_ready_timeout}\n"
            + _WAIT_FOR_ACTIVE_SCRIPT,
            timeout=agent_ready_timeout + 10,
        )
        if not wait_result.get("ready"):
            raise RuntimeError(
                wait_result.get("error", f"Agent not active after {agent_ready_timeout}s")
            )

        token = os.environ.get("BUILDKITE_API_TOKEN", "")
        if not token:
            raise RuntimeError("BUILDKITE_API_TOKEN environment variable not set")

        resp = requests.post(
            f"https://api.buildkite.com/v2/organizations/{BUILDKITE_ORG}"
            f"/pipelines/{BUILDKITE_PIPELINE}/builds",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "branch": branch,
                "env": {
                    "HIL_PIPELINES": hil_pipeline,
                    "AGENT_TAGS": f"hostname={self.agent_name}",
                },
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"build_number": data.get("number"), "web_url": data.get("web_url")}
