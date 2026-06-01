"""StarTech hub health check and control — uses stdlib termios, no pyserial needed."""
from __future__ import annotations

import textwrap

from devices.ssh_runner import ssh_exec

_CHECK_BODY = textwrap.dedent("""\
    import json, sys, os, time, termios
    from pathlib import Path

    result = dict(layer=0, status="unknown", error=None, diagnosis=None, port_states=[])

    if not Path(path).exists():
        result.update(layer=1, status="missing",
                      error="Serial device path not found",
                      diagnosis="Hardware: check USB cable between hub and host, and hub power")
        print(json.dumps(result)); sys.exit(0)
    result["layer"] = 1

    try:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
        old = termios.tcgetattr(fd)
        cfg = termios.tcgetattr(fd)
        cfg[0] = 0
        cfg[1] = 0
        cfg[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        cfg[3] = 0
        cfg[4] = termios.B9600
        cfg[5] = termios.B9600
        cfg[6][termios.VMIN] = 0
        cfg[6][termios.VTIME] = 5
        termios.tcsetattr(fd, termios.TCSANOW, cfg)
        termios.tcflush(fd, termios.TCIOFLUSH)
        result["layer"] = 2

        os.write(fd, b"?Q\\r")
        time.sleep(0.3)
        resp = os.read(fd, 64).decode(errors="replace").strip()
        if not resp.startswith("CENTOS"):
            result.update(status="comm_error",
                          error=f"Unexpected identity: {resp!r}",
                          diagnosis="Software: wrong device on this port or hub firmware mismatch")
            termios.tcsetattr(fd, termios.TCSANOW, old); os.close(fd)
            print(json.dumps(result)); sys.exit(0)
        result["layer"] = 3

        os.write(fd, b"GP\\r")
        time.sleep(0.3)
        s = os.read(fd, 64).decode(errors="replace").strip()
        nibble = int(s[1], 16)
        result.update(status="ok", port_states=[(nibble >> i) & 1 == 1 for i in range(4)])
        termios.tcsetattr(fd, termios.TCSANOW, old); os.close(fd)

    except PermissionError as e:
        result.update(layer=2, status="driver_error", error=str(e),
                      diagnosis="Software: sudo usermod -aG dialout $USER then re-login")
    except Exception as e:
        result.update(layer=2, status="driver_error", error=str(e),
                      diagnosis="Software: check serial port access permissions")

    print(json.dumps(result))
""")

_TOGGLE_BODY = textwrap.dedent("""\
    import json, os, time, termios

    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    old = termios.tcgetattr(fd)
    cfg = termios.tcgetattr(fd)
    cfg[0] = 0; cfg[1] = 0
    cfg[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    cfg[3] = 0; cfg[4] = termios.B9600; cfg[5] = termios.B9600
    cfg[6][termios.VMIN] = 0; cfg[6][termios.VTIME] = 5
    termios.tcsetattr(fd, termios.TCSANOW, cfg)
    termios.tcflush(fd, termios.TCIOFLUSH)

    os.write(fd, b"GP\\r"); time.sleep(0.3)
    s = os.read(fd, 64).decode(errors="replace").strip()
    nibble = int(s[1], 16)
    states = [(nibble >> i) & 1 == 1 for i in range(4)]
    states[port_idx] = not states[port_idx]
    bits = sum(int(b) << i for i, b in enumerate(states))
    os.write(fd, f"SPpass    0{bits:X}000000\\r".encode()); time.sleep(0.3)
    os.read(fd, 64)
    termios.tcsetattr(fd, termios.TCSANOW, old); os.close(fd)
    print(json.dumps({"port": port_idx, "new_state": states[port_idx]}))
""")


class StarTechHub:
    def __init__(self, serial_path: str, ssh_host: str, ssh_user: str) -> None:
        self._path = serial_path
        self._host = ssh_host
        self._user = ssh_user

    def check(self, cfg: dict) -> dict:
        try:
            result = ssh_exec(
                self._host, self._user,
                f"path = {self._path!r}\n" + _CHECK_BODY,
            )
        except Exception as exc:
            result = dict(layer=0, status="unknown", port_states=[],
                          error=str(exc), diagnosis="Script failed — see error for details")
        result["name"] = cfg["name"]
        result["serial_path"] = self._path
        result.setdefault("port_labels", cfg.get("ports", []))
        return result

    def toggle(self, port_idx: int) -> dict:
        return ssh_exec(
            self._host, self._user,
            f"path = {self._path!r}\nport_idx = {port_idx}\n" + _TOGGLE_BODY,
        )
