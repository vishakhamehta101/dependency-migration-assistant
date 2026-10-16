"""Checks for the matcher's evidence tiers and statuses. Run from the project root: python3 tests/verify_match_evidence.py"""
import json, sys
sys.path.insert(0, "src")
import match, retrieve
import scan as scanner
from qdrant_client import QdrantClient

ok_all = True

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

# ---- part 1: the rules on their own (no services needed) ------------------
APPEND = "Removed deprecated Series.append() , DataFrame.append() , use concat() instead ( GH 35407 )"
EXCEL = "Removed deprecated methods ExcelWriter.write_cells() , ExcelWriter.save() , ExcelWriter.cur_sheet() ( GH 45795 )"
READ_CSV = "Removed arguments prefix , squeeze , error_bad_lines and warn_bad_lines from read_csv() ( GH 40413 )"

check("removed: a whole method is gone", match.classify(APPEND, "append", []) == ("removed", []))
check("removed: names ExcelWriter.save, so 'save' is removed", match.classify(EXCEL, "save", [])[0] == "removed")
check("the class ExcelWriter itself is NOT called removed (only methods inside it were)", match.classify(EXCEL, "ExcelWriter", [])[0] == "name_match")
check("argument-level removal is not 'removed' without the argument", match.classify(READ_CSV, "read_csv", []) == ("name_match", []))
check("keyword_match: the call passes a removed argument", match.classify(READ_CSV, "read_csv", ["error_bad_lines"]) == ("keyword_match", ["error_bad_lines"]))
check("a different argument does not match", match.classify(READ_CSV, "read_csv", ["sep"]) == ("name_match", []))
check("note that never mentions the function is dropped", match.classify(APPEND, "read_csv", []) == (None, []))
check("whole words only: 'autosave' is not 'save'", match.classify("Added an autosave option", "save", [])[0] is None)
check("whole words only: 'squeezed' is not the argument 'squeeze'", match.classify("squeezed rows now kept in read_csv()", "read_csv", ["squeeze"]) == ("name_match", []))
check("names are case-sensitive: 'append' is not 'Append'", match.classify("Append now returns a new object", "append", [])[0] is None)

def fake(rank, text, score=0.01):
    return {"rank": rank, "score": score, "semantic_rank": rank, "keyword_rank": None, "chunk": {"chunk_id": f"c{rank}", "text": text}}

usage = {"function": "read_csv", "keywords": ["error_bad_lines"], "resolved": True}
mixed = [fake(1, "Changed behavior of read_csv() where the index is now a RangeIndex"),
         fake(2, READ_CSV),
         fake(3, "Removed read_csv() entirely"),
         fake(4, "Something unrelated about to_json()")]
result = match.match_usage(usage, mixed)
check("matches are ordered removed > keyword_match > name_match, unrelated dropped",
      [m["evidence"] for m in result["matches"]] == ["removed", "keyword_match", "name_match"], str([m["evidence"] for m in result["matches"]]))
check("match ranks are 1, 2, 3", [m["rank"] for m in result["matches"]] == [1, 2, 3])
check("status is documented_change when there is strong evidence", result["status"] == "documented_change")
only_names = match.match_usage({"function": "read_csv", "keywords": []}, [fake(i, f"Note {i} about read_csv()") for i in range(1, 9)])
check("only name matches -> review, and only the top 5 are kept", only_names["status"] == "review" and len(only_names["matches"]) == 5)
check("nothing mentions the function -> no_documented_change",
      match.match_usage({"function": "tolist", "keywords": []}, [fake(1, APPEND)])["status"] == "no_documented_change")
check("no candidates at all -> no_documented_change", match.match_usage({"function": "x", "keywords": []}, [])["status"] == "no_documented_change")
check("confidence: low when the scanner could not confirm pandas, high otherwise",
      match.match_usage({"function": "append", "resolved": False}, [])["confidence"] == "low"
      and match.match_usage({"function": "append", "resolved": True}, [])["confidence"] == "high"
      and match.match_usage({"function": "append"}, [])["confidence"] == "high")

# ---- part 2: the whole chain on the sample project (real Qdrant + real model) ----
CLIENT = QdrantClient(host="localhost", port=6333)
MODEL = retrieve.get_model()

LIBRARY = "pandas"
settings = json.load(open("configs/pandas/pandas_settings.json"))
chunks = json.load(open("data/chunks/pandas_chunks.json"))
names = scanner.known_names_from_chunks(chunks, settings["function_pattern"])
usages = scanner.scan("samples/pandas1_sample", settings, known_names=names)
results = match.match_usages(usages, LIBRARY, settings, client=CLIENT, model=MODEL)
by_key = {(r["usage"]["file"], r["usage"]["line"], r["usage"]["function"]): r for r in results}

# the answer key, written by hand before the matcher: (usage) -> (evidence of the top match, start of its text)
DOCUMENTED = {
    ("loader.py", 9, "append"):        ("removed", "Removed deprecated Series.append()"),
    ("io_tools.py", 7, "save"):        ("removed", "Removed deprecated methods ExcelWriter.write_cells()"),
    ("io_tools.py", 9, "Int64Index"):  ("removed", "Removed Int64Index"),
    ("io_tools.py", 10, "Int64Index"): ("removed", "Removed Int64Index"),
    ("legacy_args.py", 5, "read_csv"): ("keyword_match", "Removed arguments prefix , squeeze , error_bad_lines"),
    ("legacy_args.py", 6, "groupby"):  ("keyword_match", "Remove argument squeeze from DataFrame.groupby()"),
    ("legacy_args.py", 7, "to_excel"): ("keyword_match", "Removed arguments verbose and encoding from DataFrame.to_excel()"),
}
REVIEW = [("loader.py", 7, "read_csv"), ("io_tools.py", 5, "ExcelWriter"), ("loader.py", 8, "DataFrame")]
NOTHING = [("cleaning.py", 7, "tolist"), ("cleaning.py", 9, "agg"), ("cleaning.py", 10, "api.types.is_scalar"),
           ("io_tools.py", 8, "options.mode.chained_assignment")]

print()
for key, (evidence, prefix) in DOCUMENTED.items():
    result = by_key[key]
    top = result["matches"][0] if result["matches"] else None
    good = result["status"] == "documented_change" and top is not None and top["evidence"] == evidence and top["chunk"]["text"].startswith(prefix)
    check(f"{key[0]}:{key[1]} {key[2]} -> documented_change, top note is the right one ({evidence})", good)
low = by_key[("loader.py", 10, "append")]
check("loader.py:10 LOG.append (a list) -> documented_change but low confidence", low["status"] == "documented_change" and low["confidence"] == "low")
for key in REVIEW:
    check(f"{key[0]}:{key[1]} {key[2]} -> review", by_key[key]["status"] == "review", f"{len(by_key[key]['matches'])} note(s) kept")
for key in NOTHING:
    check(f"{key[0]}:{key[1]} {key[2]} -> no_documented_change", by_key[key]["status"] == "no_documented_change")

print("\nevery usage in the sample project:")
print(f"{'usage':36s} | {'status':21s} | conf | notes | top evidence")
for result in results:
    u = result["usage"]
    top = result["matches"][0]["evidence"] if result["matches"] else "-"
    print(f"{u['file'] + ':' + str(u['line']) + ' ' + u['function']:36s} | {result['status']:21s} | {result['confidence']:4s} | {len(result['matches']):5d} | {top}")

print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
