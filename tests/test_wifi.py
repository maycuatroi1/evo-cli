from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import wifi


def test_wifi_registered():
    assert "wifi" in cli.commands


def test_wifi_help_lists_subcommands():
    result = CliRunner().invoke(cli, ["wifi", "--help"])
    assert result.exit_code == 0
    for sub in ("status", "scan", "connect", "disconnect", "on", "off", "saved", "forget", "password"):
        assert sub in result.output


def test_parse_macos_interface():
    text = (
        "Hardware Port: Ethernet\nDevice: en4\nEthernet Address: 00:11\n\n"
        "Hardware Port: Wi-Fi\nDevice: en0\nEthernet Address: 84:2f\n"
    )
    assert wifi.parse_macos_interface(text) == "en0"
    assert wifi.parse_macos_interface("Hardware Port: Ethernet\nDevice: en4\n") is None
    assert wifi.parse_macos_interface("") is None


def test_parse_preferred():
    text = "Preferred networks on en0:\n\tHomeWifi\n\tCafe Net\n"
    assert wifi.parse_preferred(text) == ["HomeWifi", "Cafe Net"]
    assert wifi.parse_preferred("") == []


def test_parse_rssi():
    assert wifi.parse_rssi("-76 dBm / -93 dBm") == -76
    assert wifi.parse_rssi("-45 dBm") == -45
    assert wifi.parse_rssi(None) is None
    assert wifi.parse_rssi("no signal") is None


def test_clean_security():
    assert wifi.clean_security("spairport_security_mode_wpa2_personal_mixed") == "WPA2 Personal Mixed"
    assert wifi.clean_security("spairport_security_mode_wpa3_transition") == "WPA3 Transition"
    assert wifi.clean_security("spairport_security_mode_none") == "Open"
    assert wifi.clean_security(None) is None


def test_is_open():
    assert wifi.is_open("Open") is True
    assert wifi.is_open("") is True
    assert wifi.is_open(None) is True
    assert wifi.is_open("WPA2 Personal") is False


def test_band_of():
    assert wifi.band_of("149 (5GHz, 80MHz)") == "5 GHz"
    assert wifi.band_of("11 (2GHz, 20MHz)") == "2.4 GHz"
    assert wifi.band_of("37 (6GHz, 160MHz)") == "6 GHz"
    assert wifi.band_of(None) is None


def test_nmcli_split_handles_escaped_colons():
    # nmcli escapes colons inside field values with a backslash.
    assert wifi.nmcli_split("yes:My\\:Net:90:WPA2") == ["yes", "My:Net", "90", "WPA2"]
    assert wifi.nmcli_split("a:b:c") == ["a", "b", "c"]


def test_quality_thresholds():
    assert wifi.quality_from_rssi(-40) == "excellent"
    assert wifi.quality_from_rssi(-60) == "good"
    assert wifi.quality_from_rssi(-72) == "fair"
    assert wifi.quality_from_rssi(-85) == "weak"
    assert wifi.quality_from_rssi(None) is None
    assert wifi.quality_from_pct(90) == "excellent"
    assert wifi.quality_from_pct(20) == "weak"


def test_signal_display():
    assert "dBm" in wifi.signal_display({"rssi": -50})
    assert "%" in wifi.signal_display({"rssi": None, "signal": 80})
    assert wifi.signal_display({"rssi": None, "signal": None}) == "[dim]-[/dim]"


def test_sort_networks_strongest_first():
    nets = [
        {"ssid": "weak", "rssi": -80, "signal": None},
        {"ssid": "strong", "rssi": -45, "signal": None},
        {"ssid": "unknown", "rssi": None, "signal": None},
    ]
    ordered = [n["ssid"] for n in wifi.sort_networks(nets)]
    assert ordered == ["strong", "weak", "unknown"]


def test_macos_network_entry_shape():
    node = {
        "_name": "HomeWifi",
        "spairport_signal_noise": "-55 dBm / -90 dBm",
        "spairport_security_mode": "spairport_security_mode_wpa2_personal",
        "spairport_network_channel": "149 (5GHz, 80MHz)",
        "spairport_network_phymode": "802.11ax",
        "spairport_network_rate": 216,
    }
    entry = wifi._macos_network_entry(node, connected=True)
    assert entry["ssid"] == "HomeWifi"
    assert entry["rssi"] == -55
    assert entry["security"] == "WPA2 Personal"
    assert entry["band"] == "5 GHz"
    assert entry["connected"] is True


def test_macos_scan_parses_profiler(monkeypatch):
    prof = {
        "spairport_current_network_information": {
            "_name": "HomeWifi",
            "spairport_signal_noise": "-55 dBm",
            "spairport_security_mode": "spairport_security_mode_wpa2_personal",
            "spairport_network_channel": "149 (5GHz, 80MHz)",
        },
        "spairport_airport_other_local_wireless_networks": [
            {
                "_name": "Cafe",
                "spairport_security_mode": "spairport_security_mode_none",
                "spairport_network_channel": "11 (2GHz, 20MHz)",
            },
            {"_name": "HomeWifi"},  # duplicate of the current network, must be deduped
        ],
    }
    monkeypatch.setattr(wifi, "_macos_profiler_iface", lambda: prof)
    nets = wifi.macos_scan("en0")
    ssids = [n["ssid"] for n in nets]
    assert ssids.count("HomeWifi") == 1
    assert "Cafe" in ssids
    assert nets[0]["ssid"] == "HomeWifi"  # has signal, sorts first
    assert nets[0]["connected"] is True


def test_status_notes_flags_open_and_weak():
    state = {"power": True, "connected": True, "ssid": "Cafe", "security": "Open", "rssi": -85}
    messages = " ".join(m for _, m in wifi.status_notes(state))
    assert "weak" in messages.lower()
    assert "open" in messages.lower()


def test_status_notes_radio_off():
    notes = wifi.status_notes({"power": False, "connected": False})
    assert notes and notes[0][0] == "warning"


def test_status_json_runs(monkeypatch):
    monkeypatch.setattr(wifi, "prepare", lambda: ("Darwin", "en0"))
    monkeypatch.setattr(
        wifi,
        "collect_status",
        lambda iface: {"interface": "en0", "power": True, "connected": True, "ssid": "Home", "rssi": -50},
    )
    result = CliRunner().invoke(cli, ["wifi", "status", "--json"])
    assert result.exit_code == 0
    assert '"ssid"' in result.output
