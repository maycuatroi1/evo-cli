import base64
import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from evo_cli import openvpn as vpn
from evo_cli.cli import cli
from evo_cli.credentials.store import CredentialError, get_value

CONFIG = "client\ndev tun\nremote vpn.example 1194 udp\nauth-user-pass\n<ca>\ntest CA\n</ca>\n"
SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("OMELET_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("OMELET_CONFIG", str(tmp_path / "flat.json"))
    # Keep Unix socket paths below the macOS 104-byte limit.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="evpn-", dir="/tmp") as runtime:
        monkeypatch.setenv("EVO_OPENVPN_HOME", runtime)
        yield tmp_path


def test_commands_registered():
    assert set(cli.commands["openvpn"].commands) == {
        "list",
        "import",
        "credentials",
        "otp",
        "connect",
        "status",
        "disconnect",
    }


@pytest.mark.parametrize("stamp,expected", [(59, "94287082"), (1111111109, "07081804"), (20000000000, "65353130")])
def test_rfc6238(stamp, expected):
    settings = {"secret": SECRET, "digits": 8, "period": 30, "algorithm": "SHA1"}
    assert vpn.totp(settings, at=stamp) == expected


@pytest.mark.parametrize("algorithm,length,expected", [("SHA256", 32, "46119246"), ("SHA512", 64, "90693936")])
def test_rfc6238_other_hashes(algorithm, length, expected):
    key = (b"1234567890" * 7)[:length]
    settings = {"secret": base64.b32encode(key).decode(), "digits": 8, "algorithm": algorithm}
    assert vpn.totp(settings, at=59) == expected


def test_parse_totp_parameters():
    parsed = vpn.parse_totp(f"otpauth://totp/test?secret={SECRET}&algorithm=SHA256&digits=8&period=60")
    assert parsed["algorithm"] == "SHA256"
    assert parsed["digits"] == 8
    assert parsed["period"] == 60


@pytest.mark.parametrize(
    "value",
    [
        "not-a-secret",
        "",
        "otpauth://hotp/test?secret=ABC",
        f"otpauth://totp/test?secret={SECRET}&period=0",
        f"otpauth://totp/test?secret={SECRET}&digits=abc",
        f"otpauth://totp/test?secret={SECRET}&secret=ABC",
    ],
)
def test_invalid_otp_never_echoes_secret(value):
    with pytest.raises(CredentialError) as exc:
        vpn.parse_totp(value)
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize(
    "directive",
    [
        "up /tmp/pwn",
        "plugin /tmp/lib.so",
        "config /tmp/other",
        "log /tmp/log",
        "script-security 3",
        "auth-user-pass /tmp/auth",
        "key /tmp/key",
        "daemon",
        "management 0.0.0.0 1234",
        "dev /tmp/device",
        "setenv LD_PRELOAD /tmp/lib.so",
        "<connection>\nremote other\n</connection>",
    ],
)
def test_rejects_unsafe_or_external_config(directive):
    with pytest.raises(CredentialError):
        vpn.validate_config(CONFIG + directive + "\n")


def test_config_accepts_inline_key_without_parsing_it():
    assert vpn.validate_config(CONFIG + "lport 0\n<key>\nPRIVATE SECRET\n</key>\n") == ["vpn.example 1194 udp"]


@pytest.mark.parametrize("name", ["../x", "x.y", "-flag", "x/y", "", "x" * 49])
def test_rejects_ambiguous_profile_names(name):
    with pytest.raises(CredentialError):
        vpn.profile_name(name)


def test_import_two_profiles_and_replace_preserves_credentials(store):
    path = store / "profile.ovpn.txt"
    path.write_text(CONFIG)
    runner = CliRunner()
    for name in ("m1", "fe-ho"):
        assert runner.invoke(cli, ["openvpn", "import", name, str(path)]).exit_code == 0
    vpn.save_profile("fe-ho", {**vpn.get_profile("fe-ho"), "password": "untouched"})
    assert runner.invoke(cli, ["openvpn", "import", "m1", str(path)]).exit_code != 0
    assert runner.invoke(cli, ["openvpn", "import", "m1", str(path), "--replace"]).exit_code == 0
    assert get_value("openvpn.profiles.fe-ho.password") == "untouched"
    assert (store / "store/credentials/infra/openvpn.json").stat().st_mode & 0o777 == 0o600
    assert (store / "flat.json").stat().st_mode & 0o777 == 0o600


def test_credentials_qr_saved_without_output(store, monkeypatch):
    vpn.save_profile("m1", {"config": CONFIG})
    monkeypatch.setattr("evo_cli.commands.openvpn.getpass.getpass", lambda _: "password-secret")
    monkeypatch.setattr(vpn, "qr_totp", lambda _: vpn.parse_totp(SECRET))
    image = store / "qr.png"
    image.touch()
    result = CliRunner().invoke(cli, ["openvpn", "credentials", "m1", "--username", "test", "--qr", str(image)])
    assert result.exit_code == 0, result.output
    assert "password-secret" not in result.output and SECRET not in result.output
    assert get_value("openvpn.profiles.m1.totp.secret") == SECRET.rstrip("=")


def test_list_never_exposes_config_password_or_otp(store):
    vpn.save_profile("m1", {"config": CONFIG, "password": "sensitive", "totp": {"secret": SECRET}})
    result = CliRunner().invoke(cli, ["openvpn", "list"])
    assert result.exit_code == 0
    assert "sensitive" not in result.output and SECRET not in result.output and CONFIG not in result.output


def test_static_challenge_uses_scrv1_and_fresh_totp(monkeypatch):
    codes = iter(["123456", "654321"])
    monkeypatch.setattr(vpn, "totp", lambda _: next(codes))
    profile = {"username": "test", "password": "pass", "totp": {"secret": SECRET}}
    line = ">PASSWORD:Need 'Auth' username/password SC:1,OTP"
    assert "SCRV1:cGFzcw==:MTIzNDU2" in vpn.auth_commands(line, profile)
    assert "SCRV1:cGFzcw==:NjU0MzIx" in vpn.auth_commands(line, profile)


def test_auth_rejection_does_not_retry_or_leak_challenge():
    with pytest.raises(CredentialError) as exc:
        vpn.auth_commands(">PASSWORD:Verification Failed: 'Auth' ['sensitive']", {})
    assert "sensitive" not in str(exc.value)


def test_auth_token_is_a_notification_not_a_prompt():
    assert vpn.auth_commands(">PASSWORD:Auth-Token:ephemeral-secret", {}) == ""


def test_plain_password_escaping_and_no_unrequested_otp():
    text = vpn.auth_commands("Need 'Auth' username/password", {"username": "u", "password": 'p"\\'})
    assert text == 'username "Auth" "u"\npassword "Auth" "p\\"\\\\"\n'
    with pytest.raises(CredentialError):
        vpn.quote("x\nsignal SIGTERM")


def test_stale_state_is_not_connected_or_confirmed_stopped(store):
    path = Path(str(vpn.session_path("m1")) + ".json")
    vpn.private_write(path, '{"state":"CONNECTED"}')
    assert vpn.request("m1")["state"] == "UNKNOWN"


FAKE_OPENVPN = """
import socket,sys
sys.stdin.readline()
with socket.socket(socket.AF_UNIX) as s:
    s.connect(sys.argv[1])
    stream = s.makefile('rb')
    assert stream.readline() == b'state on\\n'
    assert stream.readline() == b'bytecount 2\\n'
    assert stream.readline() == b'hold release\\n'
    s.sendall(b\">PASSWORD:Need 'Auth' username/password SC:1,OTP\\n\")
    assert stream.readline().startswith(b'username ')
    assert b'SCRV1:' in stream.readline()
    if sys.argv[2] == 'fail':
        s.sendall(b\">PASSWORD:Verification Failed: 'Auth'\\n\")
    else:
        s.sendall(b'>PASSWORD:Auth-Token:ephemeral-secret\\n')
        s.sendall(b'>BYTECOUNT:1024,2048\\n')
        s.sendall(b'>STATE:1,CONNECTED,SUCCESS,10.8.0.2,192.0.2.1\\n')
        if sys.argv[2] == 'reconnect':
            s.sendall(b'>STATE:2,RECONNECTING,ping-restart,,\\n')
            s.sendall(b'>STATE:3,CONNECTED,SUCCESS,10.8.0.2,192.0.2.1\\n')
    assert stream.readline() == b'signal SIGTERM\\n'
"""


@pytest.mark.parametrize("mode", ["ok", "fail", "reconnect"])
def test_worker_real_sockets_auth_status_shutdown_without_root(store, monkeypatch, mode):
    fail = mode == "fail"
    vpn.save_profile(
        "m1", {"config": CONFIG, "username": "test", "password": "password-secret", "totp": vpn.parse_totp(SECRET)}
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("not-a-real-sudo-password\n"))
    monkeypatch.setattr(vpn.os, "geteuid", lambda: 0)
    monkeypatch.setattr(vpn.shutil, "which", lambda _: "/fake/openvpn")
    popen = subprocess.Popen

    def start(command, **kwargs):
        assert "password-secret" not in command
        assert "--script-security" in command and "--management-client" in command
        # A soft restart (ping-restart, connection-reset) must reconnect, not exit.
        assert "--remap-usr1" not in command and "--connect-retry-max" not in command
        endpoint = command[command.index("--management") + 1]
        return popen([sys.executable, "-c", FAKE_OPENVPN, endpoint, mode], **kwargs)

    monkeypatch.setattr(vpn.subprocess, "Popen", start)
    thread = threading.Thread(target=vpn.worker, args=("m1", 10), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 8
        while thread.is_alive() and time.monotonic() < deadline:
            try:
                state = vpn.request("m1")
            except (ConnectionResetError, json.JSONDecodeError):
                time.sleep(0.05)
                continue
            if state["state"] == "CONNECTED" and (mode != "reconnect" or state.get("reconnects")):
                assert state["vpn_ip"] == "10.8.0.2"
                assert state["since"] and (state["bytes_in"], state["bytes_out"]) == (1024, 2048)
                assert not fail
                if mode == "reconnect":
                    assert state["reconnects"] == 1 and state["last_reason"] == "ping-restart"
                vpn.request("m1", "disconnect")
                break
            time.sleep(0.05)
        thread.join(5)
        assert not thread.is_alive()
        assert vpn.request("m1")["state"] == "STOPPED"
        base = vpn.session_path("m1")
        state = json.loads(Path(str(base) + ".json").read_text())
        assert state["state"] == ("FAILED" if fail else "STOPPED")
        # The last failure stays visible to status/watch, without resurrecting a worker.
        assert ("error" in vpn.request("m1")) == fail
        assert "password-secret" not in json.dumps(state) and SECRET not in json.dumps(state)
        assert not Path(str(base) + ".ovpn").exists()
        assert not Path(str(base) + ".sock").exists()
    finally:
        if thread.is_alive():
            try:
                vpn.request("m1", "disconnect")
            except OSError:
                pass
            thread.join(5)


def test_pick_profile_by_number_single_or_prompt(store, monkeypatch):
    runner = CliRunner()
    totp = vpn.parse_totp(SECRET)
    vpn.save_profile("solo", {"config": CONFIG, "totp": totp})
    result = runner.invoke(cli, ["openvpn", "status"])
    assert result.exit_code == 0 and '"profile": "solo"' in result.output
    vpn.save_profile("a-work", {"config": CONFIG, "remotes": ["vpn.example 1194 udp"]})
    assert '"profile": "solo"' in runner.invoke(cli, ["openvpn", "status", "2"]).output
    assert runner.invoke(cli, ["openvpn", "status", "4"]).exit_code != 0
    # Scripts without a terminal must name the profile rather than hang on a prompt.
    assert "Several profiles" in runner.invoke(cli, ["openvpn", "status"]).output
    monkeypatch.setattr("evo_cli.commands.openvpn._interactive", lambda: True)
    assert '"profile": "a-work"' in runner.invoke(cli, ["openvpn", "status"], input="1\n").output
    vpn.remember("solo")  # the last connected profile is the prompt default
    assert runner.invoke(cli, ["openvpn", "otp"], input="\n").output.strip().endswith(vpn.totp(totp))
    listing = runner.invoke(cli, ["openvpn", "list"]).output
    assert "a-work" in listing and "vpn.example" in listing


def _watch(store, monkeypatch, states):
    vpn.save_profile("m1", {"config": CONFIG, "remotes": ["vpn.example 1194 udp"]})
    states, notes = iter(states), []

    def request(name, command="status"):
        state = next(states)
        if isinstance(state, BaseException):
            raise state
        return {"profile": name, **state}

    monkeypatch.setattr(vpn, "request", request)
    monkeypatch.setattr("evo_cli.commands.openvpn._notify", notes.append)
    monkeypatch.setattr("evo_cli.commands.openvpn.time.sleep", lambda _: None)
    return CliRunner().invoke(cli, ["openvpn", "status", "m1", "--watch"]), notes


def test_watch_logs_reconnects_and_alerts_when_the_tunnel_drops(store, monkeypatch):
    up = {"state": "CONNECTED", "vpn_ip": "10.8.0.2", "since": time.time() - 90, "bytes_in": 5 << 20, "bytes_out": 2048}
    result, notes = _watch(
        store,
        monkeypatch,
        [
            up,
            ConnectionResetError(),
            {"state": "RECONNECTING", "reconnects": 1, "last_reason": "ping-restart"},
            {**up, "reconnects": 1, "last_reason": "ping-restart"},
            {"state": "STOPPED", "error": "OpenVPN exited (auth-failure); check authentication."},
        ],
    )
    assert result.exit_code == 1
    assert "RECONNECTING  ping-restart" in result.output and "auth-failure" in result.output
    assert notes == [
        "m1 dropped (ping-restart); reconnecting",
        "m1 reconnected",
        "m1 disconnected: OpenVPN exited (auth-failure); check authentication.",
    ]


def test_watch_clean_stop_and_ctrl_c_leave_no_alert(store, monkeypatch):
    result, notes = _watch(store, monkeypatch, [{"state": "CONNECTED", "vpn_ip": "10.8.0.2"}, {"state": "STOPPED"}])
    assert result.exit_code == 0 and "m1: stopped." in result.output and notes == []
    result, notes = _watch(store, monkeypatch, [{"state": "CONNECTED", "vpn_ip": "10.8.0.2"}, KeyboardInterrupt()])
    assert result.exit_code == 0 and "keeps running in the background" in result.output and notes == []


def test_qr_decoder_is_local_and_output_is_not_printed(monkeypatch):
    monkeypatch.setattr(vpn.shutil, "which", lambda _: "/usr/bin/zbarimg")
    monkeypatch.setattr(
        vpn.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=f"otpauth://totp/test?secret={SECRET}\n", stderr=""),
    )
    assert vpn.qr_totp("/tmp/qr.png")["secret"] == SECRET.rstrip("=")
