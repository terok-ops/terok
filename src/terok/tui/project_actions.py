# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""ProjectActionsMixin — project infrastructure actions for TerokTUI.

Handles project setup (generate, build, ssh-init, gate-sync), authentication,
and the project wizard.  Also provides shared TUI helpers used by both
project and task actions.
"""

import os
import shlex
import subprocess
import sys
from collections.abc import Callable

from terok_sandbox import (
    install_systemd_units,
    install_vault_systemd,
    start_daemon,
    start_vault,
    stop_daemon,
    stop_vault,
    uninstall_systemd_units,
    uninstall_vault_systemd,
)

from ..lib.core.projects import load_project
from ..lib.domain.facade import (
    authenticate,
    build_images,
    delete_project,
    find_projects_sharing_gate,
    generate_dockerfiles,
    maybe_pause_for_ssh_key_registration,
    provision_ssh_key,
    summarize_ssh_init,
)
from ..lib.domain.project import make_git_gate
from .shell_launch import launch_login


def _lookup_vault_pub_line(scope: str) -> str | None:
    """Return the scope's most-recent public key line, or ``None`` if unassigned."""
    from terok_sandbox import public_line_of

    from ..lib.domain.facade import vault_db

    with vault_db() as db:
        records = db.load_ssh_keys_for_scope(scope)
    return public_line_of(records[-1]) if records else None


