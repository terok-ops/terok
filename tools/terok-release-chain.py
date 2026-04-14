#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
"""Cascading release chain for the terok package family.

Plan-then-execute architecture: generate a release plan (JSON), validate
it, then execute step-by-step with crash-recovery.  Replaces the bash
script (tools/terok-release-chain.sh) with structured state and the
"release from PR" workflow for chained feature branches.

Usage:
    python3 tools/terok-release-chain.py quick sandbox              # single package
    python3 tools/terok-release-chain.py quick sandbox..terok        # chain
    python3 tools/terok-release-chain.py quick sandbox..terok --open-top  # chain, top=deps-only
    python3 tools/terok-release-chain.py quick --from-prs sandbox:42,executor:55
    python3 tools/terok-release-chain.py quick --from-prs s:42,e:55,t:706 --open-top
    python3 tools/terok-release-chain.py open feat/comms dbus
    python3 tools/terok-release-chain.py plan sandbox..terok -o plan.json
    python3 tools/terok-release-chain.py simulate plan.json
    python3 tools/terok-release-chain.py execute plan.json
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Never

import click
import tomlkit
from pydantic import VERSION as _pydantic_ver, BaseModel, Field
from rich.console import Console
from rich.table import Table

if int(_pydantic_ver.split(".")[0]) < 2:
    raise SystemExit(f"pydantic >= 2 required (found {_pydantic_ver}): pip install 'pydantic>=2'")

console = Console(stderr=True)

# ── Chain config ──────────────────────────────────────────────────────────
#
# Single source of truth for release ordering and sibling relationships.

CHAIN = ["terok-dbus", "terok-shield", "terok-sandbox", "terok-executor", "terok"]

DEPS: dict[str, list[str]] = {
    "terok-dbus": [],
    "terok-shield": ["terok-dbus"],
    "terok-sandbox": ["terok-shield"],
    "terok-executor": ["terok-sandbox"],
    "terok": ["terok-executor", "terok-sandbox", "terok-dbus"],
}

ALIASES = {
    "dbus": "terok-dbus",
    "shield": "terok-shield",
    "sandbox": "terok-sandbox",
    "executor": "terok-executor",
    "terok": "terok",
} | {n: n for n in CHAIN}


def die(msg: str) -> Never:
    """Print error and exit."""
    console.print(f"[bold red]ERROR:[/] {msg}")
    raise SystemExit(1)


def normalise(name: str) -> str:
    """Accept short names (shield) and full names (terok-shield)."""
    return ALIASES.get(name) or die(f"Unknown repo: {name}")


def pkg_name(repo: str) -> str:
    """terok-shield -> terok_shield."""
    return repo.replace("-", "_")


def slugify(text: str) -> str:
    """Normalize a human-readable name to a safe machine token: [a-z0-9-]."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def bump_version(ver: str, level: str = "patch") -> str:
    """X.Y.Z -> next version at the given semver level."""
    major, minor, patch = (int(x) for x in ver.split("."))
    match level:
        case "major":
            return f"{major + 1}.0.0"
        case "minor":
            return f"{major}.{minor + 1}.0"
        case _:
            return f"{major}.{minor}.{patch + 1}"


def build_chain(start: str, end: str | None = None) -> list[str]:
    """Slice CHAIN from start to end (inclusive)."""
    i = CHAIN.index(start) if start in CHAIN else die(f"Unknown: {start}")
    if not end:
        return CHAIN[i:]
    j = CHAIN.index(end) if end in CHAIN else die(f"Unknown: {end}")
    return CHAIN[i : j + 1] if j >= i else die(f"{end} is not downstream of {start}")


def wheel_filename(repo: str, version: str) -> str:
    """terok-sandbox 0.0.50 -> terok_sandbox-0.0.50-py3-none-any.whl."""
    return f"{pkg_name(repo)}-{version}-py3-none-any.whl"


def wheel_url(org: str, repo: str, version: str) -> str:
    """Construct the GitHub release wheel URL."""
    return (
        f"https://github.com/{org}/{repo}/releases/download/"
        f"v{version}/{wheel_filename(repo, version)}"
    )


# ── Plan model ────────────────────────────────────────────────────────────


class StepKind(StrEnum):
    CLONE_SYNC = "clone_sync"
    CHECKOUT = "checkout"
    VERSION_BUMP = "version_bump"
    DEP_UPDATE = "dep_update"
    POETRY_LOCK = "poetry_lock"
    GIT_COMMIT = "git_commit"
    GIT_PUSH = "git_push"
    PR_CREATE = "pr_create"
    PR_MERGE = "pr_merge"
    TAG = "tag"
    RELEASE = "release"
    WHEEL_POLL = "wheel_poll"


class Action(StrEnum):
    RELEASE_MASTER = "release_master"
    RELEASE_PR = "release_pr"
    DEPS_ONLY = "deps_only"
    SKIP = "skip"


class Step(BaseModel):
    """One atomic operation in the release plan."""

    id: str
    kind: StepKind
    package: str
    params: dict[str, Any] = {}
    status: str = "pending"
    result: dict[str, Any] = {}


class PackagePlan(BaseModel):
    """What to do with one package in the chain."""

    repo: str
    action: Action
    current_version: str
    new_version: str | None = None
    pr_number: int | None = None
    pr_branch: str | None = None
    sibling_deps: dict[str, str] = {}


class Plan(BaseModel):
    """Complete release plan — serializable to JSON."""

    packages: list[PackagePlan]
    steps: list[Step]
    gh_org: str
    gh_fork: str
    release_name: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ── Runtime context ───────────────────────────────────────────────────────


