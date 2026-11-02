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
    if sys.platform == "win32":
        pytest.skip("evo openvpn runs on macOS/Linux only (Unix sockets, fcntl, /tmp)")
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
        "log",
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


def test_config_accepts_access_server_timing():
    # OpenVPN Access Server profiles carry server-poll-timeout, an alias of connect-timeout.
    assert vpn.validate_config(CONFIG + "server-poll-timeout 4\n") == ["vpn.example 1194 udp"]


def test_config_accepts_inline_key_without_parsing_it():
    assert vpn.validate_config(CONFIG + "lport 0\n<key>\nPRIVATE SECRET\n</key>\n") == ["vpn.example 1194 udp"]


@pytest.mark.parametrize(
    "extra,full",
    [
        ("", True),
        ('pull-filter ignore "redirect-gateway"\n', False),
        ("pull-filter ignore redirect\n", False),
        ("route-nopull\n", False),
        ('pull-filter ignore "route"\n', True),
        # The first matching pull-filter wins, and a local redirect-gateway needs no push.
        ('pull-filter accept "redirect-gateway"\npull-filter ignore "redirect-gateway"\n', True),
        ('pull-filter ignore "redirect-gateway"\nredirect-gateway def1\n', True),
        ('<key>\nredirect-gateway def1\n</key>\npull-filter ignore "redirect-gateway"\n', False),
    ],
)
def test_split_tunnel_is_only_what_provably_keeps_the_default_route(extra, full):
    assert vpn.takes_default_route(CONFIG + extra) is full


CERT_ONLY = CONFIG.replace("auth-user-pass\n", "")


@pytest.mark.parametrize(
    "config,expected",
    [
        (CONFIG, True),
        (CERT_ONLY, False),
        (CERT_ONLY + "<key>\nauth-user-pass\n</key>\n", False),
    ],
)
def test_only_auth_user_pass_asks_for_credentials(config, expected):
    assert vpn.uses_credentials(config) is expected


@pytest.mark.parametrize(
    "platform,output,gateway",
    [
        ("darwin", "route to: default\n    gateway: 192.0.2.1\n  interface: en0\n", "192.0.2.1"),
        ("darwin", "route to: default\n    gateway: 10.8.0.1\n  interface: utun4\n", None),
        ("linux", "default via 192.0.2.1 dev wlan0 proto dhcp metric 600\n", "192.0.2.1"),
        ("linux", "default via 10.8.0.1 dev tun0\n", None),
        ("linux", "", None),
    ],
)
def test_default_gateway_is_the_physical_one(monkeypatch, platform, output, gateway):
    monkeypatch.setattr(vpn.sys, "platform", platform)
    monkeypatch.setattr(vpn.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, output, ""))
    assert vpn.default_gateway() == gateway


@pytest.mark.skipif(sys.platform == "win32", reason="the wrapper is a POSIX shell script")
@pytest.mark.parametrize(
    "platform,expected",
    [
        (
            "darwin",
            [
                "route -n delete -host 198.51.100.7",
                "route -n add -host 198.51.100.7 192.0.2.1",
                "openvpn --config $(touch pwned)",
                "route -n delete -host 198.51.100.7",
            ],
        ),
        (
            "linux",
            [
                "ip route replace 198.51.100.7/32 via 192.0.2.1",
                "openvpn --config $(touch pwned)",
                "ip route del 198.51.100.7/32 via 192.0.2.1",
            ],
        ),
    ],
)
def test_split_tunnel_pins_its_server_around_openvpn(tmp_path, monkeypatch, platform, expected):
    monkeypatch.setattr(vpn.sys, "platform", platform)
    monkeypatch.setattr(vpn, "default_gateway", lambda: "192.0.2.1")
    for tool in ("route", "ip"):
        (tmp_path / tool).write_text(f'#!/bin/sh\necho "{tool} $*" >> "$LOG"\n')
        (tmp_path / tool).chmod(0o755)
    fake = "import os,sys; open(os.environ['LOG'],'a').write(' '.join(['openvpn',*sys.argv[1:]])+'\\n'); sys.exit(3)"
    command = vpn.pin_remotes([sys.executable, "-c", fake, "--config", "$(touch pwned)"], ["198.51.100.7 1194 tcp4"])
    log = tmp_path / "log"
    env = {**vpn.os.environ, "PATH": f"{tmp_path}:{vpn.os.environ['PATH']}", "LOG": str(log)}
    # OpenVPN's exit status survives the unpin, and its arguments never reach the shell as code.
    assert subprocess.run(command, cwd=tmp_path, env=env, check=False).returncode == 3
    assert log.read_text().splitlines() == expected
    assert not (tmp_path / "pwned").exists()


