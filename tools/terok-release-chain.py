#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
"""Cascading release chain for the terok package family.

Plan-then-execute architecture: generate a release plan (JSON), validate
it, then execute step-by-step with crash-recovery.  Supports full and
GitHub-prerelease releases, and the "release from PR" workflow for
chained feature branches.

Usage:
    python3 tools/terok-release-chain.py quick sandbox              # single package
    python3 tools/terok-release-chain.py quick sandbox..terok        # chain
    python3 tools/terok-release-chain.py quick sandbox..terok --open-top  # chain, top=deps-only
    python3 tools/terok-release-chain.py quick --from-prs sandbox:42,executor:55
    python3 tools/terok-release-chain.py quick --from-prs s:42,e:55,t:706 --open-top
    python3 tools/terok-release-chain.py open feat/comms clearance
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
import urllib.request
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


# ── Chain ─────────────────────────────────────────────────────────────────

CHAIN = ["terok-clearance", "terok-shield", "terok-sandbox", "terok-executor", "terok"]

# When you add a new inter-package dep, update this table and the
# consuming package's ``pyproject.toml`` in the same PR — the planner
# cross-checks the two and aborts the next release otherwise.
DEPS: DepGraph = {
    "terok-clearance": [],
    "terok-shield": ["terok-clearance"],
    "terok-sandbox": ["terok-shield"],
    "terok-executor": ["terok-sandbox"],
    "terok": ["terok-executor", "terok-sandbox", "terok-shield", "terok-clearance"],
}

ALIASES = {repo.removeprefix("terok-"): repo for repo in CHAIN} | {repo: repo for repo in CHAIN}


# ── Tuning ────────────────────────────────────────────────────────────────
#
# Seconds everywhere unless noted.

DEFAULT_CHECK_TIMEOUT = 1800  # 30 min — long enough for a full CI matrix
DEFAULT_WHEEL_TIMEOUT = 300

CHECK_POLL_INTERVAL = 2
CHECK_GRACE_WINDOW = 30  # leniency before missing check data becomes a hard fail
CHECK_STATE_RECHECK = 10  # cadence for PR-state (MERGED/CLOSED) lookups

WHEEL_POLL_INTERVAL = 5
WHEEL_HEAD_TIMEOUT = 10  # per HEAD probe of the actual download URL

MERGE_RACE_POLL_COUNT = 15
MERGE_RACE_POLL_INTERVAL = 2

RELEASE_BRANCH_PREFIX = "chore/release-"
BUMP_DEPS_BRANCH_PREFIX = "chore/bump-deps"
RELEASE_COMMIT_PREFIX = "chore: release"
BUMP_DEPS_COMMIT = "chore: bump sibling deps"
AUTOMATED_RELEASE_LABEL = "automated-release"


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


# ── Domain types ──────────────────────────────────────────────────────────

# Package → in-chain packages it depends on.
type DepGraph = dict[str, list[str]]

# Sibling package → version string to pin for it.
type SiblingVersions = dict[str, str]

# Package → GitHub PR number (the release-from-PR workflow).
type PrSpecs = dict[str, int]

# Package → new version string, for packages already processed in this run.
type ReleasedVersions = dict[str, str]


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
    prerelease: bool = False
    """When True, publish as a GitHub prerelease (hidden from the "Latest"
    badge on the repo homepage).  Useful for batching half-done work that
    downstream packages need to pin against, without promoting it to the
    public release pointer."""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ── Runtime context ───────────────────────────────────────────────────────


@dataclass
class Ctx:
    """Mutable runtime state threaded through executor calls."""

    cache_dir: Path
    dry_run: bool = False
    auto_yes: bool = False
    skip_checks: bool = False
    check_timeout: int = DEFAULT_CHECK_TIMEOUT
    wheel_timeout: int = DEFAULT_WHEEL_TIMEOUT
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


# ── Dep-graph verifier ────────────────────────────────────────────────────
#
# A stale sibling pin in a pyproject.toml (or a missing entry in DEPS)
# would ship a release with a broken transitive pin, so: reconcile the
# two before planning; on any drift, fail fast with a diff.


def _discover_sibling_deps(pyproject_path: Path, family: list[str]) -> list[str]:
    """Members of *family* that appear as dependency keys in ``pyproject_path``.

    ``family`` must be the full package family (typically ``CHAIN``) — not a
    slice.  A sliced family would miss legitimate upstream pins and produce
    false drift reports.  Matches both hyphen (``terok-shield``) and
    underscore (``terok_shield``) forms since Poetry accepts either.
    """
    _, deps = _toml_deps(pyproject_path)
    return [m for m in family if m in deps or pkg_name(m) in deps]


def _verify_dep_graph(chain: list[str], cache_dir: Path) -> DepGraph:
    """Cross-check vendored ``DEPS`` against each cloned ``pyproject.toml``.

    Walks the whole chain first, collects every discrepancy, then calls
    ``die()`` once with a combined diff — one bad run should surface *all*
    drift in a single shot so the operator can fix everything before the
    next attempt, not one mismatch at a time.  Returns the verified live
    graph (identical to ``DEPS`` after a successful check).
    """
    live: DepGraph = {}
    mismatches: list[str] = []
    for repo in chain:
        found = _discover_sibling_deps(cache_dir / repo / "pyproject.toml", CHAIN)
        declared = DEPS.get(repo, [])
        live[repo] = found
        if set(found) != set(declared):
            mismatches.append(
                f"  {repo}:\n"
                f"    declared in DEPS:   {declared or '[]'}\n"
                f"    found in pyproject: {found or '[]'}"
            )
    if mismatches:
        die(
            "Dependency graph mismatch between vendored DEPS and live pyproject.toml:\n\n"
            + "\n".join(mismatches)
            + "\n\nReconcile before releasing: either update DEPS in this script "
            "(if the sibling dep is legitimate and newly added) or remove the "
            "stale pin from the package's pyproject.toml."
        )
    return live


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
    """Block until CI settles on the PR.

    Returns ``"passed"`` when all checks are green, or ``"merged"`` if
    somebody merged the PR out-of-band while we were waiting.  On a
    check failure, prompts the operator to force-merge; on a flat
    timeout, calls ``die()``.  The grace window tolerates the brief gap
    between push and check registration.
    """
    if ctx.skip_checks:
        console.print("[yellow]Skipping CI checks[/]")
        return "passed"
    if ctx.dry_run:
        console.print(f"[yellow][pretend][/] Would wait for checks on {pr_url}")
        return "passed"

    console.print(f"Waiting for PR checks (timeout {ctx.check_timeout}s)...")

    for elapsed in range(0, ctx.check_timeout, CHECK_POLL_INTERVAL):
        if elapsed and elapsed % CHECK_STATE_RECHECK == 0:
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
            if elapsed < CHECK_GRACE_WINDOW:
                time.sleep(CHECK_POLL_INTERVAL)
                continue
            detail = (r.stderr or r.stdout or "").strip()
            die(f"gh pr checks failed (exit {r.returncode}): {detail}")

        checks = json.loads(r.stdout) if r.stdout.strip() else []
        # Fail-closed: an empty check list is never "passed".  Keep polling
        # until real checks appear or ctx.check_timeout fires — operators
        # whose repo genuinely has no CI must say so with --skip-checks.
        if not checks:
            time.sleep(CHECK_POLL_INTERVAL)
            continue

        pending = sum(1 for c in checks if c["bucket"] == "pending")
        failing = [c for c in checks if c["bucket"] in ("fail", "cancel")]

        if pending:
            time.sleep(CHECK_POLL_INTERVAL)
            continue
        if not failing:
            console.print("[green]All checks passed![/]")
            return "passed"

        console.print("[yellow]Checks failed:[/]")
        for c in failing:
            console.print(f"  {c['name']}: {c['bucket']}")
        if ctx.auto_yes:
            console.print("[yellow]Force-merging (--yes)[/]")
        elif not alert_confirm("Force merge anyway?", default=False):
            die("Aborted.")
        return "passed"

    die(f"Timed out after {ctx.check_timeout}s")


def _gh_merge_commit(pr_url: str, gh_repo: str) -> str:
    """Commit SHA that the PR was merged into."""
    r = sh(
        "gh", "pr", "view", pr_url, "--repo", gh_repo,
        "--json", "mergeCommit", "--jq", ".mergeCommit.oid",
        capture=True,
    )  # fmt: skip
    return r.stdout.strip()


def squash_merge(pr_url: str, gh_repo: str) -> str:
    """Squash-merge the PR and return the resulting master commit SHA.

    Tolerates a narrow race: ``gh pr merge`` can report "already in
    progress" or "already merged" when another automation (or a fast
    operator) got there first — in that case we poll PR state briefly
    rather than giving up.
    """
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
            for _ in range(MERGE_RACE_POLL_COUNT):
                if pr_state(pr_url, gh_repo) == "MERGED":
                    break
                time.sleep(MERGE_RACE_POLL_INTERVAL)
            else:
                die(
                    f"PR still not merged after {MERGE_RACE_POLL_COUNT * MERGE_RACE_POLL_INTERVAL}s"
                )
        else:
            die(f"Merge failed: {err.strip()}")

    sha = _gh_merge_commit(pr_url, gh_repo)
    console.print(f"[green]Merged ({sha[:12]})[/]")
    return sha


def _wheel_downloadable(url: str) -> bool:
    """Whether the wheel is actually downloadable right now (past the GitHub CDN)."""
    req = urllib.request.Request(url, method="HEAD")  # noqa: S310 — GitHub release URL
    try:
        with urllib.request.urlopen(req, timeout=WHEEL_HEAD_TIMEOUT) as resp:  # noqa: S310
            return resp.status == 200  # noqa: PLR2004
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def wait_for_wheel(repo: str, version: str, org: str, timeout: int = DEFAULT_WHEEL_TIMEOUT) -> None:
    """Block until the released wheel is downloadable.

    Two-phase check: the GitHub API lists the asset name first, then the
    actual download URL goes live a few seconds later as the CDN
    propagates.  Only both together mean consumers can poetry-resolve it.
    """
    expected = wheel_filename(repo, version)
    url = wheel_url(org, repo, version)
    console.print(f"Waiting for {expected}...")
    api_ready = False
    for _elapsed in range(0, timeout, WHEEL_POLL_INTERVAL):
        if not api_ready:
            r = sh(
                "gh", "release", "view", f"v{version}", "--repo", f"{org}/{repo}",
                "--json", "assets", "-q", ".assets[].name",
                capture=True, check=False,
            )  # fmt: skip
            if expected in (r.stdout or ""):
                api_ready = True
        if api_ready and _wheel_downloadable(url):
            console.print("[green]Wheel available![/]")
            return
        time.sleep(WHEEL_POLL_INTERVAL)
    die(f"Timed out waiting for {expected}")


# ── Planner ───────────────────────────────────────────────────────────────


def _step(pkg: str, seq: int, kind: StepKind, **params: Any) -> Step:
    return Step(id=f"{pkg}.{seq}.{kind}", kind=kind, package=pkg, params=params)


def _branch_for(pkg: PackagePlan, release_name: str) -> tuple[str, dict[str, str]]:
    """Branch name + checkout parameters for *pkg*'s work on this run."""
    if pkg.pr_branch:
        return pkg.pr_branch, {"branch": pkg.pr_branch, "source": "pr"}
    if pkg.action in (Action.RELEASE_MASTER, Action.RELEASE_PR):
        branch = f"{RELEASE_BRANCH_PREFIX}{pkg.new_version}"
        return branch, {"branch": branch, "base": "upstream/master"}
    suffix = slugify(release_name)
    branch = f"{BUMP_DEPS_BRANCH_PREFIX}{'-' + suffix if suffix else ''}"
    return branch, {"branch": branch, "base": "upstream/master"}


