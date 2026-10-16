"""
Dependency Migration Assistant — Codebase Scanner

Reads a Python project and lists every place it uses a library, so each place
can later be looked up in that library's changelog.

How it decides something is a pandas usage:
  - it finds the file's imports of the library (aliases included), e.g.
    `import pandas as pd`, `from pandas import read_csv as rc`
  - calls and references reached through those names are "module" or "imported_name" usages
  - a variable counts as holding a pandas object when it was assigned from a pandas
    call or from a method/attribute/item of another pandas object, was annotated with a
    pandas type, or was bound by `with <pandas call> as name`; a method call on it is a
    "tracked_variable" usage
  - a method call on anything else is kept only if its name is in `known_names`
    (names the library documents), marked "unknown"; the rest are counted in the log

Which library to look for comes from the settings file ("import_names"), so nothing
library-specific is hardcoded here.
"""

import ast
import logging
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SKIP_DIRS = {"venv", ".venv", "env", ".git", "__pycache__", "site-packages", "node_modules", ".tox", ".mypy_cache"}

# Attributes and methods whose result is NOT a pandas object (a dict, list, array, number...).
NON_PANDAS_RESULT = {
    "to_dict", "to_list", "tolist", "to_numpy", "to_json", "to_csv", "to_string", "to_html",
    "to_latex", "to_markdown", "to_pickle", "to_parquet", "to_excel", "item",
    "values", "shape", "size", "ndim", "empty",
}


def path_inside_library(module_name: str, import_names: list) -> str | None:
    """'pandas' -> '', 'pandas.api.types' -> 'api.types', 'numpy' -> None (not this library)."""
    for name in import_names:
        if module_name == name:
            return ""
        if module_name.startswith(name + "."):
            return module_name[len(name) + 1:]
    return None


def collect_bindings(tree, import_names: list) -> dict:
    """name -> (path inside the library, 'module' | 'imported_name'), for every import of the library."""
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                inside = path_inside_library(alias.name, import_names)
                if inside is None:
                    continue
                if alias.asname:
                    bindings[alias.asname] = (inside, "module")
                else:
                    bindings[alias.name.split(".")[0]] = ("", "module")  # `import pandas.api.types` binds `pandas`
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            inside = path_inside_library(node.module, import_names)
            if inside is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                path = f"{inside}.{alias.name}" if inside else alias.name
                bindings[alias.asname or alias.name] = (path, "imported_name")
    return bindings


class UsageVisitor(ast.NodeVisitor):
    """Walks one file in source order, tracking which variables hold library objects."""

    def __init__(self, bindings: dict, known_names, relative_path: str, lines: list):
        self.bindings = bindings
        self.known_names = known_names
        self.relative_path = relative_path
        self.lines = lines
        self.scopes = [set()]  # names that hold library objects; innermost scope last
        self.usages = []
        self.unresolved_skipped = 0

    @property
    def tracked(self) -> set:
        return self.scopes[-1]

    def record(self, node, function: str, resolution: str, kind: str, keywords=()) -> None:
        line_index = min(node.lineno, len(self.lines)) - 1
        self.usages.append({
            "file": self.relative_path,
            "line": node.lineno,
            "snippet": self.lines[line_index].strip()[:200],
            "function": function,
            "resolved": resolution != "unknown",
            "resolution": resolution,
            "kind": kind,
            "keywords": list(keywords),
        })

    def library_path(self, node):
        """(dotted path, kind) if `node` is a chain like pd.api.types.is_scalar rooted at a library import, else None."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name) and node.id in self.bindings:
            base, kind = self.bindings[node.id]
            path = ".".join(part for part in [base, *reversed(parts)] if part)
            if path:
                return path, kind
        return None

    def is_library_object(self, node) -> bool:
        """True if the expression evaluates to something we believe is a library (pandas) object."""
        if isinstance(node, ast.Name):
            return node.id in self.tracked
        if isinstance(node, ast.Call):
            if self.library_path(node.func):
                return True  # the result of a library function (assumed to be a library object)
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in NON_PANDAS_RESULT:
                    return False
                return self.is_library_object(node.func.value)
            return False
        if isinstance(node, ast.Attribute):
            if node.attr in NON_PANDAS_RESULT:
                return False
            return self.is_library_object(node.value)
        if isinstance(node, ast.Subscript):
            return self.is_library_object(node.value)
        return False

    def is_library_annotation(self, annotation) -> bool:
        return any(
            self.library_path(n) for n in ast.walk(annotation) if isinstance(n, (ast.Attribute, ast.Name))
        )

    def set_tracking(self, name: str, tracked: bool) -> None:
        (self.tracked.add if tracked else self.tracked.discard)(name)

    # ---- what gets recorded -------------------------------------------------

    def visit_Call(self, node):
        keywords = [keyword.arg for keyword in node.keywords if keyword.arg]  # **kwargs has no name
        found = self.library_path(node.func)
        if found:
            self.record(node, found[0], found[1], "call", keywords)
        elif isinstance(node.func, ast.Attribute):
            receiver, name = node.func.value, node.func.attr
            if self.is_library_object(receiver):
                self.record(node, name, "tracked_variable", "call", keywords)
            elif self.known_names is not None and name in self.known_names:
                self.record(node, name, "unknown", "call", keywords)
            else:
                self.unresolved_skipped += 1
            self.visit(receiver)  # the receiver may hold further calls: df.groupby(...).agg(...)
        else:
            self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword)

    def visit_Attribute(self, node):
        found = self.library_path(node)
        if found:
            self.record(node, found[0], found[1], "reference")  # e.g. pd.Int64Index used without a call
            return
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in self.bindings:
            base, kind = self.bindings[node.id]
            if base:  # an imported name such as DataFrame; a bare module alias says nothing
                self.record(node, base, kind, "reference")

    # ---- what gets tracked --------------------------------------------------

    def visit_Assign(self, node):
        self.visit(node.value)
        holds_library_object = self.is_library_object(node.value)
        for target in node.targets:
            self.visit(target)
            if isinstance(target, ast.Name):
                self.set_tracking(target.id, holds_library_object)

    def visit_AnnAssign(self, node):
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)
        if isinstance(node.target, ast.Name):
            from_value = node.value is not None and self.is_library_object(node.value)
            self.set_tracking(node.target.id, from_value or self.is_library_annotation(node.annotation))

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
            if isinstance(item.optional_vars, ast.Name) and self.is_library_object(item.context_expr):
                self.tracked.add(item.optional_vars.id)
        for statement in node.body:
            self.visit(statement)

    visit_AsyncWith = visit_With

    def visit_FunctionDef(self, node):
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)  # defaults and annotations
        if node.returns is not None:
            self.visit(node.returns)

        arguments = node.args
        every_argument = arguments.posonlyargs + arguments.args + arguments.kwonlyargs
        every_argument += [a for a in (arguments.vararg, arguments.kwarg) if a is not None]

        scope = set(self.tracked)  # names visible from the enclosing scope
        for argument in every_argument:
            if argument.annotation is not None and self.is_library_annotation(argument.annotation):
                scope.add(argument.arg)
            else:
                scope.discard(argument.arg)  # a parameter shadows any outer name
        self.scopes.append(scope)
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


def scan_file(path: Path, base: Path, import_names: list, known_names):
    """Returns (usages, skipped unresolved calls), or None if the file could not be parsed."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError, UnicodeDecodeError) as error:
        logger.warning("Skipping %s — could not parse: %s", path, error)
        return None

    bindings = collect_bindings(tree, import_names)
    if not bindings:
        return [], 0  # this file never imports the library, so nothing in it can be a usage

    visitor = UsageVisitor(bindings, known_names, path.relative_to(base).as_posix(), re.split(r"\r\n|\r|\n", source))
    visitor.visit(tree)
    return visitor.usages, visitor.unresolved_skipped


