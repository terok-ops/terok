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

from ..lib.core.config import get_envs_base_dir
from ..lib.core.projects import effective_ssh_key_name, load_project
from ..lib.facade import (
    WEB_BACKENDS,
    GitGate,
    SSHManager,
    authenticate,
    build_images,
    delete_project,
    find_projects_sharing_gate,
    generate_dockerfiles,
    install_systemd_units,
    maybe_pause_for_ssh_key_registration,
    start_daemon,
    stop_daemon,
    uninstall_systemd_units,
)
from .shell_launch import launch_login


class ProjectActionsMixin:
    """Project infrastructure and shared action helpers for TerokTUI.

    Provides ``action_*`` methods for project-level operations (Dockerfile
    generation, image building, SSH init, gate sync, auth, wizard) as well
    as reusable helpers (``_run_suspended``, ``_launch_terminal_session``,
    ``_prompt_ui_backend``) used by both project and task actions.
    """

    # ---------- Shared helpers ----------

    def _prompt_ui_backend(self) -> str:
        """Prompt the user to select a web UI backend and return the choice."""
        backends = list(WEB_BACKENDS)
        default = os.environ.get("DEFAULT_AGENT", "").strip().lower()
        if default not in backends:
            default = backends[0] if backends else "codex"

        print("Select UI backend:")
        for idx, backend in enumerate(backends, start=1):
            label = backend
            if backend == default:
                label += " (default)"
            print(f"  {idx}) {label}")

        choice = input(f"Backend [{default}]: ").strip()
        if not choice:
            return default
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(backends):
                return backends[idx - 1]
            return default
        normalized = choice.lower()
        return normalized if normalized in backends else default

    def _print_sync_gate_ssh_help(self, project_id: str) -> None:
        """Print SSH-specific troubleshooting details for gate sync failures."""
        try:
            project = load_project(project_id)
        except (Exception, SystemExit):
            return

        upstream = project.upstream_url or ""
        if not (upstream.startswith("git@") or upstream.startswith("ssh://")):
            return

        ssh_dir = project.ssh_host_dir or (get_envs_base_dir() / f"_ssh-config-{project.id}")
        key_name = effective_ssh_key_name(project, key_type="ed25519")
        pub_key_path = ssh_dir / f"{key_name}.pub"

        print("\nHint: this project uses an SSH upstream.")
        print(
            "Gate sync failures are often caused by a missing SSH key registration on the remote."
        )
        print(f"Public key path: {pub_key_path}")

        if pub_key_path.is_file():
            try:
                pub_key_text = pub_key_path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                pub_key_text = ""
            if pub_key_text:
                print("Public key:")
                print(f"  {pub_key_text}")
            else:
                print("Public key file exists but is empty.")
        else:
            print(f"Public key file not found at {pub_key_path}.")
            print(f"Run 'terokctl ssh-init {project_id}' to generate it.")

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

    async def action_init_ssh(self) -> None:
        """Initialize the per-project SSH directory and keypair."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        await self._run_suspended(
            lambda: SSHManager(load_project(pid)).init(),
            success_msg=f"Initialized SSH dir for {pid}",
        )

    async def _action_build_agents(self) -> None:
        """Build L0+L1+L2 with fresh agent installs."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        await self._run_suspended(
            lambda: build_images(pid, rebuild_agents=True),
            success_msg=f"Built L0+L1+L2 with fresh agents for {pid}",
        )

    async def _action_build_full(self) -> None:
        """Full rebuild with no cache."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        await self._run_suspended(
            lambda: build_images(pid, full_rebuild=True),
            success_msg=f"Full rebuild (no cache) completed for {pid}",
        )

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
            SSHManager(load_project(pid)).init()
            maybe_pause_for_ssh_key_registration(pid)
            print("\nStep 2/4: Generating Dockerfiles...")
            generate_dockerfiles(pid)
            print("\nStep 3/4: Building images...")
            build_images(pid)
            print("\nStep 4/4: Syncing git gate...")
            res = GitGate(load_project(pid)).sync()
            if not res["success"]:
                print(f"\nGate sync warnings: {', '.join(res['errors'])}")
            else:
                print(f"\nGate ready at {res['path']}")
                gate_ok = True
            print("\n=== Full Setup complete! ===")

        ok = await self._run_suspended(work, refresh="project_state")
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
            lambda: authenticate(self.current_project_id, provider),
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
                result = GitGate(load_project(project_id)).sync()
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
            import yaml as _yaml

            project = load_project(pid)
            project_yml = project.root / "project.yml"
            if not project_yml.is_file():
                self.notify("No project.yml found.")
                return
            raw = _yaml.safe_load(project_yml.read_text(encoding="utf-8")) or {}
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

            project_yml.write_text(_yaml.safe_dump(raw, default_flow_style=False), encoding="utf-8")
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
            from ..lib.containers.agent_config import resolve_agent_config
            from ..lib.containers.instructions import resolve_instructions

            project = load_project(pid)
            effective = resolve_agent_config(pid)
            from ..lib.containers.headless_providers import get_provider as _get_provider

            provider = _get_provider(None, project)
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
            from ..lib.containers.instructions import bundled_default_instructions

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
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "terok.cli.main", "project-wizard"],
                    check=False,
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

        from ..lib.core.config import deleted_projects_dir

        archive_dir = deleted_projects_dir()
        lines.append("\nAll project data will be permanently deleted.")
        lines.append("Project config, task data, and build artifacts will be archived at:")
        lines.append(f"{archive_dir}")

        from .screens import ConfirmDeleteScreen

        await self.push_screen(
            ConfirmDeleteScreen(
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