def plan_steps(pkg: PackagePlan, org: str, fork: str, name: str) -> list[Step]:
    """Linear step sequence that realises one package's work in the plan."""
    do_release = pkg.action in (Action.RELEASE_MASTER, Action.RELEASE_PR)
    needs_new_pr = pkg.action == Action.RELEASE_MASTER or (
        pkg.action == Action.DEPS_ONLY and not pkg.pr_branch
    )

    branch, checkout_params = _branch_for(pkg, name)
    title = f"{pkg.new_version} {name}".strip() if pkg.new_version else ""
    commit_msg = f"{RELEASE_COMMIT_PREFIX} {title}" if do_release else BUMP_DEPS_COMMIT

    steps: list[Step] = []

    def add(kind: StepKind, **params: Any) -> None:
        steps.append(_step(pkg.repo, len(steps), kind, **params))

    add(StepKind.CLONE_SYNC)
    add(StepKind.CHECKOUT, **checkout_params)
    for dep, ver in pkg.sibling_deps.items():
        add(StepKind.DEP_UPDATE, dep_repo=dep, dep_version=ver)
    if do_release:
        add(StepKind.VERSION_BUMP, version=pkg.new_version)
    add(StepKind.POETRY_LOCK)
    add(StepKind.GIT_COMMIT, message=commit_msg)
    add(StepKind.GIT_PUSH, branch=branch, fork=fork)
    if needs_new_pr:
        pr_body = (
            f"Automated release bump to v{pkg.new_version}."
            if do_release
            else "Automated dependency update."
        )
        add(StepKind.PR_CREATE, branch=branch, title=commit_msg, body=pr_body)
    if do_release:
        tag = f"v{pkg.new_version}"
        add(StepKind.PR_MERGE)
        add(StepKind.TAG, tag=tag, title=title)
        add(StepKind.RELEASE, tag=tag, title=title)
        add(StepKind.WHEEL_POLL, version=pkg.new_version)
    return steps


