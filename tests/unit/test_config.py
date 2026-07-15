"""Tests for core/config.py — paths, config loading, env override."""

import tomllib
from pathlib import Path

import pytest

from agentcloak.core.config import (
    AgentcloakConfig,
    ConfigError,
    Paths,
    apply_profile_config,
    dump_config,
    load_config,
    write_example_config,
)


class TestPaths:
    def test_derived_paths(self, tmp_path: Path) -> None:
        p = Paths(root=tmp_path)
        assert p.config_file == tmp_path / "config.toml"
        assert p.profiles_dir == tmp_path / "profiles"
        assert p.logs_dir == tmp_path / "logs"
        assert p.active_session_file == tmp_path / "active-session.json"

    def test_ensure_dirs_creates_structure(self, tmp_path: Path) -> None:
        root = tmp_path / "agentcloak_test"
        p = Paths(root=root)
        p.ensure_dirs()
        assert root.is_dir()
        assert p.profiles_dir.is_dir()
        assert p.logs_dir.is_dir()


class TestDefaults:
    def test_default_config_values(self) -> None:
        cfg = AgentcloakConfig()
        assert cfg.daemon.host == "127.0.0.1"
        assert cfg.daemon.port == 18765
        assert cfg.browser.default_tier == "auto"
        assert cfg.browser.default_profile == ""
        assert cfg.browser.viewport_width == 1280
        assert cfg.browser.viewport_height == 720
        assert cfg.browser.navigation_timeout == 30
        assert cfg.browser.screenshot_format == "jpeg"
        assert cfg.security.domain_whitelist == []
        assert cfg.security.content_scan is False


class TestLoadConfig:
    def test_loads_defaults_when_no_file(self, tmp_path: Path) -> None:
        paths, cfg = load_config(root=tmp_path)
        assert paths.root == tmp_path
        assert cfg.daemon.port == 18765

    def test_reads_toml_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[daemon]\nport = 8888\n[browser]\ndefault_tier = "cloak"\n'
        )
        _, cfg = load_config(root=tmp_path)
        assert cfg.daemon.port == 8888
        assert cfg.browser.default_tier == "cloak"

    def test_env_overrides_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text("[daemon]\nport = 8888\n")
        monkeypatch.setenv("AGENTCLOAK_PORT", "7777")
        _, cfg = load_config(root=tmp_path)
        assert cfg.daemon.port == 7777

    def test_env_domain_whitelist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENTCLOAK_DOMAIN_WHITELIST", "example.com, test.org")
        _, cfg = load_config(root=tmp_path)
        assert cfg.security.domain_whitelist == ["example.com", "test.org"]

    def test_screenshot_format_file_and_env_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "config.toml").write_text('[browser]\nscreenshot_format = "png"\n')
        _, cfg = load_config(root=tmp_path)
        assert cfg.browser.screenshot_format == "png"

        monkeypatch.setenv("AGENTCLOAK_SCREENSHOT_FORMAT", "jpeg")
        _, cfg = load_config(root=tmp_path)
        assert cfg.browser.screenshot_format == "jpeg"

    def test_rejects_unsupported_screenshot_format(self, tmp_path: Path) -> None:
        (tmp_path / "config.toml").write_text('[browser]\nscreenshot_format = "webp"\n')

        with pytest.raises(ConfigError, match=r"browser\.screenshot_format"):
            load_config(root=tmp_path)