class ProjectActionsMixin:
    """Project infrastructure and shared action helpers for TerokTUI.

    Provides ``action_*`` methods for project-level operations (Dockerfile
    generation, image building, SSH init, gate sync, auth, wizard) as well
    as reusable helpers (``_run_suspended``, ``_launch_terminal_session``)
    used by both project and task actions.
    """

    # ---------- Shared helpers ----------

    def _print_sync_gate_ssh_help(self, project_id: str) -> None:
        """Print SSH-specific troubleshooting details for gate sync failures."""
        from terok_sandbox import is_ssh_url

        try:
            project = load_project(project_id)
        except (Exception, SystemExit):
            return

        if not is_ssh_url(project.upstream_url):
            return

        print("\nHint: this project uses an SSH upstream.")
        print(
            "Gate sync failures are often caused by a missing SSH key registration on the remote."
        )

        pub_line = _lookup_vault_pub_line(project.id)
        if pub_line is None:
            print(f"No SSH key assigned to project (scope) {project.id!r} in the vault.")
            print(f"Run 'terok project ssh-init {project_id}' to generate one,")
            print("then register the printed public key as a deploy key upstream.")
        else:
            print("Public key (register as a deploy key on the remote):")
            print(f"  {pub_line}")

    async def _run_suspended(
        self,
        fn: Callable[[], None],
        *,
        success_msg: str | None = None,
        refresh: str | None = "project_state",
    ) -> bool:
        """Run *fn* in a suspended TUI session with standard error handling.

        Suspends the TUI, runs *fn*, waits for the user to press Enter,
        then optionally notifies and refreshes.  Returns True if *fn*
        completed without error.  The resume prompt is shown in a finally
        block so the user always gets back to the TUI.
        """
        ok = False
        with self.suspend():
            try:
                fn()
                ok = True
            except SystemExit as e:
                print(f"Error: {e}")
            except Exception as e:
                print(f"Error: {e}")
            finally:
                input("\n[Press Enter to return to TerokTUI] ")
        if ok and success_msg:
            self.notify(success_msg)
        if refresh == "project_state":
            self._refresh_project_state()
        elif refresh == "tasks":
            await self.refresh_tasks()
        return ok

    async def _launch_terminal_session(
        self,
        cmd: list[str],
        *,
        title: str,
        cname: str,
        label: str = "Opened",
    ) -> None:
        """Launch *cmd* via tmux/terminal/web, falling back to a suspended TUI."""
        method, port = launch_login(cmd, title=title)

        if method == "tmux":
            self.notify(f"{label} in tmux window: {cname}")
        elif method == "terminal":
            self.notify(f"{label} in new terminal: {cname}")
        elif method == "web" and port is not None:
            self.open_url(f"http://localhost:{port}")
            self.notify(f"{label} in browser: {cname}")
        else:
            with self.suspend():
                try:
                    subprocess.run(cmd)
                except Exception as e:
                    print(f"Error: {e}")
                input("\n[Press Enter to return to TerokTUI] ")
            await self.refresh_tasks()

    # ---------- Project infrastructure actions ----------

    async def action_generate_dockerfiles(self) -> None:
        """Generate Dockerfiles for the current project."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        await self._run_suspended(
            lambda: generate_dockerfiles(pid),
            success_msg=f"Generated Dockerfiles for {pid}",
        )

    async def action_build_images(self) -> None:
        """Build only L2 project images (reuses existing L0/L1)."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        await self._run_suspended(
            lambda: build_images(pid),
            success_msg=f"Built L2 project images for {pid}",
        )
        self._invalidate_image_caches()

    async def action_init_ssh(self) -> None:
        """Mint a fresh vault-backed SSH keypair for the current project."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id

        await self._run_suspended(
            lambda: summarize_ssh_init(provision_ssh_key(pid)),
            success_msg=f"Initialized vault-backed SSH key for {pid}",
        )

    async def _action_build_agents(self) -> None:
        """Rebuild from L0 with fresh agents."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        await self._run_suspended(
            lambda: build_images(pid, refresh_agents=True),
            success_msg=f"Rebuilt from L0 with fresh agents for {pid}",
        )
        self._invalidate_image_caches()

    async def _action_build_full(self) -> None:
        """Rebuild from L0 (no cache)."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        await self._run_suspended(
            lambda: build_images(pid, full_rebuild=True),
            success_msg=f"Rebuilt from L0 (no cache) for {pid}",
        )
        self._invalidate_image_caches()

    @staticmethod
    def _invalidate_image_caches() -> None:
        """Drop cached image-label lookups after an in-TUI rebuild.

        The :func:`installed_agents` lru_cache is keyed on the L1 tag,
        which a rebuild reuses — so without this, the picker would keep
        showing the previous agent set until the TUI restarts.
        """
        from terok.lib.core.images import installed_agents

        installed_agents.cache_clear()

    async def _action_project_init(self) -> None:
        """Full project setup: ssh-init, generate, build, gate-sync."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id

        gate_ok = False

        def work() -> None:
            """Run all four setup steps sequentially."""
            nonlocal gate_ok
            print(f"=== Full Setup for {pid} ===\n")
            print("Step 1/4: Initializing SSH...")
            summarize_ssh_init(provision_ssh_key(pid))
            maybe_pause_for_ssh_key_registration(pid)
            print("\nStep 2/4: Generating Dockerfiles...")
            generate_dockerfiles(pid)
            print("\nStep 3/4: Building images...")
            build_images(pid)
            print("\nStep 4/4: Syncing git gate...")
            res = make_git_gate(load_project(pid)).sync()
            if not res["success"]:
                print(f"\nGate sync warnings: {', '.join(res['errors'])}")
            else:
                print(f"\nGate ready at {res['path']}")
                gate_ok = True
            print("\n=== Full Setup complete! ===")

        ok = await self._run_suspended(work, refresh="project_state")
        self._invalidate_image_caches()
        if ok and gate_ok:
            self.notify(f"Full setup completed for {pid}")
        elif ok:
            self.notify(f"Setup done for {pid} (gate sync had errors)", severity="warning")

    # ---------- Authentication actions ----------

    async def _action_auth(self, provider: str) -> None:
        """Run auth flow for the given provider."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        await self._run_suspended(
            lambda: authenticate(provider, self.current_project_id),
            success_msg=f"Auth completed for {provider}",
            refresh=None,
        )

    # ---------- Gate sync ----------

    async def action_sync_gate(self) -> None:
        """Manually sync gate from upstream."""
        await self._action_sync_gate()

    async def _action_sync_gate(self) -> None:
        """Sync gate (init if doesn't exist, sync if exists)."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return

        project_id = self.current_project_id
        sync_ok = False
        with self.suspend():
            try:
                print(f"Syncing gate for {project_id}...")
                result = make_git_gate(load_project(project_id)).sync()
                if result["success"]:
                    sync_ok = True
                    if result["created"]:
                        print("Gate created and synced from upstream.")
                    else:
                        print("Gate synced from upstream.")
                else:
                    print(f"Gate sync failed: {', '.join(result['errors'])}")
                    self._print_sync_gate_ssh_help(project_id)
            except SystemExit as e:
                print(f"Gate sync failed: {e}")
                self._print_sync_gate_ssh_help(project_id)
            except Exception as e:
                print(f"Gate operation error: {e}")
                self._print_sync_gate_ssh_help(project_id)
            input("\n[Press Enter to return to TerokTUI] ")

        if sync_ok:
            self.notify("Gate synced from upstream")
        else:
            self.notify("Gate sync failed. See terminal output.")
        self._refresh_project_state()

    # ---------- Instructions editing ----------

    async def _action_edit_instructions(self) -> None:
        """Open project instructions.md in $EDITOR for the current project."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id

        def work() -> None:
            """Open instructions file in $EDITOR (creates if absent)."""
            project = load_project(pid)
            instr_path = project.root / "instructions.md"
            editor = os.environ.get("EDITOR", "").strip() or "vi"
            editor_cmd = shlex.split(editor)
            result = subprocess.run([*editor_cmd, str(instr_path)], check=False)
            if result.returncode != 0:
                raise SystemExit(f"Editor exited with code {result.returncode}")

        await self._run_suspended(work, success_msg=f"Instructions updated for {pid}")

    async def _action_toggle_instructions_inherit(self) -> None:
        """Toggle YAML instructions between inherit and override mode."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id

        try:
            from ..lib.util.yaml import dump as _yaml_dump, load as _yaml_load

            project = load_project(pid)
            project_yml = project.root / "project.yml"
            if not project_yml.is_file():
                self.notify("No project.yml found.")
                return
            raw = _yaml_load(project_yml.read_text(encoding="utf-8")) or {}
            agent = raw.setdefault("agent", {})
            current = agent.get("instructions")

            # Determine current mode and toggle, preserving existing custom entries
            if current is None:
                # Implicit inherit → explicit custom-only (empty)
                agent["instructions"] = []
                mode_label = "custom only (defaults disabled)"
            elif isinstance(current, list):
                items = [item for item in current if item != "_inherit"]
                if "_inherit" in current:
                    # Disable inheritance, keep existing custom entries
                    agent["instructions"] = items
                    mode_label = "custom only (defaults disabled)"
                else:
                    # Enable inheritance, preserve existing custom entries
                    agent["instructions"] = ["_inherit", *items]
                    mode_label = "inheriting defaults"
            else:
                # Scalar/dict forms — not safe to toggle automatically
                self.notify(
                    "Toggle supports list/implicit instructions only; "
                    "edit project.yml manually for this form.",
                    severity="warning",
                )
                return

            project_yml.write_text(_yaml_dump(raw), encoding="utf-8")
            self.notify(f"Instructions: {mode_label}")
        except Exception as e:
            self.notify(f"Toggle failed: {e}")
        self._refresh_project_state()

    async def _action_show_resolved_instructions(self) -> None:
        """Display fully resolved instructions as a task would receive them."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id

        def work() -> None:
            """Resolve and print the effective instructions."""
            from terok_executor import resolve_instructions

            from ..lib.orchestration.agent_config import resolve_agent_config

            project = load_project(pid)
            effective = resolve_agent_config(
                pid, agent_config=project.agent_config, project_root=project.root
            )
            from terok_executor import get_provider as _get_provider

            provider = _get_provider(None, default_agent=project.default_agent)
            text = resolve_instructions(effective, provider.name, project_root=project.root)
            print("=== Resolved Instructions ===\n")
            print(text)
            print(f"\n=== End ({len(text)} chars) ===")

        await self._run_suspended(work, refresh=None)

    async def _action_edit_global_instructions(self) -> None:
        """Open global instructions.md in $EDITOR."""

        def work() -> None:
            """Open global instructions file in $EDITOR."""
            from ..lib.core.config import global_config_path

            global_instr = global_config_path().parent / "instructions.md"
            global_instr.parent.mkdir(parents=True, exist_ok=True)
            editor = os.environ.get("EDITOR", "").strip() or "vi"
            editor_cmd = shlex.split(editor)
            result = subprocess.run([*editor_cmd, str(global_instr)], check=False)
            if result.returncode != 0:
                raise SystemExit(f"Editor exited with code {result.returncode}")

        await self._run_suspended(work, success_msg="Global instructions updated", refresh=None)

    async def _action_show_default_instructions(self) -> None:
        """Display the bundled default instructions (read-only)."""

        def work() -> None:
            """Print bundled default instructions."""
            from terok_executor import bundled_default_instructions

            text = bundled_default_instructions()
            print("=== Bundled Default Instructions ===\n")
            print(text)
            print(f"\n=== End ({len(text)} chars) ===")

        await self._run_suspended(work, refresh=None)

    # ---------- OpenCode config import ----------

    async def _action_import_opencode_config(self) -> None:
        """Push the OpenCode config import modal and handle the result."""
        from .screens import OpenCodeConfigScreen

        def _on_result(result: str | None) -> None:
            """Notify the user about the import result."""
            if result:
                self.notify(f"OpenCode config imported to {result}")

        await self.push_screen(OpenCodeConfigScreen(), _on_result)

    # --- Project wizard ---

    async def action_new_project_wizard(self) -> None:
        """Launch the CLI project wizard in a suspended terminal."""
        with self.suspend():
            env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "terok.cli", "project", "wizard"],
                    check=False,
                    env=env,
                )
                if result.returncode != 0:
                    print(f"Wizard exited with code {result.returncode}")
            except Exception as e:
                print(f"Error: {e}")
            input("\n[Press Enter to return to TerokTUI] ")
        await self.refresh_projects()
        self.notify("Project list refreshed.")

    # --- Project delete ---

    async def _action_delete_project(self) -> None:
        """Delete the current project after confirmation."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return

        pid = self.current_project_id
        try:
            project = load_project(pid)
        except (SystemExit, Exception) as e:
            self.notify(f"Error loading project: {e}")
            return

        # Build confirmation message
        lines = [
            f"Delete project '{pid}'?\n",
            f"Config root: {project.root}",
            f"Security class: {project.security_class}",
        ]
        if project.upstream_url:
            lines.append(f"Upstream: {project.upstream_url}")

        sharing = find_projects_sharing_gate(project.gate_path, exclude_project=pid)
        if sharing:
            names = ", ".join(p for p, _ in sharing)
            lines.append(f"\nNote: gate is shared with: {names} (will NOT be deleted)")

        from ..lib.core.config import archive_dir as _archive_dir

        archive_path = _archive_dir()
        lines.append("\nAll project data will be permanently deleted.")
        lines.append("Project config, task data, and build artifacts will be archived at:")
        lines.append(f"{archive_path}")

        from .screens import ConfirmDestructiveScreen

        await self.push_screen(
            ConfirmDestructiveScreen(
                message="\n".join(lines),
                title=f"Delete Project: {pid}",
            ),
            self._on_delete_project_confirmed,
        )

    async def _on_delete_project_confirmed(self, confirmed: bool) -> None:
        """Handle the result of the delete confirmation dialog."""
        if not confirmed or not self.current_project_id:
            return

        pid = self.current_project_id
        try:
            result = delete_project(pid)
        except (SystemExit, Exception) as e:
            self.notify(f"Delete failed: {e}")
            return

        msg = f"Project '{pid}' deleted."
        if result.get("archive"):
            msg += f" Archive: {result['archive']}"
        if result.get("skipped"):
            msg += f" ({len(result['skipped'])} item(s) skipped)"
        self.notify(msg)

        self.current_project_id = None
        await self.refresh_projects()

    # ---------- Gate server actions ----------

    async def _action_gate_install(self) -> None:
        """Install systemd socket units for the gate server."""
        await self._run_suspended(
            install_systemd_units,
            success_msg="Gate server systemd units installed",
        )

    async def _action_gate_uninstall(self) -> None:
        """Uninstall systemd units for the gate server."""
        await self._run_suspended(
            uninstall_systemd_units,
            success_msg="Gate server systemd units uninstalled",
        )

    async def _action_gate_start(self) -> None:
        """Start the gate server daemon."""
        await self._run_suspended(
            start_daemon,
            success_msg="Gate server daemon started",
        )

    async def _action_gate_stop(self) -> None:
        """Stop the gate server daemon."""
        await self._run_suspended(
            stop_daemon,
            success_msg="Gate server daemon stopped",
        )

    # ---------- Shield actions ----------

    async def _action_shield_setup(self) -> None:
        """Push shield setup modal and run hook installation on result."""
        from .screens import ShieldSetupScreen

        await self.push_screen(ShieldSetupScreen(), self._on_shield_setup_result)

    async def _on_shield_setup_result(self, result: str | None) -> None:
        """Run hook installation after shield setup modal choice."""
        if result is None:
            return
        from terok_sandbox import setup_hooks_direct as shield_setup_hooks_direct

        await self._run_suspended(
            lambda: shield_setup_hooks_direct(root=result == "root"),
            success_msg="Shield hooks installed",
        )

    # ---------- Vault actions ----------

    async def _action_vault_install(self) -> None:
        """Install systemd socket activation for the vault."""
        from terok_executor import ensure_vault_routes

        from ..lib.core.config import make_sandbox_config

        def _install() -> None:
            ensure_vault_routes(cfg=make_sandbox_config())
            install_vault_systemd(cfg=make_sandbox_config())

        await self._run_suspended(
            _install,
            success_msg="Vault systemd socket installed",
        )

    async def _action_vault_uninstall(self) -> None:
        """Uninstall vault systemd units."""
        from ..lib.core.config import make_sandbox_config

        await self._run_suspended(
            lambda: uninstall_vault_systemd(cfg=make_sandbox_config()),
            success_msg="Vault systemd units removed",
        )

    async def _action_vault_start(self) -> None:
        """Generate routes and start the vault daemon."""
        from terok_executor import ensure_vault_routes

        from ..lib.core.config import make_sandbox_config

        def _start() -> None:
            ensure_vault_routes(cfg=make_sandbox_config())
            start_vault(cfg=make_sandbox_config())

        await self._run_suspended(
            _start,
            success_msg="Vault started",
        )

    async def _action_vault_stop(self) -> None:
        """Stop the vault daemon."""
        await self._run_suspended(
            stop_vault,
            success_msg="Vault stopped",
        )
