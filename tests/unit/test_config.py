"""Tests for core/config.py — paths, config loading, env override."""

import tomllib
from pathlib import Path

import pytest

from agentcloak.core.config import (
    AgentcloakConfig,
    Paths,
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
