#!/usr/bin/env python3
"""Dependency-graph guard -- enforces ARCHITECTURE.md section 3 / section 9 in CI.

ARCHITECTURE.md section 10 specifies these as `grep` one-liners. Grep can't tell a
runtime import from a `if TYPE_CHECKING:` one, but **invariant 3 explicitly
allows** the latter ("persona has zero *runtime* imports from silicon_brain;
TYPE_CHECKING-guarded imports for type hints are allowed"). So this walks the
AST instead and only reports imports that actually execute at runtime.

Checks (each must report zero violations):
  1. infra/         imports nothing from silicon_brain, persona, services, app
  2. silicon_brain/ imports nothing from persona, services, app
  3. persona/       has no *runtime* imports from silicon_brain
  4. persona/<A>/   does not import persona/<B>/ internals

Usage:
    python scripts/check_arch.py            # report + exit 1 on violation
    python scripts/check_arch.py --list     # list every rule, then check
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Violation:
    path: Path
    lineno: int
    statement: str
    rule: str

    def render(self) -> str:
        rel = self.path.relative_to(REPO_ROOT).as_posix()
        return f"  {rel}:{self.lineno}: {self.statement}\n      -> {self.rule}"


def _iter_py(pkg: str):
    root = REPO_ROOT / pkg
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.py")):
        # Skip vendored / build noise if any ever lands inside a package.
        if any(part in {"__pycache__", ".venv", "node_modules"} for part in path.parts):
            continue
        yield path


def _runtime_imports(tree: ast.AST) -> list[tuple[int, str, str]]:
    """Yield (lineno, module_root, source_fragment) for runtime-only imports.

    Imports nested inside `if TYPE_CHECKING:` are skipped, per invariant 3.
    Imports inside a function body still count -- they execute when called.
    """
    type_checking_nodes: set[int] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_tc = (
            (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
            or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
        )
        if is_tc:
            for child in ast.walk(node):
                type_checking_nodes.add(id(child))

    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if id(node) in type_checking_nodes:
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                found.append((node.lineno, root, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            # Relative imports (level > 0) stay inside the package -- not cross-layer.
            if node.level and not node.module:
                continue
            if node.level:
                continue
            module = node.module or ""
            root = module.split(".")[0]
            names = ", ".join(a.name for a in node.names)
            found.append((node.lineno, root, f"from {module} import {names}"))
    return found


def check_package(pkg: str, forbidden: set[str], rule: str) -> list[Violation]:
    out: list[Violation] = []
    for path in _iter_py(pkg):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # a broken file is its own CI failure elsewhere
            print(f"  ! could not parse {path}: {exc}", file=sys.stderr)
            continue
        for lineno, root, frag in _runtime_imports(tree):
            if root in forbidden:
                out.append(Violation(path, lineno, frag, rule))
    return out


def check_cross_persona() -> list[Violation]:
    """Invariant 4 -- persona/<A> must not import persona/<B> internals."""
    persona_root = REPO_ROOT / "persona"
    if not persona_root.is_dir():
        return []
    personas = {p.name for p in persona_root.iterdir() if p.is_dir() and not p.name.startswith("_")}

    out: list[Violation] = []
    for name in sorted(personas):
        siblings = personas - {name}
        for path in _iter_py(f"persona/{name}"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for lineno, _root, frag in _runtime_imports(tree):
                # Look for `persona.<sibling>` anywhere in the statement.
                for sib in siblings:
                    if f"persona.{sib}" in frag:
                        out.append(
                            Violation(
                                path,
                                lineno,
                                frag,
                                f"invariant 4: persona/{name} imports persona/{sib} "
                                f"internals; cross-persona calls go through tools/HTTP",
                            )
                        )
                        break
    return out


RULES = [
    (
        "infra",
        {"silicon_brain", "persona", "services", "app"},
        "invariant 1: infra/ is the leaf -- it imports nothing from layers above it",
    ),
    (
        "silicon_brain",
        {"persona", "services", "app"},
        "invariant 2: silicon_brain/ depends only on infra",
    ),
    (
        "persona",
        {"silicon_brain"},
        "invariant 3: persona/ has no runtime silicon_brain imports -- "
        "use SiliconBrainClient over HTTP (TYPE_CHECKING hints are allowed)",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the rules being enforced")
    args = parser.parse_args()

    if args.list:
        print("Enforcing ARCHITECTURE.md dependency invariants:")
        for pkg, forbidden, rule in RULES:
            print(f"  - {pkg}/ must-not-import {sorted(forbidden)}  -- {rule}")
        print("  - persona/<A> must-not-import persona/<B> internals -- invariant 4")
        print()

    violations: list[Violation] = []
    for pkg, forbidden, rule in RULES:
        violations.extend(check_package(pkg, forbidden, rule))
    violations.extend(check_cross_persona())

    if violations:
        print(f"FAIL: dependency-graph violations ({len(violations)}):\n", file=sys.stderr)
        for v in violations:
            print(v.render(), file=sys.stderr)
        print(
            "\nSee ARCHITECTURE.md section 3 (dependency graph) and section 9 (invariants).",
            file=sys.stderr,
        )
        return 1

    print("OK: dependency graph clean -- ARCHITECTURE.md section 9 invariants 1-4 hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
