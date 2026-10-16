"""Checks for saved matcher results. Run from the project root: python3 tests/verify_snapshot.py"""
import json, logging, sys, tempfile
from pathlib import Path
sys.path.insert(0, "src")
import match, snapshot

ok_all = True

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []
    def emit(self, record):
        self.messages.append(record.getMessage())

capture = Capture()
logging.getLogger("snapshot").addHandler(capture)

chunks = json.load(open("data/chunks/pandas_chunks.json"))
note = next(c for c in chunks if c["text"].startswith("Removed deprecated Series.append()"))
usage = {"file": "loader.py", "line": 9, "snippet": "df = df.append(extra)", "function": "append", "keywords": [],
         "resolved": True, "resolution": "tracked_variable", "kind": "call"}
results = [
    match.match_usage(usage, [{"rank": 1, "score": 0.0328, "semantic_rank": 1, "keyword_rank": None, "chunk": note}]),
    match.match_usage({**usage, "function": "tolist"}, []),
]

with tempfile.TemporaryDirectory() as d:
    path = str(Path(d) / "nested" / "folder" / "matches.json")
    snapshot.save_matches(results, path, "pandas", "samples/pandas1_sample")
    check("saving creates missing folders", Path(path).exists())
    check("what is loaded is exactly what was saved (including None values and floats)", snapshot.load_matches(path) == results)
    header = json.loads(Path(path).read_text())
    check("the file records the library, the folder that was scanned and the count",
          header["library"] == "pandas" and header["target"] == "samples/pandas1_sample" and header["count"] == 2)

    capture.messages.clear()
    check("a missing file gives None and a message that says how to create it",
          snapshot.load_matches(str(Path(d) / "nope.json")) is None and any("not found" in m and "snapshot.py" in m for m in capture.messages))
    broken = Path(d) / "broken.json"
    broken.write_text("{not json")
    capture.messages.clear()
    check("a broken file gives None and an error", snapshot.load_matches(str(broken)) is None and any("unreadable" in m for m in capture.messages))
    wrong = Path(d) / "wrong.json"
    wrong.write_text('{"library": "pandas"}')
    capture.messages.clear()
    check("a file without results gives None and an error", snapshot.load_matches(str(wrong)) is None and any("unreadable" in m for m in capture.messages))

print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
