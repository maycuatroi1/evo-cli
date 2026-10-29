import json
import socket
import struct
import subprocess
import threading

import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands.route import _dns, _dpi, _hosts, _probe, _spoofdpi


def test_route_registered():
    assert "route" in cli.commands


def test_route_subcommands_registered():
    route = cli.commands["route"]
    for name in ("check", "probe", "open", "close", "status"):
        assert name in route.commands


def test_route_help_runs():
    result = CliRunner().invoke(cli, ["route", "--help"])
    assert result.exit_code == 0
    for word in ("poisoned", "DPI", "check", "probe"):
        assert word in result.output


def test_every_subcommand_help_runs():
    for name in ("check", "probe", "open", "close", "status"):
        result = CliRunner().invoke(cli, ["route", name, "--help"])
        assert result.exit_code == 0, name


def test_is_bogus():
    assert _dns.is_bogus("127.0.0.1")
    assert _dns.is_bogus("0.0.0.0")
    assert not _dns.is_bogus("162.159.152.4")
    assert not _dns.is_bogus("203.0.113.7")


def test_encode_qname_round_trip():
    encoded = _dns._encode_qname("store.steampowered.com")
    assert encoded.startswith(b"\x05store")
    assert encoded.endswith(b"\x00")
    assert _dns._skip_name(encoded, 0) == len(encoded)


def test_query_a_parses_an_answer(monkeypatch):
    host = "example.com"
    qname = _dns._encode_qname(host)
    header = struct.pack(">HHHHHH", 0x4242, 0x8180, 1, 1, 0, 0)
    question = qname + struct.pack(">HH", 1, 1)
    answer = qname + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton("93.184.216.34")
    packet = header + question + answer

    class FakeSocket:
        def __init__(self, *a, **kw):
            pass

        def settimeout(self, _):
            pass

        def sendto(self, *_):
            pass

        def recvfrom(self, _):
            return packet, ("8.8.8.8", 53)

        def close(self):
            pass

    monkeypatch.setattr(_dns.socket, "socket", FakeSocket)
    assert _dns.query_a(host, "8.8.8.8") == ["93.184.216.34"]


def test_trusted_union_drops_bogus_and_untrusted():
    answers = {
        "google": ["1.2.3.4"],
        "doh": ["1.2.3.4", "5.6.7.8"],
        "vnpt": ["127.0.0.1", "9.9.9.9"],
        "cloudflare": ["127.0.0.1"],
    }
    trusted = _dns._trusted_union(answers)
    assert trusted == ["1.2.3.4", "5.6.7.8"]


def _probe_row(ip, tcp=True, tls=False):
    return {"ip": ip, "sni": "x", "tcp": tcp, "tls": tls, "ms": 1.0 if tls else None, "error": None, "reset": not tls}


def test_verdict_open_when_a_probe_completes():
    probes = [_probe_row("1.2.3.4", tls=True)]
    assert _probe._verdict(["1.2.3.4"], probes, probes, None, False) == _probe.OPEN


def test_verdict_dns_poisoned_when_route_works_but_answer_lied():
    probes = [_probe_row("1.2.3.4", tls=True)]
    assert _probe._verdict(["1.2.3.4"], probes, probes, None, True) == _probe.DNS_POISONED


def test_verdict_sni_blocked_when_control_name_gets_through():
    probes = [_probe_row("1.2.3.4")]
    control = _probe_row("1.2.3.4", tls=True)
    assert _probe._verdict(["1.2.3.4"], probes, [], control, False) == _probe.SNI_BLOCKED


def test_verdict_ip_blocked_when_control_name_fails_too():
    probes = [_probe_row("1.2.3.4")]
    control = _probe_row("1.2.3.4")
    assert _probe._verdict(["1.2.3.4"], probes, [], control, False) == _probe.IP_BLOCKED


def test_verdict_unreachable_without_candidates():
    assert _probe._verdict([], [], [], None, False) == _probe.UNREACHABLE


def test_verdict_unreachable_when_tcp_never_opens():
    probes = [_probe_row("1.2.3.4", tcp=False)]
    control = _probe_row("1.2.3.4", tcp=False)
    assert _probe._verdict(["1.2.3.4"], probes, [], control, False) == _probe.UNREACHABLE


HOSTS_SAMPLE = "127.0.0.1 localhost\n192.168.1.9 nas.local\n"


