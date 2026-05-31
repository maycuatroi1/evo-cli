from evo_cli.commands.fix_claude import (
    AFFECTED_MAX,
    AFFECTED_MIN,
    autoupdater_disabled,
    is_affected,
    parse_version,
    version_str,
)


def test_parse_version_reads_first_semver():
    assert parse_version("2.1.158 (Claude Code)") == (2, 1, 158)
    assert parse_version("v0.1.11") == (0, 1, 11)


def test_parse_version_returns_none_on_garbage():
    assert parse_version("garbage") is None
    assert parse_version("") is None
    assert parse_version(None) is None


def test_is_affected_covers_the_known_bad_range():
    assert is_affected(AFFECTED_MIN)
    assert is_affected(AFFECTED_MAX)
    assert is_affected((2, 1, 156))


def test_is_affected_excludes_versions_outside_the_range():
    assert not is_affected((2, 1, 153))
    assert not is_affected((2, 1, 159))
    assert not is_affected((3, 0, 0))
    assert not is_affected(None)


def test_version_str_formats_tuple_or_unknown():
    assert version_str((2, 1, 158)) == "2.1.158"
    assert version_str(None) == "unknown"


def test_autoupdater_disabled_checks_env_flag():
    assert autoupdater_disabled({"env": {"DISABLE_AUTOUPDATER": "1"}})
    assert not autoupdater_disabled({"env": {"DISABLE_AUTOUPDATER": "0"}})
    assert not autoupdater_disabled({"env": {}})
    assert not autoupdater_disabled({})
