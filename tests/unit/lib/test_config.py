# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for core config helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from terok.lib.core import config as cfg


@pytest.fixture(autouse=True)
def reset_experimental() -> Iterator[None]:
    """Reset the module-global experimental flag around each test."""
    cfg.set_experimental(False)
    yield
    cfg.set_experimental(False)


def write_config(tmp_path: Path, content: str) -> Path:
    """Write a temporary config file and return its path."""
    path = tmp_path / "config.yml"
    path.write_text(content, encoding="utf-8")
    return path


def test_global_config_search_paths_respects_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yml"
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(config_path))
    assert cfg.global_config_search_paths() == [config_path.expanduser().resolve()]


def test_global_config_path_prefers_xdg(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("TEROK_CONFIG_FILE", raising=False)
    config_file = tmp_path / "terok" / "config.yml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("ui:\n  base_port: 7000\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cfg.global_config_path() == config_file.resolve()


@pytest.mark.parametrize(
    ("env_var", "config_text", "resolver", "expected_name"),
    [
        ("TEROK_STATE_DIR", None, cfg.state_dir, "state"),
        (
            "TEROK_CONFIG_FILE",
            "paths:\n  user_projects_dir: {path}\n",
            cfg.user_projects_dir,
            "projects",
        ),
        (
            "TEROK_CONFIG_FILE",
            "ui:\n  base_port: 8123\ncredentials:\n  dir: {path}\n",
            cfg.credentials_dir,
            "envs",
        ),
    ],
    ids=["state-env", "projects-config", "credentials-config"],
)
def test_path_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_var: str,
    config_text: str | None,
    resolver: Callable[[], Path],
    expected_name: str,
) -> None:
    expected_path = tmp_path / expected_name
    if config_text is None:
        monkeypatch.setenv(env_var, str(expected_path))
    else:
        monkeypatch.setenv(
            env_var, str(write_config(tmp_path, config_text.format(path=expected_path)))
        )
    assert resolver() == expected_path.resolve()


def test_ui_base_port_is_read_from_global_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "ui:\n  base_port: 8123\n")))
    assert cfg.get_ui_base_port() == 8123


@pytest.mark.parametrize(
    ("config_text", "expected"),
    [
        ("tui:\n  default_tmux: true\n", True),
        ("", False),
        ("tui:\n  default_tmux: false\n", False),
    ],
    ids=["true", "default-false", "explicit-false"],
)
def test_tui_default_tmux(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_text: str,
    expected: bool,
) -> None:
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, config_text)))
    assert cfg.get_tui_default_tmux() is expected


def test_experimental_flag_roundtrip() -> None:
    assert not cfg.is_experimental()
    cfg.set_experimental(True)
    assert cfg.is_experimental()
    cfg.set_experimental(False)
    assert not cfg.is_experimental()


def test_get_public_host_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEROK_PUBLIC_HOST", raising=False)
    assert cfg.get_public_host() == "127.0.0.1"


def test_get_public_host_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEROK_PUBLIC_HOST", "myserver.local")
    assert cfg.get_public_host() == "myserver.local"


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ({}, False),
        ({"bypass_firewall_no_protection": True}, True),
        ({"bypass_firewall_no_protection": False}, False),
    ],
    ids=["default-false", "enabled", "explicit-false"],
)
def test_get_shield_bypass_firewall_no_protection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    section: dict[str, bool],
    expected: bool,
) -> None:
    config_text = (
        ""
        if not section
        else f"shield:\n  bypass_firewall_no_protection: {str(section['bypass_firewall_no_protection']).lower()}\n"
    )
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, config_text)))
    assert cfg.get_shield_bypass_firewall_no_protection() is expected


@pytest.mark.parametrize(
    ("config_yaml", "expected_drop", "expected_restart"),
    [
        ("", True, "retain"),
        ("shield:\n  drop_on_task_run: false\n  on_task_restart: up\n", False, "up"),
    ],
    ids=["defaults", "explicit"],
)
def test_get_shield_policy_accessors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_yaml: str,
    expected_drop: bool,
    expected_restart: str,
) -> None:
    """Shield policy accessors read from the global config file."""
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, config_yaml)))
    assert cfg.get_shield_drop_on_task_run() is expected_drop
    assert cfg.get_shield_on_task_restart() == expected_restart


# ---------- Renamed / new path functions ----------


def test_projects_dir_appends_subdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``projects_dir()`` always returns ``<config_root>/projects``."""
    monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path))
    assert cfg.projects_dir() == (tmp_path / "projects").resolve()


def test_state_dir_via_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``state_dir()`` reads TEROK_STATE_DIR."""
    target = tmp_path / "my-state"
    monkeypatch.setenv("TEROK_STATE_DIR", str(target))
    assert cfg.state_dir() == target.resolve()


def test_state_dir_via_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``state_dir()`` honors ``paths.root`` from config as umbrella root."""
    target = tmp_path / "custom-root"
    monkeypatch.delenv("TEROK_STATE_DIR", raising=False)
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, f"paths:\n  root: {target}\n")),
    )
    assert cfg.state_dir() == (target / "core").resolve()


def test_build_dir_via_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``build_dir()`` reads ``paths.build_dir`` from global config."""
    target = tmp_path / "builds"
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, f"paths:\n  build_dir: {target}\n")),
    )
    assert cfg.build_dir() == target.resolve()