def _resolve_sibling_version(
    dep: str,
    repo_deps: list[str],
    released: ReleasedVersions,
    planned_pins: dict[str, str],
    repo_dir: Path,
    org: str,
    upgrade_pinned: bool,
) -> str:
    """Version to pin for *dep* in the current repo — most-local first."""
    if dep in released:
        return released[dep]
    # Two downstream repos sharing an upstream must agree on its version
    # even if neither is being released in this run.
    for other in repo_deps:
        if other == dep or other not in released:
            continue
        if from_sibling := planned_pins.get(f"{other}:{dep}"):
            return from_sibling
    current = pinned_version(repo_dir / "pyproject.toml", dep, org)
    if current and not upgrade_pinned:
        return current
    return latest_version(dep, org)


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
    pr_specs: PrSpecs | None = None,
    prerelease: bool = False,
) -> Plan:
    """Build the full, serialisable release plan for *chain*.

    Fails fast if any repo's live pyproject.toml disagrees with ``DEPS``.
    Otherwise emits one ``PackagePlan`` + step sequence per repo, in
    order; downstream repos pick sibling versions from what upstream
    repos ship in the same run.
    """
    live_deps = _verify_dep_graph(chain, cache_dir)

    packages: list[PackagePlan] = []
    all_steps: list[Step] = []
    released: ReleasedVersions = {}
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

        level = version_step if (i == 0 or uniform) else "patch"
        new_ver = bump_version(current, level) if action != Action.DEPS_ONLY else None

        repo_deps = live_deps[repo]
        sibling_deps: SiblingVersions = {}
        for dep in repo_deps:
            ver = _resolve_sibling_version(
                dep, repo_deps, released, planned_pins, repo_dir, org, upgrade_pinned
            )
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
        prerelease=prerelease,
    )


