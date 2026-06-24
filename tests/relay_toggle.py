"""Sainsmart relay toggle test — toggle channel OFF then ON, verify state each time."""
from __future__ import annotations
import textwrap
import time
from tests.base import TestResult
from devices.ssh_runner import ssh_exec

_RELAY_TOGGLE_SCRIPT = textwrap.dedent("""\
    import json, time, sys
    import sys, glob
    _home = __import__('os').path.expanduser('~')
    for _pkg in ('pyusb', 'pyftdi'):
        _m = glob.glob(f'{_home}/*/.cache/bazel/_bazel_bk/*/external/pipdeps_py310_{_pkg}/site-packages')
        if _m:
            sys.path.insert(0, _m[0])
    _lu = glob.glob('/nix/store/*-libusb-*/lib/libusb-1.0.so.0')
    if _lu:
        import usb.backend.libusb1 as _lb
        _lb.get_backend(find_library=lambda _n: _lu[0])

    from pyftdi.ftdi import Ftdi

    result = {"status": "fail", "input": "", "output": "", "error": None}

    def read_state(ftdi, ch):
        raw = ftdi.read_pins()
        return bool((raw >> ch) & 1)

    def write_state(ftdi, ch, states, desired):
        states[ch] = desired
        ftdi.write_data(bytes([sum(s << i for i, s in enumerate(states))]))
        time.sleep(0.1)

    try:
        ftdi = Ftdi()
        ftdi.open_from_url(f"ftdi://::{relay_identifier}/1")
        ftdi.set_bitmode(bitmask=0xFF, mode=Ftdi.BitMode.BITBANG)
        time.sleep(0.05)

        raw = ftdi.read_pins()
        original_states = [(raw >> i) & 1 for i in range(4)]
        original = bool(original_states[channel_idx])

        steps = []

        # Turn OFF
        write_state(ftdi, channel_idx, list(original_states), 0)
        time.sleep(0.15)
        got_off = not read_state(ftdi, channel_idx)
        steps.append(f"→ OFF: {'OK' if got_off else 'FAIL — stuck ON'}")

        # Turn ON
        write_state(ftdi, channel_idx, list(original_states), 1)
        time.sleep(0.15)
        got_on = read_state(ftdi, channel_idx)
        steps.append(f"→ ON:  {'OK' if got_on else 'FAIL — stuck OFF'}")

        # Restore original
        write_state(ftdi, channel_idx, list(original_states), original)
        ftdi.close(freeze=True)

        passed = got_off and got_on
        result.update(
            status="pass" if passed else "fail",
            input=f"Relay: {relay_identifier}  CH{channel_idx}",
            output="  ".join(steps),
            error=None if passed else "Channel not responding to commands",
        )
    except Exception as exc:
        result.update(status="error", error=str(exc))

    print(json.dumps(result))
""")


def run(ssh_host: str, ssh_user: str, identifier: str,
        channel_idx: int = 0, sudo_user: str | None = None) -> TestResult:
    t0 = time.time()
    result = TestResult(
        test_name=f"Relay Toggle CH{channel_idx}",
        device_name=f"Sainsmart {identifier}",
        status="running",
    )
    try:
        script = (
            f"relay_identifier = {identifier!r}\n"
            f"channel_idx = {channel_idx}\n"
            + _RELAY_TOGGLE_SCRIPT
        )
        data = ssh_exec(ssh_host, ssh_user, script,
                        sudo_user=sudo_user, timeout=15)
        result.status = data.get("status", "error")
        result.input_desc = data.get("input", "")
        result.output_desc = data.get("output", "")
        result.error = data.get("error")
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result