def test_build_dir_defaults_under_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``build_dir()`` falls back to ``state_dir()/build``."""
    monkeypatch.setenv("TEROK_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    assert cfg.build_dir() == (tmp_path / "build").resolve()


def test_archive_dir_at_umbrella_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``archive_dir()`` lives at the umbrella state root."""
    monkeypatch.setenv("TEROK_ROOT", str(tmp_path))
    assert cfg.archive_dir() == (tmp_path / "archive").resolve()


def test_sandbox_live_dir_via_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``sandbox_live_dir()`` reads TEROK_SANDBOX_LIVE_DIR."""
    target = tmp_path / "live"
    monkeypatch.setenv("TEROK_SANDBOX_LIVE_DIR", str(target))
    assert cfg.sandbox_live_dir() == target.resolve()


def test_sandbox_live_dir_via_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``sandbox_live_dir()`` reads ``paths.sandbox_live_dir`` from config."""
    target = tmp_path / "custom-live"
    monkeypatch.delenv("TEROK_SANDBOX_LIVE_DIR", raising=False)
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, f"paths:\n  sandbox_live_dir: {target}\n")),
    )
    assert cfg.sandbox_live_dir() == target.resolve()


def test_sandbox_live_dir_defaults_under_umbrella(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``sandbox_live_dir()`` defaults to ``umbrella_root/sandbox-live``."""
    monkeypatch.delenv("TEROK_SANDBOX_LIVE_DIR", raising=False)
    monkeypatch.setenv("TEROK_ROOT", str(tmp_path))
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    assert cfg.sandbox_live_dir() == (tmp_path / "sandbox-live").resolve()


def test_sandbox_live_mounts_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``sandbox_live_mounts_dir()`` appends ``mounts/`` to sandbox-live."""
    monkeypatch.setenv("TEROK_SANDBOX_LIVE_DIR", str(tmp_path / "live"))
    assert cfg.sandbox_live_mounts_dir() == (tmp_path / "live" / "mounts").resolve()


def test_credentials_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``credentials_dir()`` prioritizes TEROK_CREDENTIALS_DIR env var."""
    target = tmp_path / "creds"
    monkeypatch.setenv("TEROK_CREDENTIALS_DIR", str(target))
    assert cfg.credentials_dir() == target.resolve()


def test_credentials_dir_config_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``credentials_dir()`` reads ``credentials.dir`` from config when no env var."""
    target = tmp_path / "config-creds"
    monkeypatch.delenv("TEROK_CREDENTIALS_DIR", raising=False)
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, f"credentials:\n  dir: {target}\n")),
    )
    assert cfg.credentials_dir() == target.resolve()


def test_credentials_dir_env_beats_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Env var wins over config file for ``credentials_dir()``."""
    env_target = tmp_path / "env-creds"
    cfg_target = tmp_path / "cfg-creds"
    monkeypatch.setenv("TEROK_CREDENTIALS_DIR", str(env_target))
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, f"credentials:\n  dir: {cfg_target}\n")),
    )
    assert cfg.credentials_dir() == env_target.resolve()


def test_gate_repos_dir_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``gate_repos_dir()`` falls back to sandbox's ``gate_base_path``."""
    sandbox_state = tmp_path / "sandbox-state"
    monkeypatch.setenv("TEROK_SANDBOX_STATE_DIR", str(sandbox_state))
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    assert cfg.gate_repos_dir() == (sandbox_state / "gate").resolve()


def test_gate_repos_dir_custom(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``gate_repos_dir()`` reads ``gate_server.repos_dir`` from config."""
    target = tmp_path / "custom-gate"
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, f"gate_server:\n  repos_dir: {target}\n")),
    )
    assert cfg.gate_repos_dir() == target.resolve()


def test_user_presets_dir_via_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``user_presets_dir()`` reads ``paths.user_presets_dir`` from config."""
    target = tmp_path / "presets"
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, f"paths:\n  user_presets_dir: {target}\n")),
    )
    assert cfg.user_presets_dir() == target.resolve()


def test_user_projects_dir_via_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``user_projects_dir()`` reads ``paths.user_projects_dir`` from config."""
    target = tmp_path / "projects"
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, f"paths:\n  user_projects_dir: {target}\n")),
    )
    assert cfg.user_projects_dir() == target.resolve()


def test_get_prefix_default() -> None:
    """``get_prefix()`` returns sys.prefix when TEROK_PREFIX is not set."""
    import sys

    result = cfg.get_prefix()
    assert result == Path(sys.prefix).resolve()


def test_get_prefix_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``get_prefix()`` reads TEROK_PREFIX when set."""
    monkeypatch.setenv("TEROK_PREFIX", str(tmp_path))
    assert cfg.get_prefix() == tmp_path.resolve()


# ---------- Validated config accessor coverage ----------