# ── Executor ──────────────────────────────────────────────────────────────


def _find_pr_url(package: str, plan: Plan) -> str:
    """URL of the PR the executor should act on for *package*.

    Prefers the URL captured by an earlier PR_CREATE step (authoritative);
    falls back to the PR number from a ``--from-prs`` spec.
    """
    for s in plan.steps:
        if s.package == package and s.kind == StepKind.PR_CREATE and s.result.get("pr_url"):
            return s.result["pr_url"]
    for pkg in plan.packages:
        if pkg.repo == package and pkg.pr_number:
            return str(pkg.pr_number)
    die(f"No PR URL found for {package}")


def _merge_sha_for(package: str, plan: Plan) -> str | None:
    """Commit SHA recorded by *package*'s PR_MERGE step, if it ran."""
    for s in plan.steps:
        if s.package == package and s.kind == StepKind.PR_MERGE:
            return s.result.get("merge_sha")
    return None


def _branch_matches_upstream(repo_dir: Path) -> bool:
    """Whether HEAD is already at upstream/master — no release payload to ship.

    Hit when the version bump + lockfile update already landed via an earlier
    feature PR: the release cut has nothing to commit, push, or PR — just tag.
    """
    head = sh("git", "rev-parse", "HEAD", cwd=repo_dir, capture=True).stdout.strip()
    tip = sh("git", "rev-parse", "upstream/master", cwd=repo_dir, capture=True).stdout.strip()
    return head == tip


