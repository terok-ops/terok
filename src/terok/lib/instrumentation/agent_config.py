# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Agent config resolution: layered merging across global, project, preset, and CLI scopes.

Builds a :class:`~terok.lib.util.config_stack.ConfigStack` from up to four
layers and returns a single merged agent-config dict that can be fed directly
into :func:`~terok.lib.instrumentation.agents.prepare_agent_config_dir`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from terok.lib.core.config import bundled_presets_dir, get_global_agent_config, global_presets_dir
from terok.lib.util.config_stack import ConfigScope, ConfigStack


def _preset_scope_label(preset_path: Path) -> str:
    """Return a scope label based on where the preset was found."""
    resolved = preset_path.resolve()
    for directory, label in (
        (bundled_presets_dir(), "preset (bundled)"),
        (global_presets_dir(), "preset (global)"),
    ):
        try:
            if resolved.is_relative_to(directory.resolve()):
                return label
        except (ValueError, OSError):
            continue
    return "preset (project)"


def build_agent_config_stack(
    project_id: str,
    *,
    agent_config: dict[str, Any] | None = None,
    project_root: Path | None = None,
    preset: str | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> ConfigStack:
    """Build config stack: global → project → preset → CLI overrides.

    Args:
        project_id: Project identifier (needed for preset resolution).
        agent_config: Project-level agent config dict (from ``project.agent_config``).
        project_root: Project root path (for provenance display).
        preset: Optional preset name.
        cli_overrides: CLI-level overrides (highest priority).

    Returns the :class:`ConfigStack` so callers can either ``.resolve()`` it
    for the merged dict or inspect ``.scopes`` for provenance display.
    """
    stack = ConfigStack()

    # 1. Global agent config
    global_cfg = get_global_agent_config()
    if global_cfg:
        stack.push(ConfigScope("global", None, global_cfg))

    # 2. Project agent config (passed in by caller)
    if agent_config:
        source = (project_root / "project.yml") if project_root else None
        stack.push(ConfigScope("project", source, agent_config))

    # 3. Preset (if requested)
    if preset:
        from terok.lib.core.projects import load_preset

        preset_data, preset_path = load_preset(project_id, preset)
        # Skip empty presets – they contribute nothing to the merge and would
        # only add noise to provenance output from ``config show``.
        if preset_data:
            scope_label = _preset_scope_label(preset_path)
            stack.push(ConfigScope(scope_label, preset_path, preset_data))

    # 4. CLI overrides
    if cli_overrides:
        stack.push(ConfigScope("cli", None, cli_overrides))

    return stack


def resolve_provider_value(
    key: str,
    config: dict[str, Any],
    provider_name: str,
) -> Any | None:
    """Extract a provider-aware config value.

    Supports two forms:

    * **Flat value** — ``model: opus`` → same for all providers.
    * **Per-provider dict** — ``model: {claude: opus, codex: o3, _default: fast}``
      → looks up *provider_name*, falls back to ``_default``, then ``None``.

    Returns ``None`` when the key is absent or has no match for the provider.

    **Null override behaviour**: when a per-provider dict maps a provider to
    ``null`` (Python ``None``), that ``None`` is treated as "no value" and the
    resolver falls back to ``_default``.  This is intentional — it allows a
    lower-priority config layer to set a provider-specific value that a
    higher-priority layer can effectively *unset* by mapping it to ``null``,
    letting the ``_default`` (or ``None``) bubble up instead.
    """
    val = config.get(key)
    if val is None:
        return None
    if isinstance(val, dict):
        provider_val = val.get(provider_name)
        if provider_val is not None:
            return provider_val
        return val.get("_default")
    return val


def resolve_agent_config(
    project_id: str,
    *,
    agent_config: dict[str, Any] | None = None,
    project_root: Path | None = None,
    preset: str | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build config stack and return the merged agent config dict.

    Convenience wrapper around :func:`build_agent_config_stack` for callers
    that only need the final resolved dict (e.g. task runners).
    """
    return build_agent_config_stack(
        project_id,
        agent_config=agent_config,
        project_root=project_root,
        preset=preset,
        cli_overrides=cli_overrides,
    ).resolve()
