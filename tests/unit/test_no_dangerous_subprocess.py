# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""AST-based static analysis: ban subprocess calls co-located with workspace-dangerous.

Scans all ``.py`` files under ``src/terok/`` and flags any function that both:

1. Calls a ``subprocess`` function (``run``, ``check_output``, ``Popen``,
   ``call``, ``check_call``)
2. References the ``"workspace-dangerous"`` string literal **or** the
   ``WORKSPACE_DANGEROUS_DIRNAME`` constant name

There should be **zero** co-occurrences — all legitimate git operations
targeting agent workspaces now go through ``podman exec`` in
``container_exec.py``, never through host-side subprocess calls.
"""

import ast
from pathlib import Path

_SUBPROCESS_FUNCS = frozenset({"run", "check_output", "Popen", "call", "check_call"})
_DANGEROUS_NAMES = frozenset({"WORKSPACE_DANGEROUS_DIRNAME"})
_DANGEROUS_STRINGS = frozenset({"workspace-dangerous"})

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "terok"


def _has_subprocess_call(node: ast.AST) -> bool:
    """Return True if *node* contains a call to a subprocess function."""
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        # subprocess.run(...)
        if isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_FUNCS:
            if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                return True
    return False


def _has_dangerous_ref(node: ast.AST) -> bool:
    """Return True if *node* references the dangerous workspace sentinel."""
    for child in ast.walk(node):
        # String literal "workspace-dangerous"
        if isinstance(child, ast.Constant) and child.value in _DANGEROUS_STRINGS:
            return True
        # Name reference WORKSPACE_DANGEROUS_DIRNAME
        if isinstance(child, ast.Name) and child.id in _DANGEROUS_NAMES:
            return True
    return False


def test_no_subprocess_in_dangerous_workspace() -> None:
    """No function may combine subprocess calls with workspace-dangerous refs."""
    violations: list[str] = []

    for py_file in sorted(_SRC_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue

        rel = py_file.relative_to(_SRC_ROOT.parent.parent)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _has_subprocess_call(node) and _has_dangerous_ref(node):
                violations.append(
                    f"{rel}:{node.lineno} — {node.name}() mixes subprocess "
                    f"calls with workspace-dangerous references"
                )

    assert not violations, (
        "Host-side subprocess calls must NEVER target workspace-dangerous directories.\n"
        "Use container_exec.container_git_diff() instead.\n\n" + "\n".join(violations)
    )