def execute_step(step: Step, plan: Plan, ctx: Ctx):
    """Perform the side-effect prescribed by one plan step.

    All irreversible operations live in this dispatch — the planner
    decides, the executor acts.  Each case is idempotent where possible
    so a resumed plan doesn't re-push, re-merge, or re-tag.
    """
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
            # Idempotent: HEAD already carries this commit message (re-run of a
            # previously-committed step), or nothing is staged (a prior feature
            # PR already landed the version bump + lockfile on master, so the
            # release cut has nothing to commit — just tag and ship).
            head = sh("git", "log", "-1", "--format=%s", cwd=repo_dir, capture=True)
            if head.stdout.strip() == p["message"]:
                console.print("[dim]Already committed — skipping.[/]")
            elif (
                sh("git", "diff", "--cached", "--quiet", cwd=repo_dir, check=False).returncode == 0
            ):
                console.print("[dim]Nothing to commit — release payload already on master.[/]")
            else:
                sh("git", "commit", "-m", p["message"], cwd=repo_dir)

        case StepKind.GIT_PUSH:
            if _branch_matches_upstream(repo_dir):
                console.print("[dim]Branch is at upstream/master — nothing to push.[/]")
            else:
                sh("git", "push", "-u", "origin", p["branch"], "--force-with-lease", cwd=repo_dir)

        case StepKind.PR_CREATE:
            if _branch_matches_upstream(repo_dir):
                console.print("[dim]Branch is at upstream/master — no PR needed.[/]")
                return
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
                    "gh", "pr", "create", "--repo", gh_repo,
                    "--base", "master",
                    "--head", f"{plan.gh_fork}:{p['branch']}",
                    "--title", p["title"], "--body", p["body"],
                    "--label", AUTOMATED_RELEASE_LABEL,
                    capture=True,
                )  # fmt: skip
                step.result["pr_url"] = r.stdout.strip()
                console.print(f"PR created: {step.result['pr_url']}")

        case StepKind.PR_MERGE:
            if _branch_matches_upstream(repo_dir):
                # No PR was opened.  Pin ``merge_sha`` to the current
                # upstream/master commit so TAG doesn't re-resolve the
                # moving ref and accidentally tag someone else's push
                # that landed between this step and the tag step.  HEAD
                # equals upstream/master here by the condition above.
                step.result["merge_sha"] = sh(
                    "git", "rev-parse", "HEAD", cwd=repo_dir, capture=True
                ).stdout.strip()
                console.print(
                    f"[dim]No PR to merge — pinning tag to {step.result['merge_sha'][:12]}.[/]"
                )
                return
            pr_url = _find_pr_url(step.package, plan)
            # Idempotent: if already merged, just capture the SHA
            if pr_state(pr_url, gh_repo) == "MERGED":
                step.result["merge_sha"] = _gh_merge_commit(pr_url, gh_repo)
                console.print(f"[dim]Already merged ({step.result['merge_sha'][:12]})[/]")
            elif wait_for_checks(pr_url, gh_repo, ctx) == "merged":
                step.result["merge_sha"] = _gh_merge_commit(pr_url, gh_repo)
            else:
                step.result["merge_sha"] = squash_merge(pr_url, gh_repo)

        case StepKind.TAG:
            sh("git", "fetch", "upstream", cwd=repo_dir)
            target = _merge_sha_for(step.package, plan) or "upstream/master"
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
                cmd = [
                    "gh",
                    "release",
                    "create",
                    p["tag"],
                    "--repo",
                    gh_repo,
                    "--title",
                    p["title"],
                    "--generate-notes",
                ]
                if plan.prerelease:
                    cmd.append("--prerelease")
                sh(*cmd)

        case StepKind.WHEEL_POLL:
            wait_for_wheel(step.package, p["version"], plan.gh_org, ctx.wheel_timeout)


