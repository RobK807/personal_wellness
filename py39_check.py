"""Check the Flask front-end would run on Python 3.9.

    python py39_check.py

The NAS runs Python 3.9.22 but development happens on a much newer interpreter,
so syntax and runtime constructs that 3.9 rejects sail through here and only
fail on the NAS - where the symptom is a server that reports "Started" and then
silently dies. This catches them locally instead.

What it looks for:

  * PEP 604 unions (`A | B`) evaluated at runtime. Annotations are fine, because
    every module uses `from __future__ import annotations`, which defers them.
    A plain assignment like `Params = Sequence[Any] | dict[str, Any]` is not an
    annotation and blows up on import under 3.9.
  * `match` statements (3.10) and `except*` (3.11).

Only the modules the NAS actually loads are checked; the Streamlit front-end is
free to use anything, since it needs 3.10+ regardless.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# What the NAS imports: the Flask front-end plus the shared core.
NAS_PATHS = ["config.py", "serve.py", "core", "web"]

# Not checked - these require 3.10+ anyway.
STREAMLIT_ONLY = {"app.py", "views"}


def annotation_nodes(tree: ast.AST) -> set:
    """Every AST node id that sits inside an annotation.

    Annotations are stringified by PEP 563, so a `|` inside one never runs.
    """
    inside: set = set()

    def mark(node) -> None:
        if node is None:
            return
        for child in ast.walk(node):
            inside.add(id(child))

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            mark(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mark(node.returns)
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                        args.vararg, args.kwarg):
                if arg is not None:
                    mark(arg.annotation)
    return inside


def _label(path: Path) -> str:
    """Path relative to the project when possible, absolute otherwise."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def check_file(path: Path) -> list:
    name = _label(path)
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{name}:{exc.lineno}: syntax error: {exc.msg}"]

    # Ask the parser itself to judge the file as 3.9 would. It catches things
    # the walk below never could - most usefully the PEP 701 f-strings that
    # 3.12 allows and 3.9 rejects, which is easy to write by accident when the
    # SQL in core/ is built out of nested f-strings.
    try:
        ast.parse(source, feature_version=(3, 9))
    except SyntaxError as exc:
        return [f"{name}:{exc.lineno}: not valid Python 3.9 syntax: {exc.msg}"]

    problems: list = []
    deferred = annotation_nodes(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            if id(node) in deferred:
                continue
            # Only flag unions of type-ish things; `a | b` on ints is fine.
            if _looks_like_types(node):
                problems.append(
                    f"{name}:{node.lineno}: "
                    f"PEP 604 union evaluated at runtime - use typing.Union"
                )
        elif node.__class__.__name__ == "Match":
            problems.append(f"{name}:{node.lineno}: match statement needs 3.10")
        elif node.__class__.__name__ == "TryStar":
            problems.append(f"{name}:{node.lineno}: except* needs 3.11")
    return problems


def _looks_like_types(node: ast.BinOp) -> bool:
    """True if either side looks like a type rather than a value."""
    for side in (node.left, node.right):
        if isinstance(side, ast.Subscript):
            return True
        if isinstance(side, ast.Constant) and side.value is None:
            return True
        if isinstance(side, ast.Name) and side.id and side.id[0].isupper():
            return True
        if isinstance(side, ast.Attribute) and side.attr and side.attr[0].isupper():
            return True
    return False


def iter_files():
    for entry in NAS_PATHS:
        target = ROOT / entry
        if target.is_file():
            yield target
        elif target.is_dir():
            for path in sorted(target.rglob("*.py")):
                if "__pycache__" not in path.parts:
                    yield path


def main() -> int:
    print(f"Checking the Python 3.9 path (skipping {', '.join(sorted(STREAMLIT_ONLY))})\n")
    problems: list = []
    checked = 0
    for path in iter_files():
        checked += 1
        found = check_file(path)
        problems += found
        print(f"  [{'FAIL' if found else 'ok '}] {_label(path)}")

    print(f"\n{checked} files checked")
    if problems:
        print(f"\n{len(problems)} problem(s) that would break on Python 3.9:")
        for line in problems:
            print(f"  {line}")
        return 1
    print("Nothing here needs Python 3.10.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
