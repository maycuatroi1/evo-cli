from evo_cli.commands.cloudflare import build_config_yaml


def test_build_config_yaml_contains_ingress():
    text = build_config_yaml("abc-123", "dev.example.com", 22, "/etc/cloudflared/abc-123.json")
    assert "tunnel: abc-123" in text
    assert "credentials-file: /etc/cloudflared/abc-123.json" in text
    assert "hostname: dev.example.com" in text
    assert "service: ssh://localhost:22" in text
    assert "service: http_status:404" in text


def test_build_config_yaml_custom_port():
    text = build_config_yaml("id", "h.example.com", 2222, "/p.json")
    assert "ssh://localhost:2222" in text


def test_build_config_yaml_catch_all_is_last():
    text = build_config_yaml("id", "h.example.com", 22, "/p.json")
    lines = [line.strip() for line in text.splitlines() if "service:" in line]
    assert lines[-1] == "- service: http_status:404"