def simulate_step(step: Step, plan: Plan, ctx: Ctx):
    """Dry-run one step: verify preconditions, log the intent, no side effects."""
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
    """Snapshot the plan to disk so a crashed run can resume from where it failed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2))


class ExecMode(StrEnum):
    SIMULATE = "simulate"
    """Log intent + validate preconditions, no side effects."""
    EXECUTE = "execute"
    """Run every step from scratch."""
    RESUME = "resume"
    """Skip already-completed steps; run the rest (after a crash)."""


def execute_plan(plan: Plan, *, mode: ExecMode, ctx: Ctx) -> Plan:
    """Walk the plan step by step, persisting status between steps.

    On failure the step is marked ``failed``, the plan is saved, and
    the exception propagates — the operator fixes the root cause and
    re-runs ``execute`` on the same plan file to resume.
    """
    for step in plan.steps:
        if mode == ExecMode.RESUME and step.status == "completed":
            console.print(f"[dim]Skipping completed: {step.id}[/]")
            continue

        pkg_label = f"[bold cyan]{step.package}[/]"
        console.print(f"\n{pkg_label} {step.kind.value}")

        if mode == ExecMode.SIMULATE:
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


# ── Operator attention prompts ────────────────────────────────────────────
#
# Long stages (clones, CI waits) tempt the operator to wander; these
# helpers pull their attention back when input is actually needed.


def _alert_banner(text: str) -> None:
    """Pull the operator's eyes back to the terminal: bell + banner."""
    console.bell()
    console.print(f"\n[black on bright_yellow] {text} [/]")


def alert_confirm(prompt: str, **kwargs: Any) -> bool:
    """Ask a yes/no question loudly enough that a distracted operator notices."""
    _alert_banner("INPUT NEEDED")
    return click.confirm(prompt, **kwargs)


def alert_prompt(prompt: str, **kwargs: Any) -> Any:
    """Ask for free-form input loudly enough that a distracted operator notices."""
    _alert_banner("INPUT NEEDED")
    return click.prompt(prompt, **kwargs)


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


def _render_plan_preview(plan: Plan) -> None:
    """Print the plan as a table — the operator's last look before we commit."""
    kind_hint = "[yellow]prerelease[/]" if plan.prerelease else "[green]release[/]"
    console.print(f"\n[bold]Release plan ({kind_hint}):[/]\n")
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


_CLICK_CONTEXT = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=_CLICK_CONTEXT)
def cli():
    """Cascading release chain for the terok package family."""
    _check_gh_version()


