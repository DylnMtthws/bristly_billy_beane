"""AST import walker for the package-boundary tests.

The repo already had one of these inline in ``test_cedh_lab.py``
(``_imported_modules``). Its core choice is right and is kept: walk the **whole**
AST rather than ``tree.body``, because a lazy import inside a function is still
an import and this repo uses them deliberately (``cedh/factory.py`` imports the
Postgres adapters inside ``build_card_repository`` so the default test suite
never needs psycopg).

Four holes in that version are closed here, each of which lets a real violation
through:

1. **Alias expansion.** ``from sabermetrics import pipeline`` records only
   ``sabermetrics``. The banned name is the *attribute*, so the aliases have to
   be joined onto the module and offered as candidates.
2. **Relative imports.** ``from ..analytics import cvar`` has ``node.module ==
   'analytics'`` and a non-zero ``node.level``; unresolved, it matches nothing.
3. **Dynamic imports.** ``importlib.import_module("sabermetrics.reasoning")``
   is a call, not an ``Import`` node, and is invisible to a walker that only
   looks at import statements.
4. **Segment boundaries.** ``startswith("sabermetrics.pipeline")`` also matches
   a future ``sabermetrics.pipelines_new``, which would be a false positive that
   erodes trust in the gate.

A boundary test that can be walked around is worse than none: it reads as a
guarantee while providing none.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

#: Every top-level stdlib module name for this interpreter. ``__future__`` is
#: included, so ``from __future__ import annotations`` needs no special case.
STDLIB: frozenset[str] = frozenset(sys.stdlib_module_names)


def _package_parts(path: Path) -> list[str]:
    return list(path.relative_to(SRC).parts[:-1])


def import_statements(path: Path) -> list[tuple[int, tuple[str, ...]]]:
    """Every import in a file as ``(lineno, candidate absolute dotted names)``.

    Args:
        path: A ``.py`` file under ``src/``.

    Returns:
        One entry per import statement. Each carries every dotted name that
        statement could plausibly mean, because ``from X import y`` might be
        importing the submodule ``X.y`` or the attribute ``y`` of ``X`` and the
        AST alone cannot tell which.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg = _package_parts(path)
    out: list[tuple[int, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, (alias.name,)))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # hole 2
                base = pkg[: len(pkg) - (node.level - 1)]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            if not prefix:
                continue
            candidates = [prefix]  # hole 1
            candidates += [f"{prefix}.{a.name}" for a in node.names if a.name != "*"]
            out.append((node.lineno, tuple(candidates)))
        elif isinstance(node, ast.Call):  # hole 3
            fn = node.func
            dynamic = (
                isinstance(fn, ast.Attribute) and fn.attr == "import_module"
            ) or (isinstance(fn, ast.Name) and fn.id == "__import__")
            if dynamic and node.args and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    out.append((node.lineno, (node.args[0].value,)))
    return out


def covers(name: str, target: str) -> bool:
    """Whether ``name`` is ``target`` or a module inside it. Hole 4."""
    return name == target or name.startswith(target + ".")


def violations(
    package: str,
    *,
    banned: tuple[str, ...] = (),
    allowed: tuple[str, ...] | None = None,
    exempt: tuple[str, ...] = (),
) -> list[str]:
    """Import-rule violations in ``src/sabermetrics/<package>``.

    Evaluated per **statement**, not per candidate name. For a denylist a
    statement fails if *any* candidate is banned. For an allowlist it fails only
    if *no* candidate is allowed — otherwise ``from sabermetrics import
    mechanics`` would fail on its bare ``sabermetrics`` candidate, which is the
    same statement written a different way.

    Args:
        package: Package name under ``src/sabermetrics/``.
        banned: Dotted prefixes that may not be imported.
        allowed: If given, the only dotted prefixes that may be imported.
            Stdlib names are always allowed and are not checked against it.
        exempt: Dotted prefixes that override ``banned`` and satisfy ``allowed``.
            Its only use is letting a package import **itself**: a rule phrased
            as "may not import ``sabermetrics``" is aimed at siblings, and
            without this a package could never be more than one module. Matching
            is segment-bounded like everything else here, so exempting
            ``sabermetrics.mechanics`` does not exempt a future
            ``sabermetrics.mechanics_v2``.

    Returns:
        Human-readable ``path:lineno imports X`` strings, empty when clean.
    """
    root = SRC / "sabermetrics" / package
    assert root.is_dir(), f"{package} package is missing"
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for lineno, candidates in import_statements(path):
            where = f"{path.relative_to(SRC)}:{lineno}"
            spared = {c for c in candidates if any(covers(c, e) for e in exempt)}
            hit = next(
                (
                    c
                    for c in candidates
                    if c not in spared
                    for b in banned
                    if covers(c, b)
                ),
                None,
            )
            if hit:
                out.append(f"{where} imports {hit}")
                continue
            if allowed is None:
                continue
            if candidates[0].split(".")[0] in STDLIB:
                continue
            if spared:
                continue
            if not any(covers(c, a) for c in candidates for a in allowed):
                out.append(f"{where} imports {candidates[0]}")
    return out