def test_no_pin_without_a_physical_gateway_or_address(monkeypatch):
    monkeypatch.setattr(vpn, "default_gateway", lambda: None)
    assert vpn.pin_remotes(["openvpn"], ["198.51.100.7 1194"]) == ["openvpn"]
    monkeypatch.setattr(vpn, "default_gateway", lambda: "192.0.2.1")

    def unresolvable(*_):
        raise vpn.socket.gaierror("no such host")

    monkeypatch.setattr(vpn.socket, "getaddrinfo", unresolvable)
    assert vpn.pin_remotes(["openvpn"], ["vpn.example 1194"]) == ["openvpn"]


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


@pytest.mark.parametrize(
    "line,shown,hidden",
    [
        (">PASSWORD:Verification Failed: 'Auth'", "check password/OTP enrollment", None),
        (">PASSWORD:Verification Failed: 'Auth' ['SAML user does not match']", ": SAML user does not match.", None),
        (
            ">PASSWORD:Verification Failed: 'Auth' ['CRV1:R,E:state-id-secret:dXNlcg==:Enter token PIN']",
            "dynamic challenge, not supported: Enter token PIN",
            "state-id-secret",
        ),
    ],
)
def test_auth_rejection_shows_the_reason_but_not_the_challenge_state(line, shown, hidden):
    with pytest.raises(CredentialError) as exc:
        vpn.auth_commands(line, {})
    assert shown in str(exc.value)
    assert not hidden or (hidden not in str(exc.value) and "dXNlcg==" not in str(exc.value))


def test_auth_token_is_a_notification_not_a_prompt():
    assert vpn.auth_commands(">PASSWORD:Auth-Token:ephemeral-secret", {}) == ""


OTP_URL = "https://otp.example/public/link-secret"


def test_otp_url_is_appended_without_a_challenge_and_scrv1_with_one(monkeypatch):
    codes = iter(["123456", "654321"])
    monkeypatch.setattr(vpn, "fetch_otp", lambda url: url == OTP_URL and next(codes))
    profile = {"username": "u", "password": "1307", "otp_url": OTP_URL}
    # A fresh code at each login: OpenVPN asks again on every restart (--auth-nocache).
    assert 'password "Auth" "1307123456"' in vpn.auth_commands("Need 'Auth' username/password", profile)
    line = ">PASSWORD:Need 'Auth' username/password SC:1,OTP"
    assert "SCRV1:MTMwNw==:NjU0MzIx" in vpn.auth_commands(line, profile)


def test_fetch_otp_checks_the_answer_without_echoing_the_link(monkeypatch):
    import urllib.error

    answers = iter([b"042137\n", b"<html>login</html>", urllib.error.HTTPError(OTP_URL, 403, "no", {}, None)])

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    def urlopen(request, timeout):
        assert request.get_header("User-agent") == "evo-cli" and timeout <= 5
        answer = next(answers)
        if isinstance(answer, BaseException):
            raise answer
        return Response(answer)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert vpn.fetch_otp(OTP_URL) == "042137"
    for _ in range(2):
        with pytest.raises(CredentialError) as exc:
            vpn.fetch_otp(OTP_URL)
        assert "link-secret" not in str(exc.value)
    with pytest.raises(CredentialError) as exc:
        vpn.parse_otp_url("http://otp.example/public/link-secret")
    assert "link-secret" not in str(exc.value)