class TestWriteExampleConfig:
    def test_writes_example_with_all_sections(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        example_path = write_example_config(paths)
        assert example_path == tmp_path / "config.example.toml"
        assert example_path.is_file()
        # Should be parseable TOML — the doc must be a real config file
        # the user can copy values from.
        data = tomllib.loads(example_path.read_text(encoding="utf-8"))
        assert "daemon" in data
        assert "browser" in data
        assert "security" in data
        assert "bridge" in data

    def test_example_defaults_match_dataclass(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        write_example_config(paths)
        data = tomllib.loads(
            (tmp_path / "config.example.toml").read_text(encoding="utf-8")
        )
        defaults = AgentcloakConfig()
        assert data["daemon"]["port"] == defaults.daemon.port
        assert data["browser"]["headless"] is defaults.browser.headless
        assert data["browser"]["viewport_width"] == defaults.browser.viewport_width
        assert (
            data["browser"]["screenshot_format"] == defaults.browser.screenshot_format
        )
        assert (
            data["bridge"]["local_idle_timeout"] == defaults.bridge.local_idle_timeout
        )

    def test_overwrites_existing_example(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        example = tmp_path / "config.example.toml"
        paths.ensure_dirs()
        example.write_text("# stale content\n")
        write_example_config(paths)
        assert "stale content" not in example.read_text(encoding="utf-8")

    def test_never_touches_real_config_toml(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        paths.ensure_dirs()
        real_config = tmp_path / "config.toml"
        real_config.write_text("[daemon]\nport = 9999\n")
        write_example_config(paths)
        # User's real config is untouched
        assert real_config.read_text() == "[daemon]\nport = 9999\n"


class TestDumpConfig:
    """``cloak config list`` uses dotted key names matching ``config get/set``.

    Phase 6d split ``AgentcloakConfig`` into nested sub-configs; ``dump_config``
    now returns dotted keys (``section.field``) so users can copy-paste from
    ``config list`` output directly into ``config get/set`` commands.
    """

    def test_returns_dotted_key_names(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        cfg = AgentcloakConfig()
        result = dump_config(cfg, paths)
        assert "daemon.port" in result
        assert "browser.default_tier" in result
        assert "security.domain_whitelist" in result
        assert "bridge.token" in result
        assert "bridge.local_idle_timeout" in result

    def test_values_pulled_from_subconfig(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        cfg = AgentcloakConfig()
        cfg.daemon.port = 12345
        cfg.browser.headless = False
        cfg.security.content_scan = True
        result = dump_config(cfg, paths)
        assert result["daemon.port"]["value"] == 12345
        assert result["browser.headless"]["value"] is False
        assert result["security.content_scan"]["value"] is True

    def test_source_marked_default_when_no_toml(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        result = dump_config(AgentcloakConfig(), paths)
        assert result["daemon.port"]["source"] == "default"

    def test_source_marked_config_toml_when_present(self, tmp_path: Path) -> None:
        paths = Paths(root=tmp_path)
        paths.ensure_dirs()
        paths.config_file.write_text("[daemon]\nport = 9001\n")
        _, cfg = load_config(root=tmp_path)
        result = dump_config(cfg, paths)
        assert result["daemon.port"]["source"] == "config.toml"
        assert result["daemon.port"]["value"] == 9001

    def test_env_source_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        paths = Paths(root=tmp_path)
        paths.ensure_dirs()
        paths.config_file.write_text("[daemon]\nport = 9001\n")
        monkeypatch.setenv("AGENTCLOAK_PORT", "7777")
        _, cfg = load_config(root=tmp_path)
        result = dump_config(cfg, paths)
        assert result["daemon.port"]["source"] == "env:AGENTCLOAK_PORT"
        assert result["daemon.port"]["value"] == 7777


class TestApplyProfileConfig:
    """Per-profile config overlay — bool/list coercion, typos, validation."""

    def test_no_config_file_is_noop(self, tmp_path: Path) -> None:
        cfg = AgentcloakConfig()
        original_width = cfg.browser.viewport_width
        apply_profile_config(cfg, tmp_path)
        assert cfg.browser.viewport_width == original_width

    def test_valid_browser_overlay_applied(self, tmp_path: Path) -> None:
        (tmp_path / "config.toml").write_text(
            "[browser]\nviewport_width = 1920\nheadless = false\n"
        )
        cfg = AgentcloakConfig()
        apply_profile_config(cfg, tmp_path)
        assert cfg.browser.viewport_width == 1920
        assert cfg.browser.headless is False

    def test_valid_security_overlay_applied(self, tmp_path: Path) -> None:
        (tmp_path / "config.toml").write_text(
            '[security]\ndomain_whitelist = ["example.com", "trusted.io"]\n'
        )
        cfg = AgentcloakConfig()
        apply_profile_config(cfg, tmp_path)
        assert cfg.security.domain_whitelist == ["example.com", "trusted.io"]

    def test_daemon_section_ignored(self, tmp_path: Path) -> None:
        """Profile config must not affect daemon-scoped settings."""
        (tmp_path / "config.toml").write_text("[daemon]\nport = 9999\n")
        cfg = AgentcloakConfig()
        original_port = cfg.daemon.port
        apply_profile_config(cfg, tmp_path)
        assert cfg.daemon.port == original_port

    def test_unknown_key_ignored_and_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A typo like ``headles = true`` must not silently no-op — log it."""
        (tmp_path / "config.toml").write_text("[browser]\nheadles = true\n")
        cfg = AgentcloakConfig()
        with caplog.at_level("WARNING"):
            apply_profile_config(cfg, tmp_path)
        assert cfg.browser.headless is True  # default preserved
        assert any(
            "profile_config_unknown_key" in rec.message for rec in caplog.records
        )

    def test_string_bool_rejected_correctly(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``bool("false")`` in Python is ``True``; overlay must handle strings."""
        (tmp_path / "config.toml").write_text('[browser]\nheadless = "false"\n')
        cfg = AgentcloakConfig()
        apply_profile_config(cfg, tmp_path)
        # String "false" must be coerced to Python False, not True.
        assert cfg.browser.headless is False

    def test_string_list_rejected_not_char_exploded(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``list("--flag")`` would produce per-char list — must be skipped."""
        (tmp_path / "config.toml").write_text('[browser]\nextra_args = "--flag"\n')
        cfg = AgentcloakConfig()
        with caplog.at_level("WARNING"):
            apply_profile_config(cfg, tmp_path)
        # Default (empty list) preserved, not char-exploded per-character.
        assert cfg.browser.extra_args == []
        assert any(
            "profile_config_type_mismatch" in rec.message for rec in caplog.records
        )

    def test_invalid_int_string_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Garbage int value must not crash daemon startup."""
        (tmp_path / "config.toml").write_text(
            '[browser]\nviewport_width = "not_a_number"\n'
        )
        cfg = AgentcloakConfig()
        default_width = cfg.browser.viewport_width
        with caplog.at_level("WARNING"):
            apply_profile_config(cfg, tmp_path)
        assert cfg.browser.viewport_width == default_width

    def test_validation_failure_rolls_back(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Invalid tier passes coercion but fails _validate — must roll back."""
        (tmp_path / "config.toml").write_text(
            '[browser]\ndefault_tier = "banana"\nviewport_width = 1920\n'
        )
        cfg = AgentcloakConfig()
        with caplog.at_level("WARNING"):
            apply_profile_config(cfg, tmp_path)
        # Rollback: no field survives when validation fails.
        assert cfg.browser.default_tier == "auto"
        assert cfg.browser.viewport_width == 1280
        assert any(
            "profile_config_validation_failed" in rec.message for rec in caplog.records
        )