@dataclass
class Ctx:
    """Mutable runtime state for the executor."""

    cache_dir: Path
    dry_run: bool = False
    auto_yes: bool = False
    skip_checks: bool = False
    check_timeout: int = 1800
    wheel_timeout: int = 300
    plan_path: Path | None = None


# ── Shell helpers ─────────────────────────────────────────────────────────


def sh(
    *args: str, cwd: Path | None = None, capture: bool = False, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess — always surfaces output on failure."""
    r = subprocess.run(args, cwd=cwd, capture_output=capture, text=True, check=False)
    if check and r.returncode:
        detail = (r.stderr or r.stdout or "").strip() if capture else ""
        cmd = " ".join(args)
        msg = f"Command failed (exit {r.returncode}): {cmd}"
        if detail:
            msg += f"\n{detail}"
        die(msg)
    return r


# ── TOML ops ──────────────────────────────────────────────────────────────
#
# Uses tomlkit to preserve comments and formatting.


def _toml_deps(path: Path) -> tuple[tomlkit.TOMLDocument, Any]:
    doc = tomlkit.parse(path.read_text())
    return doc, doc["tool"]["poetry"]["dependencies"]


def set_version_toml(path: Path, version: str):
    """Set the version field in pyproject.toml."""
    doc = tomlkit.parse(path.read_text())
    doc["tool"]["poetry"]["version"] = version
    path.write_text(tomlkit.dumps(doc))


def set_dep_url(path: Path, dep_repo: str, version: str, org: str):
    """Set a dependency to a wheel URL (works for both URL and git-branch sources)."""
    doc, deps = _toml_deps(path)
    key = dep_repo if dep_repo in deps else pkg_name(dep_repo)
    if key not in deps:
        return
    t = tomlkit.inline_table()
    t.append("url", wheel_url(org, dep_repo, version))
    deps[key] = t
    path.write_text(tomlkit.dumps(doc))


def set_branch_dep(path: Path, dep_repo: str, branch: str, fork: str):
    """Set a dependency to a git-branch reference (for PR chain development)."""
    doc, deps = _toml_deps(path)
    key = dep_repo if dep_repo in deps else pkg_name(dep_repo)
    if key not in deps:
        return
    t = tomlkit.inline_table()
    t.append("git", f"https://github.com/{fork}/{dep_repo}.git")
    t.append("branch", branch)
    deps[key] = t
    path.write_text(tomlkit.dumps(doc))


def pinned_version(path: Path, dep_repo: str, org: str) -> str | None:
    """Extract version from a URL wheel dep, or None if git/missing."""
    m = re.search(rf"{org}/{dep_repo}/releases/download/v([^/]+)/", path.read_text())
    return m.group(1) if m else None


# ── Clone cache ───────────────────────────────────────────────────────────


def ensure_clone(repo: str, cache_dir: Path, org: str, fork: str):
    """Create or sync a repo clone in the release cache."""
    repo_dir = cache_dir / repo
    if (repo_dir / ".git").is_dir():
        console.print(f"  [cyan]{repo:<16}[/] syncing...", end="\r")
        sh("git", "fetch", "upstream", "--quiet", cwd=repo_dir)
        sh("git", "reset", "--hard", "upstream/master", "-q", cwd=repo_dir)
        sh("git", "clean", "-fd", "--quiet", cwd=repo_dir)
    else:
        console.print(f"  [cyan]{repo:<16}[/] cloning...", end="\r")
        sh("git", "clone", "--quiet", f"git@github.com:{org}/{repo}.git", str(repo_dir))
        sh("git", "remote", "rename", "origin", "upstream", cwd=repo_dir)
        sh("git", "remote", "add", "origin", f"git@github.com:{fork}/{repo}.git", cwd=repo_dir)
    console.print(f"  [cyan]{repo:<16}[/] ready     ")


# ── GitHub ops ────────────────────────────────────────────────────────────


def latest_version(repo: str, org: str) -> str:
    """Get the latest release version from GitHub."""
    r = sh(
        "gh",
        "release",
        "list",
        "--repo",
        f"{org}/{repo}",
        "--limit",
        "1",
        "--json",
        "tagName",
        "--jq",
        ".[0].tagName",
        capture=True,
    )
    return r.stdout.strip().lstrip("v") or die(f"No releases for {repo}")


def pr_info(number: int, gh_repo: str) -> dict:
    """Get PR metadata."""
    r = sh(
        "gh",
        "pr",
        "view",
        str(number),
        "--repo",
        gh_repo,
        "--json",
        "headRefName,state,url",
        capture=True,
    )
    return json.loads(r.stdout)


def pr_state(url: str, gh_repo: str) -> str:
    """Query PR state: OPEN, MERGED, CLOSED."""
    r = sh(
        "gh",
        "pr",
        "view",
        url,
        "--repo",
        gh_repo,
        "--json",
        "state",
        "--jq",
        ".state",
        capture=True,
    )
    return r.stdout.strip()


_MIN_GH_VERSION = (2, 73, 0)
"""Minimum ``gh`` version for ``gh pr checks --json``."""


def _check_gh_version() -> None:
    """Abort early if ``gh`` is too old for the JSON flags we rely on."""
    try:
        r = subprocess.run(["gh", "--version"], capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        die("'gh' (GitHub CLI) not found on PATH")
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", r.stdout)
    if not m:
        die(f"Cannot parse gh version from: {r.stdout.strip()}")
    installed = tuple(int(x) for x in m.groups())
    if installed < _MIN_GH_VERSION:
        need = ".".join(str(x) for x in _MIN_GH_VERSION)
        have = ".".join(str(x) for x in installed)
        die(f"gh >= {need} required (found {have}). Upgrade: https://github.com/cli/cli/releases")


def wait_for_checks(pr_url: str, gh_repo: str, ctx: Ctx) -> str:
    """Wait for CI. Returns 'passed' or 'merged'."""
    if ctx.skip_checks:
        console.print("[yellow]Skipping CI checks[/]")
        return "passed"
    if ctx.dry_run:
        console.print(f"[yellow][pretend][/] Would wait for checks on {pr_url}")
        return "passed"

    console.print(f"Waiting for PR checks (timeout {ctx.check_timeout}s)...")
    grace, poll = 30, 2

    for elapsed in range(0, ctx.check_timeout, poll):
        if elapsed and elapsed % 10 == 0:
            st = pr_state(pr_url, gh_repo)
            if st == "MERGED":
                console.print("[green]PR merged externally.[/]")
                return "merged"
            if st == "CLOSED":
                die("PR closed without merging.")

        r = subprocess.run(
            ["gh", "pr", "checks", pr_url, "--repo", gh_repo, "--json", "name,bucket"],
            capture_output=True,
            text=True,
        )

        if r.returncode not in (0, 8) and not r.stdout.strip():
            if elapsed < grace:
                time.sleep(poll)
                continue
            detail = (r.stderr or r.stdout or "").strip()
            die(f"gh pr checks failed (exit {r.returncode}): {detail}")

        checks = json.loads(r.stdout) if r.stdout.strip() else []
        if not checks:
            if elapsed < grace:
                time.sleep(poll)
                continue
            console.print("[green]No checks configured.[/]")
            return "passed"

        pending = sum(1 for c in checks if c["bucket"] == "pending")
        failing = [c for c in checks if c["bucket"] in ("fail", "cancel")]

        if pending:
            time.sleep(poll)
            continue
        if not failing:
            console.print("[green]All checks passed![/]")
            return "passed"

        console.print("[yellow]Checks failed:[/]")
        for c in failing:
            console.print(f"  {c['name']}: {c['bucket']}")
        if ctx.auto_yes:
            console.print("[yellow]Force-merging (--yes)[/]")
        elif not click.confirm("Force merge anyway?", default=False):
            die("Aborted.")
        return "passed"

    die(f"Timed out after {ctx.check_timeout}s")


def squash_merge(pr_url: str, gh_repo: str) -> str:
    """Squash-merge PR. Returns merge SHA. Handles race conditions."""
    console.print("Squash-merging PR...")
    r = subprocess.run(
        ["gh", "pr", "merge", pr_url, "--repo", gh_repo, "--squash", "--delete-branch", "--admin"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = r.stderr + r.stdout
        if "already in progress" in err or "already been merged" in err:
            console.print("[yellow]Merge race — waiting...[/]")
            for _ in range(15):
                if pr_state(pr_url, gh_repo) == "MERGED":
                    break
                time.sleep(2)
            else:
                die("PR still not merged after 30s")
        else:
            die(f"Merge failed: {err.strip()}")

    r = sh(
        "gh",
        "pr",
        "view",
        pr_url,
        "--repo",
        gh_repo,
        "--json",
        "mergeCommit",
        "--jq",
        ".mergeCommit.oid",
        capture=True,
    )
    sha = r.stdout.strip()
    console.print(f"[green]Merged ({sha[:12]})[/]")
    return sha


def wait_for_wheel(repo: str, version: str, org: str, timeout: int = 300):
    """Poll release assets until the wheel appears."""
    expected = wheel_filename(repo, version)
    console.print(f"Waiting for {expected}...")
    for _elapsed in range(0, timeout, 5):
        r = sh(
            "gh",
            "release",
            "view",
            f"v{version}",
            "--repo",
            f"{org}/{repo}",
            "--json",
            "assets",
            "-q",
            ".assets[].name",
            capture=True,
            check=False,
        )
        if expected in (r.stdout or ""):
            console.print("[green]Wheel available![/]")
            return
        time.sleep(5)
    die(f"Timed out waiting for {expected}")


# ── Planner ───────────────────────────────────────────────────────────────


def _step(pkg: str, seq: int, kind: StepKind, **params: Any) -> Step:
    return Step(id=f"{pkg}.{seq}.{kind}", kind=kind, package=pkg, params=params)


def plan_steps(pkg: PackagePlan, org: str, fork: str, name: str) -> list[Step]:
    """Generate the step sequence for one package based on its action."""
    r, s = pkg.repo, 0
    has_pr = bool(pkg.pr_branch)
    do_release = pkg.action in (Action.RELEASE_MASTER, Action.RELEASE_PR)
    needs_new_pr = pkg.action == Action.RELEASE_MASTER or (
        pkg.action == Action.DEPS_ONLY and not has_pr
    )

    title = f"{pkg.new_version} {name}".strip() if pkg.new_version else ""

    if has_pr:
        branch = pkg.pr_branch
        base_params = {"branch": branch, "source": "pr"}
    elif do_release:
        branch = f"chore/release-{pkg.new_version}"
        base_params = {"branch": branch, "base": "upstream/master"}
    else:
        suffix = slugify(name)
        branch = f"chore/bump-deps{'-' + suffix if suffix else ''}"
        base_params = {"branch": branch, "base": "upstream/master"}

    steps = [_step(r, s, StepKind.CLONE_SYNC)]
    s += 1
    steps.append(_step(r, s, StepKind.CHECKOUT, **base_params))
    s += 1
    for dep, ver in pkg.sibling_deps.items():
        steps.append(_step(r, s, StepKind.DEP_UPDATE, dep_repo=dep, dep_version=ver))
        s += 1
    if do_release:
        steps.append(_step(r, s, StepKind.VERSION_BUMP, version=pkg.new_version))
        s += 1
    steps.append(_step(r, s, StepKind.POETRY_LOCK))
    s += 1
    msg = f"chore: release {title}" if do_release else "chore: bump sibling deps"
    steps.append(_step(r, s, StepKind.GIT_COMMIT, message=msg))
    s += 1
    steps.append(_step(r, s, StepKind.GIT_PUSH, branch=branch, fork=fork))
    s += 1
    if needs_new_pr:
        pr_title = f"chore: release {title}" if do_release else "chore: bump sibling deps"
        pr_body = (
            f"Automated release bump to v{pkg.new_version}."
            if do_release
            else "Automated dependency update."
        )
        steps.append(_step(r, s, StepKind.PR_CREATE, branch=branch, title=pr_title, body=pr_body))
        s += 1
    if do_release:
        steps.append(_step(r, s, StepKind.PR_MERGE))
        s += 1
        steps.append(_step(r, s, StepKind.TAG, tag=f"v{pkg.new_version}", title=title))
        s += 1
        steps.append(_step(r, s, StepKind.RELEASE, tag=f"v{pkg.new_version}", title=title))
        s += 1
        steps.append(_step(r, s, StepKind.WHEEL_POLL, version=pkg.new_version))
    return steps


def generate_plan(
    chain: list[str],
    *,
    org: str,
    fork: str,
    release_name: str,
    version_step: str,
    uniform: bool,
    cache_dir: Path,
    stop_at: str | None = None,
    upgrade_pinned: bool = False,
    pr_specs: dict[str, int] | None = None,
) -> Plan:
    """Build a complete release plan for the given chain."""
    packages, all_steps = [], []
    released: dict[str, str] = {}
    planned_pins: dict[str, str] = {}

    for i, repo in enumerate(chain):
        current = latest_version(repo, org)
        gh_repo = f"{org}/{repo}"
        repo_dir = cache_dir / repo

        # Determine action — stop_at wins over pr_specs (deps-only, no release)
        pr_num: int | None = None
        pr_branch: str | None = None
        if pr_specs and repo in pr_specs:
            info = pr_info(pr_specs[repo], gh_repo)
            if info.get("state") != "OPEN":
                die(
                    f"PR #{pr_specs[repo]} for {repo} is {info.get('state', 'unknown')} — must be OPEN"
                )
            pr_num, pr_branch = pr_specs[repo], info["headRefName"]

        if repo == stop_at:
            action = Action.DEPS_ONLY
        elif pr_num is not None:
            action = Action.RELEASE_PR
        else:
            action = Action.RELEASE_MASTER

        # Version
        level = version_step if (i == 0 or uniform) else "patch"
        new_ver = bump_version(current, level) if action != Action.DEPS_ONLY else None

        # Resolve sibling deps
        sibling_deps: dict[str, str] = {}
        for dep in DEPS.get(repo, []):
            if dep in released:
                ver = released[dep]
            else:
                # Check planned pins from siblings released in this run
                ver = None
                for other in DEPS[repo]:
                    if other != dep and other in released:
                        key = f"{other}:{dep}"
                        if key in planned_pins:
                            ver = planned_pins[key]
                            break
                if not ver:
                    ver = pinned_version(repo_dir / "pyproject.toml", dep, org)
                if not ver or upgrade_pinned:
                    ver = latest_version(dep, org)
            sibling_deps[dep] = ver
            planned_pins[f"{repo}:{dep}"] = ver

        pkg = PackagePlan(
            repo=repo,
            action=action,
            current_version=current,
            new_version=new_ver,
            pr_number=pr_num,
            pr_branch=pr_branch,
            sibling_deps=sibling_deps,
        )
        packages.append(pkg)
        all_steps.extend(plan_steps(pkg, org, fork, release_name))
        if new_ver:
            released[repo] = new_ver

    return Plan(
        packages=packages,
        steps=all_steps,
        gh_org=org,
        gh_fork=fork,
        release_name=release_name,
    )


# ── Executor ──────────────────────────────────────────────────────────────


def _find_pr_url(package: str, plan: Plan) -> str:
    """Find the PR URL for a package — from pr_create result or PR number."""
    for s in plan.steps:
        if s.package == package and s.kind == StepKind.PR_CREATE and s.result.get("pr_url"):
            return s.result["pr_url"]
    # Fall back to PR number
    for pkg in plan.packages:
        if pkg.repo == package and pkg.pr_number:
            return str(pkg.pr_number)
    die(f"No PR URL found for {package}")


def execute_step(step: Step, plan: Plan, ctx: Ctx):
    """Execute a single step."""
    repo_dir = ctx.cache_dir / step.package
    gh_repo = f"{plan.gh_org}/{step.package}"
    p = step.params

    match step.kind:
        case StepKind.CLONE_SYNC:
            ensure_clone(step.package, ctx.cache_dir, plan.gh_org, plan.gh_fork)

        case StepKind.CHECKOUT:
            if p.get("source") == "pr":
                sh("git", "fetch", "origin", p["branch"], cwd=repo_dir)
                sh("git", "checkout", "-B", p["branch"], f"origin/{p['branch']}", cwd=repo_dir)
            else:
                sh("git", "checkout", "-B", p["branch"], p["base"], cwd=repo_dir)

        case StepKind.VERSION_BUMP:
            set_version_toml(repo_dir / "pyproject.toml", p["version"])

        case StepKind.DEP_UPDATE:
            set_dep_url(repo_dir / "pyproject.toml", p["dep_repo"], p["dep_version"], plan.gh_org)

        case StepKind.POETRY_LOCK:
            sh("poetry", "lock", cwd=repo_dir)

        case StepKind.GIT_COMMIT:
            sh("git", "add", "pyproject.toml", "poetry.lock", cwd=repo_dir)
            # Idempotent: skip if HEAD already carries this commit message
            r = sh("git", "log", "-1", "--format=%s", cwd=repo_dir, capture=True)
            if r.stdout.strip() == p["message"]:
                console.print("[dim]Already committed — skipping.[/]")
            else:
                sh("git", "commit", "-m", p["message"], cwd=repo_dir)

        case StepKind.GIT_PUSH:
            sh("git", "push", "-u", "origin", p["branch"], "--force-with-lease", cwd=repo_dir)

        case StepKind.PR_CREATE:
            # Idempotent: reuse existing PR for this head branch
            r = sh(
                "gh",
                "pr",
                "view",
                "--repo",
                gh_repo,
                "--head",
                f"{plan.gh_fork}:{p['branch']}",
                "--json",
                "url",
                "--jq",
                ".url",
                capture=True,
                check=False,
            )
            if r.returncode == 0 and r.stdout.strip():
                step.result["pr_url"] = r.stdout.strip()
                console.print(f"PR already exists: {step.result['pr_url']}")
            else:
                r = sh(
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    gh_repo,
                    "--base",
                    "master",
                    "--head",
                    f"{plan.gh_fork}:{p['branch']}",
                    "--title",
                    p["title"],
                    "--body",
                    p["body"],
                    "--label",
                    "automated-release",
                    capture=True,
                )
                step.result["pr_url"] = r.stdout.strip()
                console.print(f"PR created: {step.result['pr_url']}")

        case StepKind.PR_MERGE:
            pr_url = _find_pr_url(step.package, plan)
            # Idempotent: if already merged, just capture the SHA
            st = pr_state(pr_url, gh_repo)
            if st == "MERGED":
                r = sh(
                    "gh",
                    "pr",
                    "view",
                    pr_url,
                    "--repo",
                    gh_repo,
                    "--json",
                    "mergeCommit",
                    "--jq",
                    ".mergeCommit.oid",
                    capture=True,
                )
                step.result["merge_sha"] = r.stdout.strip()
                console.print(f"[dim]Already merged ({step.result['merge_sha'][:12]})[/]")
            else:
                check_result = wait_for_checks(pr_url, gh_repo, ctx)
                if check_result == "merged":
                    r = sh(
                        "gh",
                        "pr",
                        "view",
                        pr_url,
                        "--repo",
                        gh_repo,
                        "--json",
                        "mergeCommit",
                        "--jq",
                        ".mergeCommit.oid",
                        capture=True,
                    )
                    step.result["merge_sha"] = r.stdout.strip()
                else:
                    step.result["merge_sha"] = squash_merge(pr_url, gh_repo)

        case StepKind.TAG:
            sh("git", "fetch", "upstream", cwd=repo_dir)
            merge_sha = None
            for s in plan.steps:
                if s.package == step.package and s.kind == StepKind.PR_MERGE:
                    merge_sha = s.result.get("merge_sha")
            target = merge_sha or "upstream/master"
            # Idempotent: skip if tag already exists on the expected target
            r = sh(
                "git", "rev-parse", f"refs/tags/{p['tag']}", cwd=repo_dir, capture=True, check=False
            )
            if r.returncode == 0:
                console.print(f"[dim]Tag {p['tag']} already exists — skipping.[/]")
            else:
                sh("git", "tag", "-f", p["tag"], target, cwd=repo_dir)
                sh("git", "push", "upstream", p["tag"], cwd=repo_dir)

        case StepKind.RELEASE:
            # Idempotent: skip if release already exists
            r = sh(
                "gh",
                "release",
                "view",
                p["tag"],
                "--repo",
                gh_repo,
                "--json",
                "tagName",
                capture=True,
                check=False,
            )
            if r.returncode == 0:
                console.print(f"[dim]Release {p['tag']} already exists — skipping.[/]")
            else:
                sh(
                    "gh",
                    "release",
                    "create",
                    p["tag"],
                    "--repo",
                    gh_repo,
                    "--title",
                    p["title"],
                    "--generate-notes",
                )

        case StepKind.WHEEL_POLL:
            wait_for_wheel(step.package, p["version"], plan.gh_org, ctx.wheel_timeout)


def simulate_step(step: Step, plan: Plan, ctx: Ctx):
    """Simulate a step — log what would happen, verify preconditions."""
    p = step.params
    match step.kind:
        case StepKind.CLONE_SYNC:
            ensure_clone(step.package, ctx.cache_dir, plan.gh_org, plan.gh_fork)
        case StepKind.DEP_UPDATE:
            released_in_plan = {pkg.repo for pkg in plan.packages if pkg.new_version}
            if p["dep_repo"] not in released_in_plan:
                r = sh(
                    "gh",
                    "release",
                    "view",
                    f"v{p['dep_version']}",
                    "--repo",
                    f"{plan.gh_org}/{p['dep_repo']}",
                    "--json",
                    "assets",
                    "-q",
                    ".assets[].name",
                    capture=True,
                    check=False,
                )
                expected = wheel_filename(p["dep_repo"], p["dep_version"])
                if expected not in (r.stdout or ""):
                    console.print(f"[yellow]Warning: wheel {expected} not found[/]")
        case StepKind.PR_MERGE:
            step.result["merge_sha"] = "(simulated)"
        case _:
            pass
    console.print(f"[yellow][simulate][/] {step.id}: {step.kind.value} {p}")


def save_plan(plan: Plan, path: Path):
    """Persist plan to disk for crash recovery."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2))


def execute_plan(plan: Plan, *, mode: str, ctx: Ctx) -> Plan:
    """Walk plan steps. mode = 'simulate' | 'execute' | 'resume'."""
    for step in plan.steps:
        if mode == "resume" and step.status == "completed":
            console.print(f"[dim]Skipping completed: {step.id}[/]")
            continue

        pkg_label = f"[bold cyan]{step.package}[/]"
        console.print(f"\n{pkg_label} {step.kind.value}")

        if mode == "simulate":
            simulate_step(step, plan, ctx)
            step.status = "completed"
        else:
            step.status = "running"
            if ctx.plan_path:
                save_plan(plan, ctx.plan_path)
            try:
                execute_step(step, plan, ctx)
                step.status = "completed"
            except (subprocess.CalledProcessError, SystemExit) as exc:
                step.status = "failed"
                step.result["error"] = str(exc)
                if ctx.plan_path:
                    save_plan(plan, ctx.plan_path)
                raise
            if ctx.plan_path:
                save_plan(plan, ctx.plan_path)
    return plan


# ── CLI ───────────────────────────────────────────────────────────────────


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _common_ctx(
    org: str,
    fork: str,
    cache_dir: str,
    pretend: bool,
    yes: bool,
    skip_checks: bool,
    check_timeout: int,
    *,
    require_fork: bool = True,
) -> tuple[str, str, Path, Ctx]:
    if require_fork:
        fork = fork or die("TEROK_GH_FORK is not set (e.g. TEROK_GH_FORK=sliwowitz)")
    cd = Path(cache_dir)
    cd.mkdir(parents=True, exist_ok=True)
    return (
        org,
        fork,
        cd,
        Ctx(
            cache_dir=cd,
            dry_run=pretend,
            auto_yes=yes,
            skip_checks=skip_checks,
            check_timeout=check_timeout,
        ),
    )


def _parse_pr_specs(specs: str) -> dict[str, int]:
    result = {}
    for part in specs.split(","):
        part = part.strip()
        if ":" not in part or part.count(":") != 1:
            die(f"Malformed --from-prs entry '{part}': expected repo:PR (e.g. sandbox:42)")
        repo, num = part.split(":")
        if not repo or not num:
            die(f"Malformed --from-prs entry '{part}': repo and PR number must be non-empty")
        try:
            result[normalise(repo)] = int(num)
        except ValueError:
            die(f"Malformed --from-prs entry '{part}': PR number must be an integer")
    return result


def _resolve_chain(
    repos: tuple[str, ...],
    from_prs: str | None,
    *,
    open_top: bool = False,
) -> tuple[list[str], str | None, dict[str, int] | None]:
    """Parse CLI args into (chain, stop_at, pr_specs).

    Syntax:
        sandbox             → release a single package
        sandbox..terok      → chain from sandbox to terok
        --from-prs s:42     → single PR release
        --from-prs s:42,e:5 → PR chain

    With ``--open-top``, the last package in the chain gets DEPS_ONLY
    (deps updated, no version bump or merge).
    """
    if from_prs:
        pr_specs = _parse_pr_specs(from_prs)
        chain_repos = [r for r in CHAIN if r in pr_specs]
        if not chain_repos:
            die("No known repos in --from-prs")
        chain = build_chain(chain_repos[0], chain_repos[-1])
        return chain, chain[-1] if open_top else None, pr_specs

    if not repos:
        die("Specify a repo, a range (sandbox..terok), or --from-prs")

    if len(repos) > 1:
        die("Use 'sandbox..terok' range syntax instead of two separate arguments")

    spec = repos[0]
    if ".." in spec:
        start_s, end_s = spec.split("..", 1)
        chain = build_chain(normalise(start_s), normalise(end_s))
        return chain, chain[-1] if open_top else None, None

    # Single package — release just this one
    repo = normalise(spec)
    return [repo], None, None


@click.group()
def cli():
    """Cascading release chain for the terok package family."""
    _check_gh_version()


@cli.command()
@click.argument("repos", nargs=-1)
@click.option("--version-step", default="patch", type=click.Choice(["major", "minor", "patch"]))
@click.option("--version-step-uniform", is_flag=True)
@click.option("-n", "--name", "release_name", default="", help="Release name suffix")
@click.option("-y", "--yes", is_flag=True, help="Auto-approve normal confirmations")
@click.option("-p", "--pretend", is_flag=True, help="Dry run")
@click.option("--skip-checks", is_flag=True)
@click.option("--check-timeout", default=1800, type=int)
@click.option("--upgrade-pinned", is_flag=True)
@click.option("--from-prs", default=None, help="repo:PR pairs (e.g. sandbox:42,executor:55)")
@click.option("--open-top", is_flag=True, help="Top package: update deps only, no release")
@click.option("--org", default=_env("TEROK_GH_ORG", "terok-ai"))
@click.option("--fork", default=_env("TEROK_GH_FORK"))
@click.option(
    "--cache-dir", default=_env("TEROK_RELEASE_DIR", str(Path.home() / ".cache/terok-release"))
)
def quick(
    repos,
    version_step,
    version_step_uniform,
    release_name,
    yes,
    pretend,
    skip_checks,
    check_timeout,
    upgrade_pinned,
    from_prs,
    open_top,
    org,
    fork,
    cache_dir,
):
    """Plan and execute a release chain in one shot.

    \b
    Examples:
      quick sandbox                    Release a single package
      quick sandbox..terok             Chain from sandbox to terok
      quick sandbox..terok --open-top  Chain, terok gets deps-only PR
      quick --from-prs sandbox:155     Release from a PR
      quick --from-prs s:155,e:167,t:706 --open-top
                                       PR chain, terok gets deps updated only
    """
    org, fork, cd, ctx = _common_ctx(org, fork, cache_dir, pretend, yes, skip_checks, check_timeout)

    chain, stop_at, pr_specs = _resolve_chain(repos, from_prs, open_top=open_top)

    # Prompt for release name if not given
    if not release_name and not pretend:
        release_name = click.prompt("Release name (empty for version-only)", default="")

    # Sync clones
    console.print("\n[bold]Syncing clones...[/]")
    for repo in chain:
        ensure_clone(repo, cd, org, fork)

    plan = generate_plan(
        chain,
        org=org,
        fork=fork,
        release_name=release_name,
        version_step=version_step,
        uniform=version_step_uniform,
        cache_dir=cd,
        stop_at=stop_at,
        upgrade_pinned=upgrade_pinned,
        pr_specs=pr_specs,
    )

    # Preview
    console.print("\n[bold]Release plan:[/]\n")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", width=3)
    table.add_column("Package", style="cyan")
    table.add_column("Action")
    table.add_column("Version")
    table.add_column("Deps")
    for i, pkg in enumerate(plan.packages, 1):
        ver = (
            f"{pkg.current_version} -> [green]{pkg.new_version}[/]"
            if pkg.new_version
            else pkg.current_version
        )
        dep_str = ", ".join(f"{d} v{v}" for d, v in pkg.sibling_deps.items())
        table.add_row(str(i), pkg.repo, pkg.action.value, ver, dep_str)
    console.print(table)

    if not pretend and not yes:
        click.confirm("\nProceed?", default=True, abort=True)

    # Save plan
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = slugify(release_name) or "release"
    plan_path = cd / "plans" / f"{ts}-{slug}.json"
    ctx.plan_path = plan_path
    save_plan(plan, plan_path)
    console.print(f"\nPlan saved: {plan_path}")

    # Execute
    mode = "simulate" if pretend else "execute"
    start_ts = time.monotonic()
    execute_plan(plan, mode=mode, ctx=ctx)
    elapsed = time.monotonic() - start_ts

    # Summary
    prefix = "[yellow][pretend][/] " if pretend else ""
    console.print(f"\n{prefix}[bold green]All releases complete![/]\n")
    for pkg in plan.packages:
        if pkg.new_version:
            console.print(f"  [green]*[/] {pkg.repo} v{pkg.new_version}")
        else:
            console.print(f"  [yellow]*[/] {pkg.repo}  (deps only)")
    console.print(f"\nElapsed: {elapsed:.0f}s")


@cli.command("open")
@click.argument("branch")
@click.argument("repos", nargs=-1, required=True)
@click.option("-p", "--pretend", is_flag=True, help="Dry run")
@click.option("--org", default=_env("TEROK_GH_ORG", "terok-ai"))
@click.option("--fork", default=_env("TEROK_GH_FORK"))
@click.option(
    "--cache-dir", default=_env("TEROK_RELEASE_DIR", str(Path.home() / ".cache/terok-release"))
)
def open_chain(branch, repos, pretend, org, fork, cache_dir):
    """Open a PR chain for cross-cutting development.

    Creates a branch in each repo, wires sibling deps as Poetry git-branch
    references, and opens PRs.  During an open chain, use `poetry install`
    for development — not pipx.

    \b
    Examples:
        terok-release-chain.py open feat/comms dbus
        terok-release-chain.py open feat/my-feature sandbox terok
    """
    org, fork, cd, ctx = _common_ctx(org, fork, cache_dir, pretend, True, True, 0)
    start = normalise(repos[0])
    end = normalise(repos[1]) if len(repos) > 1 else None
    chain = build_chain(start, end)

    console.print(f"\n[bold]Opening PR chain:[/] {branch}")
    console.print(f"  Repos: {' '.join(chain)}\n")

    for repo in chain:
        ensure_clone(repo, cd, org, fork)
    console.print()

    pr_urls: list[str] = []
    for i, repo in enumerate(chain):
        repo_dir = cd / repo
        gh_repo = f"{org}/{repo}"

        console.print(f"[cyan]{repo}[/]: creating branch {branch}")
        if not ctx.dry_run:
            sh("git", "checkout", "-B", branch, "upstream/master", cwd=repo_dir)

        # Wire in-chain deps as git-branch references (skip the leaf repo)
        if i > 0:
            for dep in DEPS.get(repo, []):
                if dep in chain:
                    console.print(f"  wiring {dep} -> branch {branch}")
                    if not ctx.dry_run:
                        set_branch_dep(repo_dir / "pyproject.toml", dep, branch, fork)
            if not ctx.dry_run:
                sh("poetry", "lock", cwd=repo_dir)
                sh("git", "add", "pyproject.toml", "poetry.lock", cwd=repo_dir)
                sh("git", "commit", "-m", f"chore: wire {branch} branch deps", cwd=repo_dir)

        console.print("  pushing to fork")
        if not ctx.dry_run:
            sh("git", "push", "-u", "origin", branch, "--force-with-lease", cwd=repo_dir)

        # Open PR (detect "already exists" gracefully)
        if ctx.dry_run:
            console.print("  [yellow][pretend][/] would create PR")
            pr_urls.append("(pretend)")
        else:
            r = sh(
                "gh",
                "pr",
                "view",
                "--repo",
                gh_repo,
                "--head",
                f"{fork}:{branch}",
                "--json",
                "url",
                "--jq",
                ".url",
                capture=True,
                check=False,
            )
            if r.returncode == 0 and r.stdout.strip():
                pr_urls.append(r.stdout.strip())
                console.print(f"  PR already exists: {pr_urls[-1]}")
            else:
                r = sh(
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    gh_repo,
                    "--base",
                    "master",
                    "--head",
                    f"{fork}:{branch}",
                    "--title",
                    branch,
                    "--body",
                    f"Part of `{branch}` PR chain.",
                    capture=True,
                )
                pr_urls.append(r.stdout.strip())
                console.print(f"  [green]PR created: {pr_urls[-1]}[/]")
        console.print()

    console.print("[bold green]PR chain opened![/]\n")
    for repo, url in zip(chain, pr_urls, strict=True):
        console.print(f"  {repo}  {url}")
    console.print()


@cli.command("plan")
@click.argument("repos", nargs=-1)
@click.option("-o", "--output", type=click.Path(), help="Output plan file")
@click.option("--version-step", default="patch", type=click.Choice(["major", "minor", "patch"]))
@click.option("--version-step-uniform", is_flag=True)
@click.option("-n", "--name", "release_name", default="")
@click.option("--upgrade-pinned", is_flag=True)
@click.option("--from-prs", default=None)
@click.option("--open-top", is_flag=True, help="Top package: update deps only, no release")
@click.option("--org", default=_env("TEROK_GH_ORG", "terok-ai"))
@click.option("--fork", default=_env("TEROK_GH_FORK"))
@click.option(
    "--cache-dir", default=_env("TEROK_RELEASE_DIR", str(Path.home() / ".cache/terok-release"))
)
def plan_cmd(
    repos,
    output,
    version_step,
    version_step_uniform,
    release_name,
    upgrade_pinned,
    from_prs,
    open_top,
    org,
    fork,
    cache_dir,
):
    """Generate a release plan without executing it."""
    org, fork, cd, ctx = _common_ctx(org, fork, cache_dir, True, True, True, 0)
    chain, stop_at, pr_specs = _resolve_chain(repos, from_prs, open_top=open_top)
    if not release_name:
        console.print(
            "[yellow]Warning: no release name (-n). Release titles will be version-only.[/]"
        )

    for repo in chain:
        ensure_clone(repo, cd, org, fork)

    plan = generate_plan(
        chain,
        org=org,
        fork=fork,
        release_name=release_name,
        version_step=version_step,
        uniform=version_step_uniform,
        cache_dir=cd,
        stop_at=stop_at,
        upgrade_pinned=upgrade_pinned,
        pr_specs=pr_specs,
    )

    out = Path(output) if output else cd / "plans" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    save_plan(plan, out)
    console.print(f"Plan written to {out}")


@cli.command()
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("--org", default=_env("TEROK_GH_ORG", "terok-ai"))
@click.option("--fork", default=_env("TEROK_GH_FORK"))
@click.option(
    "--cache-dir", default=_env("TEROK_RELEASE_DIR", str(Path.home() / ".cache/terok-release"))
)
def simulate(plan_file, org, fork, cache_dir):
    """Validate a plan against real repo state."""
    org, fork, cd, ctx = _common_ctx(org, fork, cache_dir, True, True, True, 0, require_fork=False)
    plan = Plan.model_validate_json(Path(plan_file).read_text())
    # Fall back to plan-embedded values when CLI/env didn't provide them
    plan.gh_org = org or plan.gh_org
    plan.gh_fork = fork or plan.gh_fork
    execute_plan(plan, mode="simulate", ctx=ctx)
    console.print("\n[green]Simulation complete — no issues found.[/]")


@cli.command()
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("-y", "--yes", is_flag=True)
@click.option("--skip-checks", is_flag=True)
@click.option("--check-timeout", default=1800, type=int)
@click.option("--org", default=_env("TEROK_GH_ORG", "terok-ai"))
@click.option("--fork", default=_env("TEROK_GH_FORK"))
@click.option(
    "--cache-dir", default=_env("TEROK_RELEASE_DIR", str(Path.home() / ".cache/terok-release"))
)
def execute(plan_file, yes, skip_checks, check_timeout, org, fork, cache_dir):
    """Execute (or resume) a release plan."""
    org, fork, cd, ctx = _common_ctx(
        org, fork, cache_dir, False, yes, skip_checks, check_timeout, require_fork=False
    )
    plan_path = Path(plan_file)
    plan = Plan.model_validate_json(plan_path.read_text())
    plan.gh_org = org or plan.gh_org
    plan.gh_fork = fork or plan.gh_fork or die("Fork required: set TEROK_GH_FORK or embed in plan")
    ctx.plan_path = plan_path

    has_completed = any(s.status == "completed" for s in plan.steps)
    mode = "resume" if has_completed else "execute"
    if has_completed:
        console.print("[yellow]Resuming partially-executed plan...[/]")

    execute_plan(plan, mode=mode, ctx=ctx)
    console.print("\n[bold green]All releases complete![/]")


if __name__ == "__main__":
    cli()