def test_credentials_otp_url_replaces_totp_and_serves_otp(store, monkeypatch):
    vpn.save_profile("m1", {"config": CONFIG, "totp": vpn.parse_totp(SECRET)})
    prompts = []

    def getpass(prompt):
        prompts.append(prompt)
        return OTP_URL if prompt.startswith("OTP URL") else "1307"

    monkeypatch.setattr("evo_cli.commands.openvpn.getpass.getpass", getpass)
    monkeypatch.setattr(vpn, "fetch_otp", lambda url: "042137")
    runner = CliRunner()
    result = runner.invoke(cli, ["openvpn", "credentials", "m1", "--username", "u", "--otp-url"])
    assert result.exit_code == 0, result.output
    assert prompts[0].startswith("VPN password or PIN, without the OTP")
    assert "link-secret" not in result.output and "1307" not in result.output
    profile = vpn.get_profile("m1")
    assert (profile["password"], profile["otp_url"], "totp" in profile) == ("1307", OTP_URL, False)
    assert runner.invoke(cli, ["openvpn", "otp", "m1"]).output.strip() == "042137"
    listing = runner.invoke(cli, ["openvpn", "list"]).output
    assert "url" in listing and "link-secret" not in listing
    assert runner.invoke(cli, ["openvpn", "credentials", "m1", "--otp", "--otp-url"]).exit_code == 2


def test_worker_pins_the_otp_host_even_for_a_full_tunnel(store, monkeypatch):
    vpn.save_profile("m1", {"config": CONFIG, "username": "u", "password": "1307", "otp_url": OTP_URL})
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    monkeypatch.setattr(vpn.os, "geteuid", lambda: 0)
    monkeypatch.setattr(vpn.shutil, "which", lambda _: "/fake/openvpn")
    pinned = []
    monkeypatch.setattr(vpn, "pin_remotes", lambda command, hosts: pinned.append(hosts) or command)

    def popen(command, **_):
        raise OSError("stop before running anything")

    monkeypatch.setattr(vpn.subprocess, "Popen", popen)
    vpn.worker("m1", 10)
    # Not the full-tunnel server (OpenVPN routes it itself), only the host a reconnect needs for its OTP.
    assert pinned == [["otp.example"]]


@pytest.mark.parametrize(
    "extra,expected",
    [
        ("", None),
        ("cipher AES-256-CBC\n", "AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305:AES-256-CBC"),
        ("cipher AES-256-GCM\n", None),
        ("cipher AES-256-CBC\ndata-ciphers AES-256-GCM\n", None),
    ],
)
def test_a_profile_cipher_is_offered_after_the_defaults(extra, expected):
    assert vpn.data_ciphers(CONFIG + extra) == expected


def test_worker_passes_the_profile_cipher(store, monkeypatch):
    vpn.save_profile("m1", {"config": CERT_ONLY + "cipher AES-256-CBC\n"})
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    monkeypatch.setattr(vpn.os, "geteuid", lambda: 0)
    monkeypatch.setattr(vpn.shutil, "which", lambda _: "/fake/openvpn")
    monkeypatch.setattr(vpn, "pin_remotes", lambda command, hosts: command)
    started = []

    def popen(command, **_):
        started.append(command)
        raise OSError("stop before running anything")

    monkeypatch.setattr(vpn.subprocess, "Popen", popen)
    vpn.worker("m1", 10)
    command = started[0]
    assert command[command.index("--data-ciphers") + 1].endswith(":AES-256-CBC")


