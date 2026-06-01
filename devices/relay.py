"""Sainsmart relay health check and control — runs checks on bench via SSH."""
from __future__ import annotations

import textwrap

from devices.ssh_runner import ssh_exec

_BAZEL_PYPATH_PREAMBLE = textwrap.dedent("""\
    import sys as _sys, glob as _glob
    _home = __import__('os').path.expanduser('~')
    for _pkg in ('pyusb', 'pyftdi'):
        _m = _glob.glob(
            f'{_home}/*/.cache/bazel/_bazel_bk/*/external/pipdeps_py310_{_pkg}/site-packages'
        )
        if _m:
            _sys.path.insert(0, _m[0])
    _lu = _glob.glob('/nix/store/*-libusb-*/lib/libusb-1.0.so.0')
    if _lu:
        import usb.backend.libusb1 as _lb
        _lb.get_backend(find_library=lambda _n: _lu[0])
        del _lb
    del _sys, _glob, _home, _pkg, _m, _lu
""")

_CHECK_BODY = textwrap.dedent("""\
    import json, sys
    from pathlib import Path

    result = dict(layer=0, status="unknown", error=None, diagnosis=None, channel_states=[])

    usb_serials = list(Path("/sys/bus/usb/devices").glob("*/serial"))
    found = any(identifier in p.read_text().strip() for p in usb_serials)
    if not found:
        result.update(layer=1, status="missing",
                      error=f"Relay {identifier!r} not found in USB device tree",
                      diagnosis="Relay not found in USB device tree — check cable and board power if persistent")
        print(json.dumps(result)); sys.exit(0)
    result["layer"] = 1

    try:
        from pyftdi.ftdi import Ftdi
        import time
        ftdi = Ftdi()
        ftdi.open_from_url(f"ftdi://::{identifier}/1")
        result["layer"] = 2
        ftdi.set_bitmode(bitmask=0xFF, mode=Ftdi.BitMode.BITBANG)
        time.sleep(0.1)
        raw = ftdi.read_pins()
        result.update(layer=3, status="ok",
                      channel_states=[(raw >> i) & 1 == 1 for i in range(4)])
        ftdi.close(freeze=True)
    except ModuleNotFoundError:
        result.update(layer=2, status="driver_error",
                      error="pyftdi not found in Python environment",
                      diagnosis="Software: pyftdi not installed — check bench Python environment")
    except Exception as e:
        msg = str(e)
        if any(k in msg for k in ("Permission", "Access", "LIBUSB", "usb_claim")):
            result.update(layer=2, status="driver_error", error=msg,
                          diagnosis="Software: ftdi_sio kernel driver conflicts with pyftdi — unbind it")
        else:
            result.update(layer=2, status="driver_error", error=msg,
                          diagnosis="Software: verify pyftdi is installed on the bench")

    print(json.dumps(result))
""")

_TOGGLE_BODY = textwrap.dedent("""\
    import json, time
    from pyftdi.ftdi import Ftdi
    ftdi = Ftdi()
    try:
        ftdi.open_from_url(f"ftdi://::{identifier}/1")
        ftdi.set_bitmode(bitmask=0xFF, mode=Ftdi.BitMode.BITBANG)
        time.sleep(0.05)
        raw = ftdi.read_pins()
        states = [(raw >> i) & 1 for i in range(4)]
        states[ch_idx] = 1 - states[ch_idx]
        ftdi.write_data(bytes([sum(s << i for i, s in enumerate(states))]))
        new_state = bool(states[ch_idx])
    finally:
        try:
            ftdi.close(freeze=True)
        except Exception:
            pass
    print(json.dumps({"channel": ch_idx, "new_state": new_state}))
""")


class SainsmartRelay:
    def __init__(self, identifier: str, ssh_host: str, ssh_user: str,
                 sudo_user: str | None = None,
                 python_interpreter: str = "python3") -> None:
        self._identifier = identifier
        self._host = ssh_host
        self._user = ssh_user
        self._sudo_user = sudo_user
        self._python_interpreter = python_interpreter

    def check(self, cfg: dict) -> dict:
        try:
            result = ssh_exec(
                self._host, self._user,
                _BAZEL_PYPATH_PREAMBLE + f"identifier = {self._identifier!r}\n" + _CHECK_BODY,
                sudo_user=self._sudo_user,
                python_interpreter=self._python_interpreter,
            )
        except Exception as exc:
            result = dict(layer=0, status="unknown", channel_states=[],
                          error=str(exc), diagnosis="Script failed — see error for details")
        result["name"] = cfg["name"]
        result["identifier"] = self._identifier
        result.setdefault("channel_states", [])
        result["channel_labels"] = [ch["label"] for ch in cfg.get("channels", [])]
        result["channel_inverted"] = [ch.get("inverted", False) for ch in cfg.get("channels", [])]
        return result

    def toggle(self, ch_idx: int) -> dict:
        return ssh_exec(
            self._host, self._user,
            _BAZEL_PYPATH_PREAMBLE + f"identifier = {self._identifier!r}\nch_idx = {ch_idx}\n" + _TOGGLE_BODY,
            sudo_user=self._sudo_user,
            python_interpreter=self._python_interpreter,
        )
