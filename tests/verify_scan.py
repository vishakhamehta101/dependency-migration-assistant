"""Checks for the scanner. Run from the project root: python3 tests/verify_scan.py"""
import json, logging, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, "src")
import scan as scanner

settings = json.load(open("configs/pandas/pandas_settings.json"))
SAMPLE = "samples/pandas1_sample"
ok_all = True

class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []
    def emit(self, record):
        self.messages.append(record.getMessage())

capture = Capture()
logging.getLogger("scan").addHandler(capture)

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

# the answer key, written by hand from the sample files:
# (file, line, function, resolution, kind, keyword arguments)
EXPECTED = sorted([
    ("cleaning.py", 5, "DataFrame", "imported_name", "reference", ()),
    ("cleaning.py", 6, "read_csv", "imported_name", "call", ()),
    ("cleaning.py", 7, "tolist", "tracked_variable", "call", ()),
    ("cleaning.py", 8, "append", "unknown", "call", ()),
    ("cleaning.py", 9, "agg", "tracked_variable", "call", ()),
    ("cleaning.py", 9, "groupby", "tracked_variable", "call", ()),
    ("cleaning.py", 10, "api.types.is_scalar", "module", "call", ()),
    ("cleaning.py", 14, "DataFrame", "imported_name", "reference", ()),
    ("cleaning.py", 15, "to_dict", "tracked_variable", "call", ()),
    ("cleaning.py", 16, "append", "unknown", "call", ()),
    ("io_tools.py", 5, "ExcelWriter", "module", "call", ()),
    ("io_tools.py", 7, "save", "tracked_variable", "call", ()),
    ("io_tools.py", 8, "options.mode.chained_assignment", "module", "reference", ()),
    ("io_tools.py", 9, "Int64Index", "module", "call", ()),
    ("io_tools.py", 10, "Int64Index", "module", "reference", ()),
    ("legacy_args.py", 5, "read_csv", "module", "call", ("error_bad_lines",)),
    ("legacy_args.py", 6, "groupby", "tracked_variable", "call", ("squeeze",)),
    ("legacy_args.py", 7, "to_excel", "tracked_variable", "call", ("encoding",)),
    ("loader.py", 7, "read_csv", "module", "call", ()),
    ("loader.py", 8, "DataFrame", "module", "call", ()),
    ("loader.py", 9, "append", "tracked_variable", "call", ()),
    ("loader.py", 10, "append", "unknown", "call", ()),
    ("plain_import.py", 5, "read_csv", "module", "call", ()),
])
N = len(EXPECTED)

capture.messages.clear()
usages = scanner.scan(SAMPLE, settings, known_names={"append"})
got = sorted((u["file"], u["line"], u["function"], u["resolution"], u["kind"], tuple(u["keywords"])) for u in usages)
check(f"answer key: all {N} expected usages found (with their keyword arguments), nothing extra", got == EXPECTED,
      f"missing {sorted(set(EXPECTED) - set(got))} | extra {sorted(set(got) - set(EXPECTED))}")
check("record fields are exactly the contract",
      all(set(u) == {"file", "line", "snippet", "function", "resolved", "resolution", "kind", "keywords"} for u in usages))
check("results come back in file/line order", [(u["file"], u["line"]) for u in usages] == sorted((u["file"], u["line"]) for u in usages))
by_line = {(u["file"], u["line"], u["function"]): u for u in usages}
check("trap: df.append (a DataFrame) is resolved", by_line[("loader.py", 9, "append")]["resolved"] is True)
check("trap: LOG.append (a list) is NOT claimed as pandas", by_line[("loader.py", 10, "append")]["resolved"] is False)
check("trap: rows = df.to_dict(); rows.append is NOT claimed as pandas", by_line[("cleaning.py", 16, "append")]["resolved"] is False)
check("snippet is the real source line", by_line[("loader.py", 9, "append")]["snippet"] == "df = df.append(extra)")
summary = [m for m in capture.messages if m.startswith("Scanned")][-1]
check("unresolved calls are counted, not silent (to_excel)", "skipped 1 method call(s)" in summary, summary)

chunks = json.load(open("data/chunks/pandas_chunks.json"))
names = scanner.known_names_from_chunks(chunks, settings["function_pattern"])
check("known names from real chunks include append and pivot", {"append", "pivot"} <= names, f"{len(names)} names")

with tempfile.TemporaryDirectory() as d:
    messy = Path(d) / "project"
    shutil.copytree(SAMPLE, messy)
    (messy / "broken.py").write_text("def (:\n    import pandas as pd\n")
    (messy / "kwargs_case.py").write_text("import pandas as pd\n\n\ndef f(path, opts):\n    return pd.read_csv(path, sep=',', **opts)\n")
    (messy / "venv" / "lib" / "site-packages").mkdir(parents=True)
    (messy / "venv" / "lib" / "site-packages" / "skipme.py").write_text("import pandas as pd\npd.read_csv('x')\n")
    (messy / "__pycache__").mkdir()
    (messy / "__pycache__" / "junk.py").write_text("import pandas as pd\npd.read_csv('y')\n")
    capture.messages.clear()
    messy_usages = scanner.scan(str(messy), settings, known_names={"append"})
    summary = [m for m in capture.messages if m.startswith("Scanned")][-1]
    check(f"messy folder: the {N} usages plus 1 from kwargs_case.py (venv and __pycache__ ignored)", len(messy_usages) == N + 1, f"got {len(messy_usages)}")
    check("messy folder: the syntax-error file is skipped and reported", "1 file(s) could not be parsed" in summary and any("broken.py" in m for m in capture.messages))
    kw = [u for u in messy_usages if u["file"] == "kwargs_case.py"]
    check("**opts expansion is ignored, named keyword (sep) is kept", len(kw) == 1 and kw[0]["keywords"] == ["sep"])

single = scanner.scan(f"{SAMPLE}/loader.py", settings, known_names={"append"})
check("a single file can be scanned", len(single) == 4 and all(u["file"] == "loader.py" for u in single))
check("settings without import_names fall back to the library name",
      len(scanner.scan(SAMPLE, {"library": "pandas"}, known_names={"append"})) == N)
check("library as a variable: import_names=['numpy'] finds nothing in pandas code",
      scanner.scan(SAMPLE, {"library": "numpy", "import_names": ["numpy"]}) == [])
check("the project's own src/ never imports pandas, so 0 usages", scanner.scan("src", settings) == [])

with tempfile.TemporaryDirectory() as empty:
    check("empty folder -> []", scanner.scan(empty, settings) == [])
capture.messages.clear()
check("path that does not exist -> [] and an error", scanner.scan("no/such/folder", settings) == [] and any("not found" in m for m in capture.messages))

print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