SPLIT_DNS_ENV = {
    "dns_server_10_address_1": "10.0.0.1",
    "dns_server_10_resolve_domain_1": "corp.example",
    "dns_server_10_resolve_domain_2": "../evil",
    "dns_server_10_resolve_domain_3": "a b",
    "dns_server_10_resolve_domain_4": "mine.example",
    "dns_server_11_address_1": "10.0.0.2",
    "dns_server_11_resolve_domain_1": "corp.example",
    "dns_server_12_address_1": "10.0.0.3",
    "dns_server_12_port_1": "5353",
    "dns_server_12_resolve_domain_1": "odd.example",
    "dns_server_13_address_1": "10.0.0.4; touch pwned",
    "dns_server_13_resolve_domain_1": "bad.example",
    "dns_server_14_address_1": "10.0.0.5",
}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell hook")
def test_dns_hook_writes_split_dns_and_removes_only_its_own(tmp_path):
    resolver = tmp_path / "resolver"
    resolver.mkdir()
    (resolver / "mine.example").write_text("nameserver 192.0.2.1\n")
    (resolver / "other.example").write_text("# evo openvpn other\nnameserver 192.0.2.2\n")
    script = vpn.DNS_HOOK.format(tag="'# evo openvpn m1'", directory=str(resolver))

    def hook(stage):
        env = {"PATH": "/usr/bin:/bin", **SPLIT_DNS_ENV}
        subprocess.run(["sh", "-c", script, "hook", stage, "utun9", "1420", "init"], env=env, cwd=tmp_path, check=True)

    hook("up")
    files = {path.name: path.read_text() for path in resolver.iterdir()}
    # Both servers of a domain, in server order; no file for a bad name, port, address or a server without domains.
    assert files["corp.example"] == "# evo openvpn m1\nnameserver 10.0.0.1\nnameserver 10.0.0.2\n"
    assert set(files) == {"corp.example", "mine.example", "other.example"}
    assert files["mine.example"] == "nameserver 192.0.2.1\n"
    assert not (tmp_path / "evil").exists() and not (tmp_path / "pwned").exists()
    hook("up")  # a restart replaces its own files instead of appending to them
    assert (resolver / "corp.example").read_text().count("nameserver") == 2
    hook("down")
    assert sorted(path.name for path in resolver.iterdir()) == ["mine.example", "other.example"]


FAKE_HOOKED_OPENVPN = """
import os, shlex, subprocess, sys
resolver = sys.argv[1]
args = sys.argv[2:]
assert not os.path.exists(os.path.join(resolver, "stale.example")), "leftover not cleared before start"
assert args[args.index("--script-security") + 1] == "2"
command = shlex.split(args[args.index("--route-up") + 1]) + ["utun9", "1420", "init"]
subprocess.run(command, env={**os.environ, "dns_server_10_address_1": "10.0.0.1",
                             "dns_server_10_resolve_domain_1": "corp.example"}, check=True)
assert open(os.path.join(resolver, "corp.example")).read().endswith("nameserver 10.0.0.1\\n")
sys.exit(7)  # dies without running --down
"""


def test_split_dns_wrapper_undoes_everything_when_openvpn_dies(store, tmp_path):
    hooks, resolver = tmp_path / "run", tmp_path / "resolver"
    hooks.mkdir()
    resolver.mkdir()
    (resolver / "stale.example").write_text("# evo openvpn m1\nnameserver 10.9.9.9\n")
    command = [sys.executable, "-c", FAKE_HOOKED_OPENVPN, str(resolver), "--script-security", "1", "--verb", "3"]
    wrapped = vpn.split_dns(command, "m1", hooks=hooks, resolver=resolver)
    result = subprocess.run(wrapped, capture_output=True, text=True, check=False)
    assert result.returncode == 7, result.stderr
    assert list(resolver.iterdir()) == [] and list(hooks.iterdir()) == []


@pytest.mark.parametrize("platform,hooked", [("darwin", True), ("linux", False)])
def test_worker_applies_split_dns_on_macos_only(store, monkeypatch, platform, hooked):
    vpn.save_profile("m1", {"config": CERT_ONLY})
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    monkeypatch.setattr(vpn.os, "geteuid", lambda: 0)
    monkeypatch.setattr(vpn.shutil, "which", lambda _: "/fake/openvpn")
    monkeypatch.setattr(vpn.sys, "platform", platform)
    monkeypatch.setattr(vpn, "pin_remotes", lambda command, hosts: command)
    started = []

    def popen(command, **_):
        started.append(command)
        raise OSError("stop before running anything")

    monkeypatch.setattr(vpn.subprocess, "Popen", popen)
    vpn.worker("m1", 10)
    assert ("--route-up" in started[0]) == hooked
    assert started[0][started[0].index("--script-security") + 1] == ("2" if hooked else "1")