@cli.command(context_settings=_CLICK_CONTEXT)
@click.argument("repos", nargs=-1)
@click.option("--version-step", default="patch", type=click.Choice(["major", "minor", "patch"]))
@click.option("--version-step-uniform", is_flag=True)
@click.option("-n", "--name", "release_name", default="", help="Release name suffix")
@click.option("-y", "--yes", is_flag=True, help="Auto-approve normal confirmations")
@click.option("-p", "--pretend", is_flag=True, help="Dry run")
@click.option("--skip-checks", is_flag=True)
@click.option("--check-timeout", default=DEFAULT_CHECK_TIMEOUT, type=int)
@click.option("--upgrade-pinned", is_flag=True)
@click.option("--from-prs", default=None, help="repo:PR pairs (e.g. sandbox:42,executor:55)")
@click.option("--open-top", is_flag=True, help="Top package: update deps only, no release")
@click.option(
    "--prerelease",
    is_flag=True,
    help="Publish as a GitHub prerelease (hidden from the repo's 'Latest' badge)",
)
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
    prerelease,
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
      quick sandbox..terok --prerelease
                                       Chain, all releases marked prerelease
    """
    org, fork, cd, ctx = _common_ctx(org, fork, cache_dir, pretend, yes, skip_checks, check_timeout)

    chain, stop_at, pr_specs = _resolve_chain(repos, from_prs, open_top=open_top)

    # Prompt for release name if not given
    if not release_name and not pretend:
        release_name = alert_prompt("Release name (empty for version-only)", default="")

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
        prerelease=prerelease,
    )

    _render_plan_preview(plan)

    if not pretend and not yes:
        alert_confirm("Proceed?", default=True, abort=True)

    # Save plan
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = slugify(release_name) or "release"
    plan_path = cd / "plans" / f"{ts}-{slug}.json"
    ctx.plan_path = plan_path
    save_plan(plan, plan_path)
    console.print(f"\nPlan saved: {plan_path}")

    # Execute
    mode = ExecMode.SIMULATE if pretend else ExecMode.EXECUTE
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


@cli.command("open", context_settings=_CLICK_CONTEXT)
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
        terok-release-chain.py open feat/comms clearance
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


@cli.command("plan", context_settings=_CLICK_CONTEXT)
@click.argument("repos", nargs=-1)
@click.option("-o", "--output", type=click.Path(), help="Output plan file")
@click.option("--version-step", default="patch", type=click.Choice(["major", "minor", "patch"]))
@click.option("--version-step-uniform", is_flag=True)
@click.option("-n", "--name", "release_name", default="")
@click.option("--upgrade-pinned", is_flag=True)
@click.option("--from-prs", default=None)
@click.option("--open-top", is_flag=True, help="Top package: update deps only, no release")
@click.option(
    "--prerelease",
    is_flag=True,
    help="Publish as a GitHub prerelease (hidden from the repo's 'Latest' badge)",
)
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
    prerelease,
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
        prerelease=prerelease,
    )

    out = Path(output) if output else cd / "plans" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    save_plan(plan, out)
    console.print(f"Plan written to {out}")


@cli.command(context_settings=_CLICK_CONTEXT)
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
    execute_plan(plan, mode=ExecMode.SIMULATE, ctx=ctx)
    console.print("\n[green]Simulation complete — no issues found.[/]")


@cli.command(context_settings=_CLICK_CONTEXT)
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("-y", "--yes", is_flag=True)
@click.option("--skip-checks", is_flag=True)
@click.option("--check-timeout", default=DEFAULT_CHECK_TIMEOUT, type=int)
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
    mode = ExecMode.RESUME if has_completed else ExecMode.EXECUTE
    if has_completed:
        console.print("[yellow]Resuming partially-executed plan...[/]")

    execute_plan(plan, mode=mode, ctx=ctx)
    console.print("\n[bold green]All releases complete![/]")


if __name__ == "__main__":
    cli()