def test_pin_and_unpin_leave_the_rest_of_hosts_alone(tmp_path):
    path = tmp_path / "hosts"
    path.write_text(HOSTS_SAMPLE, encoding="utf-8")

    _hosts.pin("medium.com", "1.2.3.4", path=path, make_backup=False)
    body = path.read_text(encoding="utf-8")
    assert "127.0.0.1 localhost" in body
    assert "192.168.1.9 nas.local" in body
    assert _hosts.read_block(path) == {"medium.com": "1.2.3.4"}

    _hosts.pin("steamcommunity.com", "5.6.7.8", path=path, make_backup=False)
    assert _hosts.read_block(path) == {"medium.com": "1.2.3.4", "steamcommunity.com": "5.6.7.8"}

    _, removed = _hosts.unpin("medium.com", path=path, make_backup=False)
    assert removed
    assert _hosts.read_block(path) == {"steamcommunity.com": "5.6.7.8"}


def test_pinning_twice_does_not_stack_blocks(tmp_path):
    path = tmp_path / "hosts"
    path.write_text(HOSTS_SAMPLE, encoding="utf-8")
    _hosts.pin("a.com", "1.1.1.1", path=path, make_backup=False)
    _hosts.pin("a.com", "2.2.2.2", path=path, make_backup=False)
    body = path.read_text(encoding="utf-8")
    assert body.count(_hosts.MARK_START) == 1
    assert _hosts.read_block(path) == {"a.com": "2.2.2.2"}


def test_clear_removes_the_block_entirely(tmp_path):
    path = tmp_path / "hosts"
    path.write_text(HOSTS_SAMPLE, encoding="utf-8")
    _hosts.pin("a.com", "1.1.1.1", path=path, make_backup=False)
    _hosts.clear(path=path, make_backup=False)
    body = path.read_text(encoding="utf-8")
    assert _hosts.MARK_START not in body
    assert body.strip() == HOSTS_SAMPLE.strip()
    assert _hosts.read_block(path) == {}


def test_unpin_a_host_we_never_pinned_is_a_no_op(tmp_path):
    path = tmp_path / "hosts"
    path.write_text(HOSTS_SAMPLE, encoding="utf-8")
    _, removed = _hosts.unpin("nothing.com", path=path, make_backup=False)
    assert not removed


def test_dpi_args_never_carry_max_payload():
    """--max-payload defaults to 1200 bytes and skips a modern Chrome ClientHello.

    That is the whole reason we spell the flags out instead of using the
    bundled `-5` preset: with the ceiling on, curl gets through and Chrome does
    not, which reads like anything but a fragmentation setting.
    """
    assert "--max-payload" not in _dpi.DEFAULT_ARGS
    assert "-5" not in _dpi.DEFAULT_ARGS
    assert "--reverse-frag" in _dpi.DEFAULT_ARGS


def test_verify_download_rejects_the_wrong_size(tmp_path):
    blob = tmp_path / "goodbyedpi.zip"
    blob.write_bytes(b"not the real archive")
    ok, reason = _dpi.verify_download(blob)
    assert not ok
    assert "size mismatch" in reason


def _fake_networksetup(proxies, calls):
    """A networksetup stand-in: ``proxies`` maps (service, kind) to (enabled, server, port)."""

    def run(*args):
        calls.append(args)
        out = ""
        if args[0] == "-listallnetworkservices":
            out = "An asterisk (*) denotes that a network service is disabled.\nWi-Fi\n*Old VPN\nUSB LAN\n"
        elif args[0].startswith("-get"):
            enabled, server, port = proxies.get((args[1], args[0][4:]), (False, "", "0"))
            out = f"Enabled: {'Yes' if enabled else 'No'}\nServer: {server}\nPort: {port}\n"
        return subprocess.CompletedProcess(args, 0, out, "")

    return run


def test_spoofdpi_host_list_round_trips_with_subdomains(tmp_path, monkeypatch):
    monkeypatch.setenv("EVO_ROUTE_HOME", str(tmp_path))
    _spoofdpi._write_hosts(["medium.com", "x.com", "medium.com"])
    assert (tmp_path / "hosts.txt").read_text() == "medium.com\n*.medium.com\nx.com\n*.x.com\n"
    assert _spoofdpi.opened() == ["medium.com", "x.com"]


def test_spoofdpi_config_bypasses_only_the_opened_hosts():
    """Every other name goes straight through, so names only a VPN resolves keep working."""
    assert 'mode = "system"' in _spoofdpi.CONFIG
    assert "skip = true" in _spoofdpi.CONFIG
    assert '"file:hosts.txt"' in _spoofdpi.CONFIG
    assert 'split-mode = "first-byte"' in _spoofdpi.CONFIG


def test_network_services_skip_the_header_and_disabled_ones(monkeypatch):
    monkeypatch.setattr(_spoofdpi, "_networksetup", _fake_networksetup({}, []))
    assert _spoofdpi.network_services() == ["Wi-Fi", "USB LAN"]