@pytest.mark.parametrize(
    "message,expected",
    [
        ("WEB_AUTH::https://vpn.example/a?b=1", "https://vpn.example/a?b=1"),
        ("WEB_AUTH:external:https://vpn.example/a", "https://vpn.example/a"),
        ("OPEN_URL:https://vpn.example/a", "https://vpn.example/a"),
        ("CR_TEXT:E,R:Enter code", None),
        ("some other notice", None),
    ],
)
def test_web_auth_url(message, expected):
    assert vpn.web_auth_url(message) == expected


@pytest.mark.parametrize("url", ["http://vpn.example/a", "file:///etc/passwd", "https://vpn.example/a b", "-a"])
def test_web_auth_url_rejects_anything_but_plain_https(url):
    with pytest.raises(CredentialError):
        vpn.web_auth_url("WEB_AUTH::" + url)


def test_plain_password_escaping_and_no_unrequested_otp():
    text = vpn.auth_commands("Need 'Auth' username/password", {"username": "u", "password": 'p"\\'})
    assert text == 'username "Auth" "u"\npassword "Auth" "p\\"\\\\"\n'
    with pytest.raises(CredentialError):
        vpn.quote("x\nsignal SIGTERM")


def _ps(monkeypatch, output):
    def run(command, **kwargs):
        assert command[0] == "ps"
        if output is None:
            raise OSError("no ps")
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(vpn.subprocess, "run", run)


