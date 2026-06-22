from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import sysmon


def test_sysmon_registered():
    assert "sysmon" in cli.commands


def test_sysmon_help_runs():
    result = CliRunner().invoke(cli, ["sysmon", "--help"])
    assert result.exit_code == 0
    for word in ("temperature", "performance", "--watch", "--json"):
        assert word in result.output


def test_fmt_bytes():
    assert sysmon.fmt_bytes(None) == "-"
    assert sysmon.fmt_bytes(512) == "512B"
    assert sysmon.fmt_bytes(1024) == "1.0KB"
    assert sysmon.fmt_bytes(1024**3) == "1.0GB"


def test_fmt_uptime():
    assert sysmon.uptime_seconds is not None
    assert sysmon._fmt_uptime(None) == "-"
    assert sysmon._fmt_uptime(90) == "1m"
    assert sysmon._fmt_uptime(3 * 3600 + 120) == "3h 2m"
    assert sysmon._fmt_uptime(2 * 86400 + 3600) == "2d 1h 0m"


def test_temp_cell_color_thresholds():
    assert "success" in sysmon._temp_cell(40.0)
    assert "warning" in sysmon._temp_cell(75.0)
    assert "error" in sysmon._temp_cell(95.0)
    assert sysmon._temp_cell(None) == "[dim]-[/dim]"


def test_pct_cell_thresholds():
    assert "info" in sysmon._pct_cell(10.0, sysmon.CPU_BUSY)
    assert "error" in sysmon._pct_cell(95.0, sysmon.CPU_BUSY)
    assert sysmon._pct_cell(None, sysmon.CPU_BUSY) == "[dim]-[/dim]"


def test_collect_temps_returns_shape(monkeypatch):
    monkeypatch.setattr(sysmon.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sysmon, "_linux_temps", lambda: [{"label": "x86_pkg_temp", "value": 55.0}])
    data = sysmon.collect_temps()
    assert data["platform"] == "Linux"
    assert data["cpu"] == 55.0
    assert data["entries"][0]["label"] == "x86_pkg_temp"


def test_build_notes_flags_hot_cpu():
    data = {
        "temps": {"platform": "Linux", "cpu": 95.0, "pressure": None, "smctemp_available": None},
        "cpu_percent": 90.0,
        "load": [10.0, 5.0, 2.0],
        "cpu_count": 4,
        "memory": {"percent": 90.0, "used": 1, "total": 2},
        "processes": [{"command": "vm", "cpu": 600.0, "pid": "42", "mem": 5.0}],
    }
    levels = {level for level, _ in sysmon.build_notes(data)}
    messages = " ".join(msg for _, msg in sysmon.build_notes(data))
    assert "warning" in levels
    assert "hot" in messages
    assert "high" in messages


def test_build_notes_all_clear():
    data = {
        "temps": {"platform": "Linux", "cpu": 45.0, "pressure": "Nominal", "smctemp_available": None},
        "cpu_percent": 10.0,
        "load": [0.5, 0.4, 0.3],
        "cpu_count": 8,
        "memory": {"percent": 30.0, "used": 1, "total": 2},
        "processes": [{"command": "idle", "cpu": 1.0, "pid": "1", "mem": 0.1}],
    }
    assert sysmon.build_notes(data) == []


def test_json_output_runs(monkeypatch):
    monkeypatch.setattr(
        sysmon,
        "collect",
        lambda top_n: {"host": "h", "system": "s", "cpu_percent": 5.0, "temps": {"entries": []}},
    )
    result = CliRunner().invoke(cli, ["sysmon", "--json"])
    assert result.exit_code == 0
    assert '"host"' in result.output
