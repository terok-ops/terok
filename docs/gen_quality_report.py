# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Generate a code quality report page for MkDocs.

This script runs during ``mkdocs build`` via the mkdocs-gen-files plugin.
It executes complexipy, vulture, tach, and docstr-coverage, then assembles
the results into a single Markdown page with a Mermaid dependency diagram.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import mkdocs_gen_files

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "terok"
COMPLEXITY_THRESHOLD = 15
_VENV_BIN = Path(sys.executable).parent

# Depth at which to aggregate modules in the dependency diagram.
# 3 → terok.lib.containers, terok.lib.core, etc.
_GRAPH_DEPTH = 3


def _run(
    *cmd: str, cwd: Path = ROOT, timeout_seconds: float = 120.0
) -> subprocess.CompletedProcess[str]:
    """Run a command and return the result (never raises on failure)."""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="timed out")


def _load_tach_toml() -> tuple[str, object, list[dict]] | None:
    """Load and parse tach.toml, returning (raw_text, parsed_data, modules) or None."""
    tach_path = ROOT / "tach.toml"
    if not tach_path.is_file():
        return None

    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return None

    raw = tach_path.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(raw)
    except Exception:
        return None

    modules = data.get("modules", [])
    return (raw, data, modules)


def _section_complexity() -> str:
    """Generate cognitive complexity section from complexipy."""
    # Run complexipy to populate the cache (use CLI entry point, not -m).
    # Note: --quiet is omitted because it causes a spurious non-zero exit code
    # in complexipy >=5.x.  capture_output=True suppresses stdout anyway.
    run_result = _run(str(_VENV_BIN / "complexipy"), str(SRC), "--ignore-complexity")
    if run_result.returncode != 0:
        output = (run_result.stdout + run_result.stderr).strip()
        return f"!!! warning\n    complexipy failed; skipping complexity report.\n\n```\n{output}\n```\n"

    # Find the cache file
    cache_dir = ROOT / ".complexipy_cache"
    cache_files = sorted(cache_dir.glob("*.json")) if cache_dir.is_dir() else []
    if not cache_files:
        return "!!! warning\n    complexipy cache not found — skipping complexity report.\n"

    latest_cache = max(cache_files, key=lambda p: p.stat().st_mtime)
    try:
        data = json.loads(latest_cache.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "!!! warning\n    complexipy cache is invalid JSON — skipping complexity report.\n"
    raw_functions = data.get("functions", [])
    functions: list[dict[str, object]] = []
    for item in raw_functions:
        if not isinstance(item, dict):
            continue
        complexity = item.get("complexity")
        if not isinstance(complexity, (int, float)):
            continue
        functions.append(
            {
                "complexity": complexity,
                "function_name": str(item.get("function_name", "<unknown>")),
                "path": str(item.get("path", "<unknown>")),
            }
        )
    if not functions:
        return "No functions found.\n"

    # Sort by complexity descending
    functions.sort(key=lambda f: f["complexity"], reverse=True)

    # Summary stats
    total = len(functions)
    scores = [int(f["complexity"]) for f in functions]
    over_threshold = [f for f in functions if f["complexity"] > COMPLEXITY_THRESHOLD]
    max_c = functions[0]["complexity"] if functions else 0
    avg_c = sum(scores) / total if total else 0
    sorted_scores = sorted(scores)
    if total == 0:
        median_c = 0
    elif total % 2 == 1:
        median_c = sorted_scores[total // 2]
    else:
        median_c = (sorted_scores[total // 2 - 1] + sorted_scores[total // 2]) / 2
    pct_within = (total - len(over_threshold)) / total * 100 if total else 0

    lines = [
        f"- **Functions analyzed:** {total}\n",
        f"- **Median complexity:** {median_c} · **Average:** {avg_c:.1f} · **Max:** {max_c}\n",
        f"- **Within threshold ({COMPLEXITY_THRESHOLD}):** {pct_within:.0f}%"
        f" ({total - len(over_threshold)}/{total})\n",
        "\n",
    ]

    # Histogram
    buckets = [(0, 5), (6, 10), (11, 15), (16, 20), (21, 25), (26, 30), (31, 50), (51, 999)]
    bar_max = 30  # max bar width in characters
    bucket_counts = []
    for lo, hi in buckets:
        count = sum(1 for s in scores if lo <= s <= hi)
        bucket_counts.append((lo, hi, count))

    peak = max(c for _, _, c in bucket_counts) if bucket_counts else 1

    lines.append("```\n")
    for lo, hi, count in bucket_counts:
        if count == 0 and lo > max(scores):
            continue
        label = f"{lo:>3d}–{hi:>3d}" if hi < 999 else f"{lo:>3d}+   "
        bar_len = round(count / peak * bar_max) if peak else 0
        bar = "█" * bar_len
        pct = count / total * 100 if total else 0
        marker = " ◄ threshold" if lo <= COMPLEXITY_THRESHOLD <= hi else ""
        lines.append(f"  {label} │ {bar:<{bar_max}} {count:>3d} ({pct:4.1f}%){marker}\n")
    lines.append("```\n\n")

    if over_threshold:
        lines.append(f"**{len(over_threshold)} functions exceeding threshold:**\n\n")
        lines.append("| Complexity | Function | File |\n")
        lines.append("|---:|---|---|\n")
        for f in over_threshold:
            lines.append(f"| {f['complexity']} | `{f['function_name']}` | `{f['path']}` |\n")
    else:
        lines.append(
            f"All functions are within the cognitive complexity threshold of"
            f" {COMPLEXITY_THRESHOLD}.\n"
        )

    return "".join(lines)


def _section_dead_code() -> str:
    """Generate dead code section from vulture."""
    result = _run(
        sys.executable,
        "-m",
        "vulture",
        str(SRC),
        str(ROOT / "vulture_whitelist.py"),
        "--min-confidence",
        "80",
    )
    output = (result.stdout + result.stderr).strip()
    if not output:
        return "No dead code found at 80% confidence threshold.\n"

    def _md_cell(value: str) -> str:
        return value.replace("|", r"\|").replace("\n", " ")

    lines = ["| Confidence | Location | Issue |\n", "|---:|---|---|\n"]
    for line in output.splitlines():
        # Format: path:line: message (NN% confidence)
        if "% confidence)" in line:
            parts = line.rsplit("(", 1)
            location_msg = parts[0].strip()
            confidence = parts[1].rstrip(")").strip()
            # Split location:line: message
            loc_parts = location_msg.split(": ", 1)
            location = loc_parts[0] if loc_parts else location_msg
            message = loc_parts[1] if len(loc_parts) > 1 else ""
            lines.append(
                f"| {_md_cell(confidence)} | `{_md_cell(location)}` | {_md_cell(message)} |\n"
            )
        else:
            lines.append(f"| — | — | {_md_cell(line)} |\n")
    return "".join(lines)


def _coarsen_module(name: str, depth: int = _GRAPH_DEPTH) -> str:
    """Truncate a dotted module path to *depth* segments."""
    parts = name.split(".")
    return ".".join(parts[:depth])


def _coarsen_graph(mermaid_lines: list[str]) -> list[str]:
    """Aggregate fine-grained mermaid edges into a coarser high-level graph.

    Edges between sub-modules of the same group are dropped.  Duplicate
    coarsened edges are collapsed and annotated with a count.
    """
    edge_re = re.compile(r"^\s*(.+?)\s*-->\s*(.+?)\s*$")
    edge_counts: dict[tuple[str, str], int] = defaultdict(int)
    nodes: set[str] = set()

    for line in mermaid_lines:
        m = edge_re.match(line)
        if not m:
            continue
        src = _coarsen_module(m.group(1).strip())
        dst = _coarsen_module(m.group(2).strip())
        nodes.add(src)
        nodes.add(dst)
        if src != dst:
            edge_counts[(src, dst)] += 1

    # Build the coarsened graph (top-down)
    out = ["graph TD"]
    for (src, dst), count in sorted(edge_counts.items()):
        label = f"|{count}|" if count > 1 else ""
        # Use short aliases to keep the diagram compact
        out.append(f"    {src} -->{label} {dst}")
    # Emit isolated nodes (no outgoing edges)
    connected = {n for pair in edge_counts for n in pair}
    for node in sorted(nodes - connected):
        out.append(f"    {node}")
    return out


def _section_layer_overview() -> str:
    """Generate a high-level layer dependency graph from tach.toml."""
    loaded = _load_tach_toml()
    if not loaded:
        return ""
    _raw, _data, modules = loaded
    if not modules:
        return ""

    layer_of: dict[str, str] = {}
    layer_modules: dict[str, list[str]] = defaultdict(list)
    for m in modules:
        path = m.get("path", "")
        layer = m.get("layer", "?")
        layer_of[path] = layer
        short = path.removeprefix("terok.").removeprefix("lib.")
        layer_modules[layer].append(short)

    # Collect inter-layer dependency edges
    layer_edges: dict[tuple[str, str], int] = defaultdict(int)
    for m in modules:
        src_layer = m.get("layer", "?")
        for dep_path in m.get("depends_on", []):
            dst_layer = layer_of.get(dep_path, "?")
            if src_layer != dst_layer:
                layer_edges[(src_layer, dst_layer)] += 1

    if not layer_edges and len(layer_modules) < 2:
        return ""

    lines = ["```mermaid\ngraph LR\n"]
    for layer in sorted(layer_modules):
        count = len(layer_modules[layer])
        lines.append(f'    {layer}["{layer} ({count} modules)"]\n')
    for (src, dst), count in sorted(layer_edges.items()):
        label = f"|{count} deps|" if count > 1 else ""
        lines.append(f"    {src} -->{label} {dst}\n")
    lines.append("```\n")
    return "".join(lines)


def _section_dependency_diagram() -> str:
    """Generate module dependency diagram from tach."""
    result = _run(sys.executable, "-m", "tach", "show", "--mermaid", "-o", "-")
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip() or "no output"
        return (
            f"!!! warning\n    tach show failed (exit {result.returncode}).\n\n```\n{output}\n```\n"
        )
    output = result.stdout.strip()
    if not output:
        return "!!! warning\n    tach show --mermaid produced no output.\n"

    # Extract just the mermaid edges (skip the NOTE lines and the "graph" header)
    edge_lines = []
    in_graph = False
    for line in output.splitlines():
        if line.startswith("graph "):
            in_graph = True
            continue
        if in_graph:
            edge_lines.append(line)

    if not edge_lines:
        return "!!! warning\n    Could not parse mermaid output from tach.\n"

    coarsened = _coarsen_graph(edge_lines)
    return "```mermaid\n" + "\n".join(coarsened) + "\n```\n"


def _section_dependency_report() -> str:
    """Generate a module dependency summary from tach.toml.

    Parses the ``[[modules]]`` entries and builds a collapsible table showing
    each module's layer, dependency count, and description (from the comment above).
    """
    loaded = _load_tach_toml()
    if not loaded:
        return "!!! warning\n    `tach.toml` not found or unparseable — skipping module summary.\n"
    raw, _data, modules = loaded
    if not modules:
        return "No modules defined in `tach.toml`.\n"

    # Extract comments above each [[modules]] block as descriptions.
    descriptions: list[str] = []
    raw_lines = raw.splitlines()
    for i, line in enumerate(raw_lines):
        if line.strip() == "[[modules]]":
            desc = (
                raw_lines[i - 1].lstrip("# ").strip()
                if i > 0 and raw_lines[i - 1].startswith("#")
                else ""
            )
            descriptions.append(desc)

    n_layers = len({m.get("layer", "?") for m in modules})
    lines = [
        f'??? info "{len(modules)} modules across {n_layers} layers (click to expand)"\n\n',
        "    | Module | Layer | Deps | Description |\n",
        "    |---|---|---:|---|\n",
    ]
    for idx, mod in enumerate(modules):
        path = mod.get("path", "?")
        layer = mod.get("layer", "?")
        deps = len(mod.get("depends_on", []))
        desc = descriptions[idx] if idx < len(descriptions) else ""
        lines.append(f"    | `{path}` | {layer} | {deps} | {desc} |\n")
    lines.append("\n")

    return "".join(lines)


def _section_boundary_check() -> str:
    """Run tach check and report results."""
    loaded = _load_tach_toml()
    mod_count = 0
    dep_count = 0
    if loaded:
        _raw, _data, modules = loaded
        mod_count = len(modules)
        dep_count = sum(len(m.get("depends_on", [])) for m in modules)

    result = _run(sys.executable, "-m", "tach", "check")
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        stats = f"{mod_count} modules, {dep_count} dependency edges — " if mod_count else ""
        return f"{stats}all boundaries validated.\n"
    return f"```\n{output}\n```\n"


def _nbsp_num(n: int) -> str:
    """Format an integer with non-breaking spaces as thousand separators."""
    s = f"{n:,}"
    return s.replace(",", "\u00a0")


_EMPTY_TOTALS: dict[str, int] = {"lines": 0, "code": 0, "comment": 0, "blank": 0, "files": 0}


def _scc_totals(path: Path) -> dict[str, int]:
    """Run scc on *path* and return aggregated totals across all languages."""
    result = _run("scc", "--format", "json", "--no-cocomo", str(path))
    if result.returncode != 0 or not result.stdout.strip():
        return dict(_EMPTY_TOTALS)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return dict(_EMPTY_TOTALS)
    totals = dict(_EMPTY_TOTALS)
    for lang in data:
        if lang.get("Name", "") in ("Total", "SUM"):
            continue
        totals["lines"] += lang.get("Lines", 0)
        totals["code"] += lang.get("Code", 0)
        totals["comment"] += lang.get("Comment", 0)
        totals["blank"] += lang.get("Blank", 0)
        totals["files"] += lang.get("Count", 0)
    return totals


def _walk_subdirs(base: Path, lines: list[str], prefix: str = "") -> None:
    """Recursively collect LoC table rows for each subdirectory under *base*."""
    n = _nbsp_num
    subdirs = sorted(p for p in base.iterdir() if p.is_dir() and p.name != "__pycache__")
    for subdir in subdirs:
        t = _scc_totals(subdir)
        if t["code"] == 0 and t["lines"] == 0:
            continue
        label = f"{prefix}{subdir.name}/"
        lines.append(
            f"| `{label}` | {t['files']} | {n(t['code'])} | {n(t['comment'])} | {n(t['blank'])} |\n"
        )
        _walk_subdirs(subdir, lines, label)


def _section_loc() -> str:
    """Generate lines-of-code statistics using scc."""
    import shutil

    if not shutil.which("scc"):
        return "!!! warning\n    `scc` not found — skipping LoC report. Install from https://github.com/boyter/scc\n"

    n = _nbsp_num

    src_totals = _scc_totals(SRC)
    tests_totals = _scc_totals(ROOT / "tests")

    comment_ratio = (
        f"{src_totals['comment'] / src_totals['code'] * 100:.0f}%" if src_totals["code"] else "—"
    )
    test_ratio = f"{tests_totals['code'] / src_totals['code']:.1%}" if src_totals["code"] else "—"

    lines = [
        "| | Files | Code | Comment | Blank | Total |\n",
        "|---|---:|---:|---:|---:|---:|\n",
        f"| Source (`src/terok/`) | {src_totals['files']} | {n(src_totals['code'])} | {n(src_totals['comment'])} | {n(src_totals['blank'])} | {n(src_totals['lines'])} |\n",
        f"| Tests (`tests/`) | {tests_totals['files']} | {n(tests_totals['code'])} | {n(tests_totals['comment'])} | {n(tests_totals['blank'])} | {n(tests_totals['lines'])} |\n",
        f"| **Combined** | **{src_totals['files'] + tests_totals['files']}** | **{n(src_totals['code'] + tests_totals['code'])}** | **{n(src_totals['comment'] + tests_totals['comment'])}** | **{n(src_totals['blank'] + tests_totals['blank'])}** | **{n(src_totals['lines'] + tests_totals['lines'])}** |\n",
        "\n",
        f"- **Comment/code ratio:** {comment_ratio}\n",
        f"- **Test/source ratio:** {test_ratio}\n",
        "\n",
    ]

    # Detailed per-module breakdown (collapsible via pymdownx.details)
    detail_lines: list[str] = []
    _walk_subdirs(SRC, detail_lines)

    lines.append('??? info "Source by module (click to expand)"\n\n')
    lines.append("    | Module | Files | Code | Comment | Blank |\n")
    lines.append("    |---|---:|---:|---:|---:|\n")
    for dl in detail_lines:
        lines.append(f"    {dl}")
    lines.append("\n")

    return "".join(lines)


def _section_docstring_coverage() -> str:
    """Generate docstring coverage section."""
    result = _run(
        str(_VENV_BIN / "docstr-coverage"),
        str(SRC),
        "--fail-under=0",
    )
    output = (result.stdout + result.stderr).strip()
    # Extract the summary lines
    summary_lines = []
    for line in output.splitlines():
        if any(kw in line for kw in ("Needed:", "Total coverage:", "Grade:")):
            summary_lines.append(f"- {line.strip()}\n")
    if not summary_lines:
        return f"```\n{output}\n```\n"
    return "".join(summary_lines)


def generate_report() -> str:
    """Assemble the full quality report."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    sections = [
        "# Code Quality Report\n\n",
        f"*Generated: {now}*\n\n",
        "---\n\n",
        "## Lines of Code\n\n",
        _section_loc(),
        "\n",
        "## Architecture\n\n",
        "### Layer Overview\n\n",
        _section_layer_overview(),
        "\n",
        "### Module Dependency Graph\n\n",
        _section_dependency_diagram(),
        "\n",
        "### Module Boundaries\n\n",
        _section_boundary_check(),
        "\n",
        "### Module Summary\n\n",
        _section_dependency_report(),
        "\n",
        "## Cognitive Complexity\n\n",
        f"Threshold: **{COMPLEXITY_THRESHOLD}** (functions above this are listed below)\n\n",
        _section_complexity(),
        "\n",
        "## Dead Code Analysis\n\n",
        _section_dead_code(),
        "\n",
        "## Docstring Coverage\n\n",
        _section_docstring_coverage(),
        "\n---\n\n",
        "*Generated by scc, complexipy, vulture, tach, and docstr-coverage.*\n",
    ]

    return "".join(sections)


# --- mkdocs-gen-files entry point ---
report = generate_report()
with mkdocs_gen_files.open("quality-report.md", "w") as f:
    f.write(report)