def test_stale_state_is_not_connected_or_confirmed_stopped(store, monkeypatch):
    import fcntl

    base = str(vpn.session_path("m1"))
    vpn.private_write(Path(base + ".json"), '{"state":"CONNECTED"}')
    _ps(monkeypatch, "")
    with open(base + ".lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert vpn.request("m1")["state"] == "UNKNOWN"  # the worker lives but does not answer
    _ps(monkeypatch, None)
    assert vpn.request("m1")["state"] == "UNKNOWN"
    _ps(monkeypatch, f"  4242 /usr/sbin/openvpn --config {base}.ovpn --verb 3\n  4300 -zsh\n")
    state = vpn.request("m1")
    assert state["state"] == "UNKNOWN" and state["error"].endswith("sudo kill 4242")


def test_dead_worker_is_reaped_and_disconnect_confirms_it(store, monkeypatch):
    import socket

    vpn.save_profile("m1", {"config": CONFIG})
    base = str(vpn.session_path("m1"))
    vpn.private_write(Path(base + ".json"), '{"profile":"m1","state":"CONNECTED","vpn_ip":"10.8.0.2"}')
    # What a SIGKILLed worker leaves: a socket nobody listens on, the management socket and the config.
    with socket.socket(socket.AF_UNIX) as stale:
        stale.bind(base + ".sock")
    for suffix in (".mgmt", ".ovpn", ".lock"):
        Path(base + suffix).touch()
    _ps(monkeypatch, "  4300 -zsh\n")
    result = CliRunner().invoke(cli, ["openvpn", "disconnect", "m1"])
    assert result.exit_code == 0, result.output
    assert "m1 was not running; last error: Worker exited unexpectedly (last state CONNECTED)" in result.output
    assert "m1: stopped" in result.output
    assert not any(Path(base + suffix).exists() for suffix in (".sock", ".mgmt", ".ovpn"))
    assert json.loads(Path(base + ".json").read_text())["state"] == "STOPPED"
    assert vpn.request("m1")["state"] == "STOPPED"  # so connect and import accept the profile again


def test_connect_reports_the_failure_of_a_worker_closing_with_a_request_queued(store, monkeypatch):
    import socket

    vpn.save_profile("m1", {"config": CONFIG, "username": "u", "password": "p"})
    base = str(vpn.session_path("m1"))
    monkeypatch.setattr("evo_cli.commands.openvpn.shutil.which", lambda _: "/fake/openvpn")
    monkeypatch.setattr("evo_cli.commands.openvpn.os.geteuid", lambda: 0)
    closed = threading.Event()

    class ShuttingDown:
        """A worker in its finally: final state published, control socket listening but never accepting."""

        def __init__(self):
            self.stdin = io.BytesIO()
            self.control = socket.socket(socket.AF_UNIX)
            self.control.bind(base + ".sock")
            self.control.listen(2)
            error = "VPN authentication rejected; check password/OTP enrollment before retrying."
            vpn.private_write(Path(base + ".json"), json.dumps({"profile": "m1", "state": "FAILED", "error": error}))
            threading.Timer(0.3, self.close).start()

        def close(self):
            self.control.close()
            Path(base + ".sock").unlink()
            closed.set()

        def poll(self):
            return 1 if closed.is_set() else None

    monkeypatch.setattr("evo_cli.commands.openvpn.subprocess.Popen", lambda command, **_: ShuttingDown())
    result = CliRunner().invoke(cli, ["openvpn", "connect", "m1", "--no-watch"])
    assert result.exit_code == 1 and "VPN authentication rejected" in result.output, result.output
    assert "Expecting value" not in result.output


FAKE_OPENVPN = """
import socket,sys
sys.stdin.readline()
with socket.socket(socket.AF_UNIX) as s:
    s.connect(sys.argv[1])
    stream = s.makefile('rb')

    # Like OpenVPN 2.6: every (re)start with the hold flag set waits for a fresh "hold release".
    def hold():
        s.sendall(b'>HOLD:Waiting for hold release:0\\n')
        seen = set()
        while (line := stream.readline()) != b'hold release\\n':
            assert line, 'hold never released'
            seen.add(line)
        return seen

    def auth():
        s.sendall(b\">PASSWORD:Need 'Auth' username/password SC:1,OTP\\n\")
        assert stream.readline().startswith(b'username ')
        assert b'SCRV1:' in stream.readline()

    seen = hold()
    assert {b'state on\\n', b'log on\\n', b'bytecount 2\\n'} <= seen
    held = b'hold off\\n' not in seen
    if sys.argv[2] == 'webauth':
        # Access Server SAML: no password prompt, a browser sign-in instead.
        s.sendall(b'>STATE:1,AUTH_PENDING,timeout 120,,,,,\\n')
        s.sendall(b'>INFOMSG:WEB_AUTH::https://vpn.example/auth/1\\n')
    else:
        auth()
    if sys.argv[2] == 'fail':
        s.sendall(b\">PASSWORD:Verification Failed: 'Auth'\\n\")
    else:
        s.sendall(b'>PASSWORD:Auth-Token:ephemeral-secret\\n')
        s.sendall(b''.join(b'>LOG:1700000000,I,line %d\\n' % n for n in range(250)))
        s.sendall(b'>BYTECOUNT:1024,2048\\n')
        s.sendall(b'>STATE:1,CONNECTED,SUCCESS,10.8.0.2,192.0.2.1\\n')
        if sys.argv[2] == 'reconnect':
            s.sendall(b'>STATE:2,RECONNECTING,connection-reset,,\\n')
            if held:
                hold()
            auth()  # --auth-nocache: a restart asks again, with a fresh OTP
            s.sendall(b'>STATE:3,CONNECTED,SUCCESS,10.8.0.2,192.0.2.1\\n')
    assert stream.readline() == b'signal SIGTERM\\n'
"""


@pytest.mark.parametrize("mode", ["ok", "fail", "reconnect", "webauth"])
def test_worker_real_sockets_auth_status_shutdown_without_root(store, monkeypatch, mode):
    fail, webauth = mode == "fail", mode == "webauth"
    config = CERT_ONLY if webauth else CONFIG
    vpn.save_profile(
        "m1", {"config": config, "username": "test", "password": "password-secret", "totp": vpn.parse_totp(SECRET)}
    )
    opened = []
    monkeypatch.setattr(vpn, "open_url", opened.append)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not-a-real-sudo-password\n"))
    monkeypatch.setattr(vpn.os, "geteuid", lambda: 0)
    monkeypatch.setattr(vpn.shutil, "which", lambda _: "/fake/openvpn")
    popen = subprocess.Popen

    def start(command, **kwargs):
        assert "password-secret" not in command
        assert "--script-security" in command and "--management-client" in command
        # A soft restart (ping-restart, connection-reset) must reconnect, not exit.
        assert "--remap-usr1" not in command and "--connect-retry-max" not in command
        assert command[command.index("--ping") + 1] == "10"
        # Only a profile without a password announces browser sign-in.
        assert ("IV_SSO" in command) == webauth
        endpoint = command[command.index("--management") + 1]
        return popen([sys.executable, "-c", FAKE_OPENVPN, endpoint, mode], **kwargs)

    monkeypatch.setattr(vpn.subprocess, "Popen", start)
    thread = threading.Thread(target=vpn.worker, args=("m1", 10), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 8
        while thread.is_alive() and time.monotonic() < deadline:
            # No retry on reset or empty replies: request() must read through the worker's shutdown.
            state = vpn.request("m1")
            if state["state"] == "CONNECTED" and (mode != "reconnect" or state.get("reconnects")):
                assert state["vpn_ip"] == "10.8.0.2"
                assert state["since"] and (state["bytes_in"], state["bytes_out"]) == (1024, 2048)
                assert not fail and "auth_url" not in state and "auth_expires" not in state
                if mode == "reconnect":
                    assert state["reconnects"] == 1 and state["last_reason"] == "connection-reset"
                # Only the newest lines are kept, and a reply larger than one read arrives whole.
                log = vpn.request("m1", "log")["log"]
                assert len(log) == 200 and log[-1].endswith("  line 249") and "log" not in vpn.request("m1")
                vpn.request("m1", "disconnect")
                break
            time.sleep(0.05)
        thread.join(5)
        assert not thread.is_alive()
        assert opened == (["https://vpn.example/auth/1"] if webauth else [])
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


def test_worker_lock_is_per_profile(store, monkeypatch):
    import fcntl

    monkeypatch.setattr(sys, "stdin", io.StringIO("\n\n"))
    with open(str(vpn.session_path("m1")) + ".lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        vpn.worker("m1", 10)  # a second worker for a running profile leaves at once
        assert not Path(str(vpn.session_path("m1")) + ".json").exists()
        vpn.worker("m2", 10)  # another profile gets past the lock (and fails: it does not exist)
        assert json.loads(Path(str(vpn.session_path("m2")) + ".json").read_text())["state"] == "FAILED"


def test_connect_stacks_a_split_tunnel_beside_a_full_one_only(store, monkeypatch):
    split = CONFIG + 'pull-filter ignore "redirect-gateway"\n'
    for name, config in (("full", CONFIG), ("full-2", CONFIG), ("split", split)):
        vpn.save_profile(name, {"config": config, "username": "u", "password": "p"})
    monkeypatch.setattr(
        vpn, "request", lambda name, command="status": {"state": "CONNECTED" if name == "full" else "STOPPED"}
    )
    monkeypatch.setattr("evo_cli.commands.openvpn.shutil.which", lambda _: "/fake/openvpn")
    monkeypatch.setattr("evo_cli.commands.openvpn.os.geteuid", lambda: 0)
    started = []

    class Exited:
        def __init__(self):
            self.stdin = io.BytesIO()

        def poll(self):
            return 1

    monkeypatch.setattr(
        "evo_cli.commands.openvpn.subprocess.Popen", lambda command, **_: started.append(command) or Exited()
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["openvpn", "connect", "full-2", "--no-watch"])
    assert result.exit_code != 0 and "both may take the default route" in result.output and not started
    result = runner.invoke(cli, ["openvpn", "connect", "split", "--no-watch"])
    assert "default route" not in result.output and started, result.output


def test_cert_only_profile_connects_and_lists_without_credentials(store, monkeypatch):
    vpn.save_profile("cert", {"config": CERT_ONLY})
    vpn.save_profile("login", {"config": CONFIG})
    monkeypatch.setattr(vpn, "request", lambda name, command="status": {"state": "STOPPED"})
    monkeypatch.setattr("evo_cli.commands.openvpn.shutil.which", lambda _: "/fake/openvpn")
    monkeypatch.setattr("evo_cli.commands.openvpn.os.geteuid", lambda: 0)
    started = []

    class Exited:
        def __init__(self):
            self.stdin = io.BytesIO()

        def poll(self):
            return 1

    monkeypatch.setattr(
        "evo_cli.commands.openvpn.subprocess.Popen", lambda command, **_: started.append(command) or Exited()
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["openvpn", "connect", "login", "--no-watch"])
    assert result.exit_code != 0 and "Missing credentials" in result.output and not started
    result = runner.invoke(cli, ["openvpn", "connect", "cert", "--no-watch"])
    assert "Missing credentials" not in result.output and started, result.output
    rows = {
        line.split()[1]: line.split()[-2] for line in runner.invoke(cli, ["openvpn", "list"]).output.splitlines()[1:3]
    }
    assert rows == {"cert": "cert", "login": "no"}


def test_connect_shows_the_sign_in_url_once_and_waits_for_it(store, monkeypatch):
    vpn.save_profile("sso", {"config": CERT_ONLY})
    pending = {"state": "AUTH_PENDING", "auth_url": "https://vpn.example/auth/1", "auth_expires": time.time() + 120}
    states = iter([{"state": "STOPPED"}, pending, pending])
    monkeypatch.setattr(vpn, "request", lambda name, command="status": next(states, {"state": "CONNECTED"}))
    monkeypatch.setattr("evo_cli.commands.openvpn.shutil.which", lambda _: "/fake/openvpn")
    monkeypatch.setattr("evo_cli.commands.openvpn.os.geteuid", lambda: 0)

    class Running:
        def __init__(self):
            self.stdin = io.BytesIO()

        def poll(self):
            return None

    monkeypatch.setattr("evo_cli.commands.openvpn.subprocess.Popen", lambda command, **_: Running())
    result = CliRunner().invoke(cli, ["openvpn", "connect", "sso", "--no-watch", "--timeout", "10"])
    assert result.exit_code == 0, result.output
    assert result.output.count("https://vpn.example/auth/1") == 1 and "connected" in result.output


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


def test_log_reads_the_live_worker_only(store, monkeypatch):
    vpn.save_profile("m1", {"config": CONFIG})
    replies = {"log": {"state": "CONNECTED", "log": [f"09:38:{n:02}  line {n}" for n in range(50)]}}
    monkeypatch.setattr(vpn, "request", lambda name, command="status": replies.get(command, {"state": "CONNECTED"}))
    result = CliRunner().invoke(cli, ["openvpn", "log", "m1", "-n", "2"])
    assert result.exit_code == 0 and result.output == "09:38:48  line 48\n09:38:49  line 49\n"
    replies.clear()
    result = CliRunner().invoke(cli, ["openvpn", "log", "m1"])
    assert result.exit_code == 1 and "no running worker" in result.output


def test_qr_decoder_is_local_and_output_is_not_printed(monkeypatch):
    monkeypatch.setattr(vpn.shutil, "which", lambda _: "/usr/bin/zbarimg")
    monkeypatch.setattr(
        vpn.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout=f"otpauth://totp/test?secret={SECRET}\n", stderr=""),
    )
    assert vpn.qr_totp("/tmp/qr.png")["secret"] == SECRET.rstrip("=")