def scan(path: str, settings: dict, known_names: set | None = None) -> list:
    """
    List every usage of the library (settings["import_names"], default [settings["library"]])
    in a file or folder of Python code.
    Returns [{"file", "line", "snippet", "function", "resolved", "resolution", "kind", "keywords"}], in file/line order.
    """
    root = Path(path)
    if not root.exists():
        logger.error("Path not found: %s", root)
        return []

    import_names = settings.get("import_names") or [settings["library"]]
    if root.is_file():
        files, base = [root], root.parent
    else:
        base = root
        files = sorted(
            p for p in root.rglob("*.py")
            if not SKIP_DIRS.intersection(p.relative_to(root).parts[:-1])
        )

    usages, skipped_calls, unparsable = [], 0, 0
    for file in files:
        result = scan_file(file, base, import_names, known_names)
        if result is None:
            unparsable += 1
            continue
        file_usages, skipped = result
        usages.extend(file_usages)
        skipped_calls += skipped

    usages.sort(key=lambda usage: (usage["file"], usage["line"]))  # stable: same-line order is kept
    logger.info(
        "Scanned %d file(s): %d usage(s) (%d resolved); skipped %d method call(s) on unresolved objects; %d file(s) could not be parsed",
        len(files), len(usages), sum(u["resolved"] for u in usages), skipped_calls, unparsable,
    )
    return usages


def known_names_from_chunks(chunks: list, function_pattern: str) -> set:
    """Method names the library documents: 'DataFrame.append()' in a chunk gives 'append'."""
    names = set()
    for chunk in chunks:
        for match in re.findall(function_pattern, chunk["text"]):
            names.add(match.rstrip("()").split(".")[-1])
    return names


if __name__ == "__main__":
    import json
    import sys

    from ingest import load_settings

    target = sys.argv[1] if len(sys.argv) > 1 else "samples/pandas1_sample"
    library_settings = load_settings("configs/pandas/pandas_settings.json")
    if library_settings is not None:
        chunks_file = Path("data/chunks") / f"{library_settings['library']}_chunks.json"
        names = None
        if chunks_file.exists():
            names = known_names_from_chunks(json.loads(chunks_file.read_text(encoding="utf-8")),
                                            library_settings["function_pattern"])
        for usage in scan(target, library_settings, names):
            print(f"{usage['file']}:{usage['line']:<4} {usage['kind']:<9} {usage['resolution']:<17} {usage['function']:<34} {usage['snippet']}")