def test_ensure_refuses_to_replace_a_proxy_the_user_set(tmp_path, monkeypatch):
    monkeypatch.setenv("EVO_ROUTE_HOME", str(tmp_path))
    proxies = {("Wi-Fi", "securewebproxy"): (True, "10.0.0.1", "3128")}
    monkeypatch.setattr(_spoofdpi, "_networksetup", _fake_networksetup(proxies, []))
    with pytest.raises(RuntimeError, match=r"Wi-Fi \(10.0.0.1:3128\)"):
        _spoofdpi.ensure("medium.com")
    assert not (tmp_path / "hosts.txt").exists()


def test_clear_system_proxy_turns_off_only_ours(monkeypatch):
    ours = (True, _spoofdpi.LISTEN_HOST, str(_spoofdpi.LISTEN_PORT))
    proxies = {
        ("Wi-Fi", "webproxy"): ours,
        ("Wi-Fi", "securewebproxy"): ours,
        ("USB LAN", "webproxy"): (True, "10.0.0.1", "3128"),
    }
    calls = []
    monkeypatch.setattr(_spoofdpi, "_networksetup", _fake_networksetup(proxies, calls))
    assert _spoofdpi.clear_system_proxy() == ["Wi-Fi"]
    offs = [call for call in calls if call[0].endswith("state")]
    assert offs == [("-setwebproxystate", "Wi-Fi", "off"), ("-setsecurewebproxystate", "Wi-Fi", "off")]


def test_forgetting_the_last_host_removes_the_bypass(tmp_path, monkeypatch):
    monkeypatch.setenv("EVO_ROUTE_HOME", str(tmp_path))
    _spoofdpi._write_hosts(["medium.com"])
    removed = []
    monkeypatch.setattr(_spoofdpi, "remove", lambda: removed.append(True) or True)
    _spoofdpi.forget("medium.com")
    assert removed == [True]


def test_forgetting_one_host_restarts_with_the_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("EVO_ROUTE_HOME", str(tmp_path))
    _spoofdpi._write_hosts(["medium.com", "x.com"])
    starts = []
    monkeypatch.setattr(_spoofdpi, "_start", lambda reload: starts.append(reload) or True)
    _spoofdpi.forget("medium.com")
    assert _spoofdpi.opened() == ["x.com"]
    assert starts == [False]


def _one_reply_proxy(reply):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        with conn:
            conn.recv(4096)
            conn.sendall(reply)
        server.close()

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()


def test_proxy_probe_reports_a_refused_connect():
    proxy = _one_reply_proxy(b"HTTP/1.1 403 Forbidden\r\n\r\n")
    row = _probe.proxy_probe("medium.com", proxy, timeout=2)
    assert not row["tcp"] and not row["tls"]
    assert "403" in row["error"]


def test_open_on_linux_names_the_supported_systems(monkeypatch):
    monkeypatch.setattr(_dpi, "is_macos", lambda: False)
    monkeypatch.setattr(_dpi, "is_windows", lambda: False)
    monkeypatch.setattr(_probe, "diagnose", lambda host, timeout: {"host": host, "verdict": _probe.SNI_BLOCKED})
    result = CliRunner().invoke(cli, ["route", "open", "medium.com"])
    assert result.exit_code == 1
    assert "Windows and macOS" in result.output


def _profile(support, browser, profile, settings, extensions=None):
    root = support / browser / profile
    root.mkdir(parents=True)
    (root / "Secure Preferences").write_text(json.dumps({"extensions": {"settings": settings}}))
    for ext_id, name in (extensions or {}).items():
        version = root / "Extensions" / ext_id / "1.0_0"
        (version / "_locales" / "en").mkdir(parents=True)
        # Chrome writes manifests with a BOM, and most names are localised.
        (version / "manifest.json").write_text(
            "\ufeff" + json.dumps({"name": "__MSG_extName__", "default_locale": "en"})
        )
        (version / "_locales" / "en" / "messages.json").write_text(json.dumps({"extname": {"message": name}}))


def test_browser_overrides_name_enabled_proxy_extensions_only(tmp_path):
    """A VPN extension left at `direct` still hides the system proxy from Chrome."""
    _profile(
        tmp_path,
        "Google/Chrome",
        "Default",
        {
            "vpn": {"preferences": {"proxy": {"mode": "direct"}}},
            "off": {"preferences": {"proxy": {"mode": "direct"}}, "disable_reasons": [1]},
            "plain": {"preferences": {}},
        },
        {"vpn": "Windscribe", "off": "Decodo"},
    )
    _profile(tmp_path, "BraveSoftware/Brave-Browser", "Profile 1", {"x": {"preferences": {"proxy": {}}}})
    assert _spoofdpi.browser_overrides(tmp_path) == ["Chrome (Default): Windscribe", "Brave-Browser (Profile 1): x"]