def test_get_global_human_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``get_global_human_name()`` reads from config."""
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, "git:\n  human_name: Jean-Luc Picard\n")),
    )
    assert cfg.get_global_human_name() == "Jean-Luc Picard"


def test_get_global_human_name_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``get_global_human_name()`` returns None when unset."""
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    assert cfg.get_global_human_name() is None


def test_get_global_human_email(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``get_global_human_email()`` reads from config."""
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, "git:\n  human_email: picard@enterprise.fed\n")),
    )
    assert cfg.get_global_human_email() == "picard@enterprise.fed"


def test_get_logs_partial_streaming_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``get_logs_partial_streaming()`` defaults to True."""
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    assert cfg.get_logs_partial_streaming() is True


def test_get_logs_partial_streaming_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``get_logs_partial_streaming()`` reads config."""
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, "logs:\n  partial_streaming: false\n")),
    )
    assert cfg.get_logs_partial_streaming() is False


def test_get_credential_proxy_bypass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``get_credential_proxy_bypass()`` reads from config."""
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, "credential_proxy:\n  bypass_no_secret_protection: true\n")),
    )
    assert cfg.get_credential_proxy_bypass() is True


def test_get_gate_server_port_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``get_gate_server_port()`` defaults to 9418."""
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    assert cfg.get_gate_server_port() == 9418


def test_get_gate_server_suppress_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``get_gate_server_suppress_warning()`` reads from config."""
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, "gate_server:\n  suppress_systemd_warning: true\n")),
    )
    assert cfg.get_gate_server_suppress_warning() is True


# ---------- Validated config error paths ----------


def test_load_validated_returns_defaults_on_malformed_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``_load_validated()`` returns defaults when config is unreadable."""
    bad_file = tmp_path / "config.yml"
    bad_file.write_text("not: {valid: yaml: broken", encoding="utf-8")
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(bad_file))
    # Should not raise — falls back to defaults
    assert cfg.get_ui_base_port() == 7860


def test_load_validated_returns_defaults_on_invalid_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``_load_validated()`` returns defaults when config has invalid schema."""
    bad_file = tmp_path / "config.yml"
    bad_file.write_text("ui:\n  base_port: not-a-number\n", encoding="utf-8")
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(bad_file))
    assert cfg.get_ui_base_port() == 7860


# ---------- make_sandbox_config() factory ----------


def test_make_sandbox_config_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Factory uses sandbox's own state_dir (not terok's)."""
    sandbox_state = tmp_path / "sandbox-state"
    monkeypatch.setenv("TEROK_SANDBOX_STATE_DIR", str(sandbox_state))
    monkeypatch.setenv("TEROK_CREDENTIALS_DIR", str(tmp_path / "creds"))
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    sc = cfg.make_sandbox_config()
    assert sc.state_dir == sandbox_state
    assert sc.credentials_dir == (tmp_path / "creds").resolve()


def test_make_sandbox_config_ssh_keys_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Factory's ssh_keys_dir derives from sandbox's state, not terok's."""
    sandbox_state = tmp_path / "sandbox-state"
    monkeypatch.setenv("TEROK_SANDBOX_STATE_DIR", str(sandbox_state))
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    sc = cfg.make_sandbox_config()
    assert sc.ssh_keys_dir == sandbox_state / "ssh-keys"


def test_make_sandbox_config_from_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Factory propagates credentials_dir from config.yml."""
    target = tmp_path / "cfg-creds"
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, f"credentials:\n  dir: {target}\n")),
    )
    sc = cfg.make_sandbox_config()
    assert sc.credentials_dir == target.resolve()


def test_make_sandbox_config_gate_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Factory propagates gate_server.port from global config."""
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, "gate_server:\n  port: 1234\n")),
    )
    assert cfg.make_sandbox_config().gate_port == 1234


def test_make_sandbox_config_credentials_propagation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Factory's credential-derived properties use terok's credentials_dir."""
    monkeypatch.setenv("TEROK_CREDENTIALS_DIR", str(tmp_path / "creds"))
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    sc = cfg.make_sandbox_config()
    assert sc.proxy_db_path == (tmp_path / "creds" / "credentials.db").resolve()
    assert sc.ssh_keys_json_path == (tmp_path / "creds" / "ssh-keys.json").resolve()


def test_make_sandbox_config_shield_bypass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Factory bridges shield.bypass_firewall_no_protection to SandboxConfig.shield_bypass."""
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, "shield:\n  bypass_firewall_no_protection: true\n")),
    )
    assert cfg.make_sandbox_config().shield_bypass is True


def test_make_sandbox_config_shield_bypass_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Factory defaults shield_bypass to False."""
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    assert cfg.make_sandbox_config().shield_bypass is False


def test_make_sandbox_config_shield_audit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Factory bridges shield.audit to SandboxConfig.shield_audit."""
    monkeypatch.setenv(
        "TEROK_CONFIG_FILE",
        str(write_config(tmp_path, "shield:\n  audit: false\n")),
    )
    assert cfg.make_sandbox_config().shield_audit is False


def test_make_sandbox_config_shield_audit_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Factory defaults shield_audit to True."""
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "")))
    assert cfg.make_sandbox_config().shield_audit is True
