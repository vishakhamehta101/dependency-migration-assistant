"""Checks for the matcher's first step. Run from the project root: python3 tests/verify_match.py"""
import json, sys
sys.path.insert(0, "src")
import match, retrieve
import scan as scanner
from qdrant_client import QdrantClient

CLIENT = QdrantClient(host="localhost", port=6333)
MODEL = retrieve.get_model()

LIBRARY = "pandas"
settings = json.load(open("configs/pandas/pandas_settings.json"))
chunks = json.load(open("data/chunks/pandas_chunks.json"))
ok_all = True

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

# 1) the query builder
check("query = function name + keyword arguments",
      match.build_query({"function": "read_csv", "keywords": ["error_bad_lines"]}) == "read_csv error_bad_lines")
check("dotted path: only the last part", match.build_query({"function": "api.types.is_scalar", "keywords": []}) == "is_scalar")
check("duplicates removed, order kept", match.build_query({"function": "f", "keywords": ["a", "a", "b"]}) == "f a b")
check("a record without a keywords field still works", match.build_query({"function": "append"}) == "append")
check("the code line is not part of the query",
      match.build_query({"function": "append", "keywords": [], "snippet": "df = df.append(extra)"}) == "append")
check("empty function -> empty query -> no candidates",
      match.build_query({"function": "", "keywords": []}) == ""
      and match.get_candidates({"function": "", "keywords": []}, LIBRARY, settings, client=CLIENT, model=MODEL) == [])

# 2) list filters: "any of these values"
filters = match.migration_filters(settings)
allowed_categories = filters["category"]
check("settings carry migration_filters", filters == {"category": ["breaking_change", "deprecation"], "version": ["2.0.0"]}, str(filters))
searches = {
    "hybrid search": retrieve.search("read_csv", LIBRARY, top_k=40, filters=filters, client=CLIENT, model=MODEL),
    "keyword search": retrieve.keyword_search("read_csv", LIBRARY, top_k=40, filters=filters, client=CLIENT),
    "semantic search": retrieve.semantic_search("read_csv", LIBRARY, top_k=40, filters=filters, client=CLIENT, model=MODEL),
}
for label, results in searches.items():
    good = bool(results) and all(r["chunk"]["category"] in allowed_categories and r["chunk"]["version"] == "2.0.0" for r in results)
    check(f"list filters respected by {label}", good, f"{len(results)} results")
unfiltered = retrieve.search("read_csv", LIBRARY, top_k=40, client=CLIENT, model=MODEL)
categories_seen = sorted({r["chunk"]["category"] for r in unfiltered})
check("the filter really narrows things (unfiltered search spans more categories)", len(categories_seen) > len(allowed_categories), str(categories_seen))
single = retrieve.search("regression", LIBRARY, top_k=20, filters={"version": "2.0.1"}, client=CLIENT, model=MODEL)
check("single-value filters still work", bool(single) and all(r["chunk"]["version"] == "2.0.1" for r in single))

# 3) candidate retrieval on the sample project: where does the documented change land?
names = scanner.known_names_from_chunks(chunks, settings["function_pattern"])
usages = scanner.scan("samples/pandas1_sample", settings, known_names=names)
by_key = {(u["file"], u["line"], u["function"]): u for u in usages}

MUST_FIND = [
    (("loader.py", 9, "append"),        lambda c: c["text"].startswith("Removed deprecated Series.append()")),
    (("io_tools.py", 7, "save"),        lambda c: c["text"].startswith("Removed deprecated methods ExcelWriter.write_cells()")),
    (("io_tools.py", 9, "Int64Index"),  lambda c: c["text"].startswith("Removed Int64Index")),
    (("io_tools.py", 10, "Int64Index"), lambda c: c["text"].startswith("Removed Int64Index")),
    (("legacy_args.py", 5, "read_csv"), lambda c: c["text"].startswith("Removed arguments prefix , squeeze , error_bad_lines")),
    (("legacy_args.py", 6, "groupby"),  lambda c: c["text"].startswith("Remove argument squeeze from DataFrame.groupby()")),
    (("legacy_args.py", 7, "to_excel"), lambda c: c["text"].startswith("Removed arguments verbose and encoding from DataFrame.to_excel()")),
]
print("\nwhere the documented change lands among the candidates (1 = best, '-' = missing):")
print(f"{'usage':34s} | {'query':28s} | candidates | rank")
for key, is_expected in MUST_FIND:
    usage = by_key[key]
    candidates = match.get_candidates(usage, LIBRARY, settings, client=CLIENT, model=MODEL)
    rank = next((c["rank"] for c in candidates if is_expected(c["chunk"])), None)
    ok = rank is not None and rank <= 10
    ok_all &= ok
    print(f"{key[0] + ':' + str(key[1]) + ' ' + key[2]:34s} | {match.build_query(usage):28s} | {len(candidates):10d} | {'-' if rank is None else rank}  {'ok' if ok else 'MISS'}")

example = by_key[("legacy_args.py", 5, "read_csv")]
print("\ntop 3 candidates for legacy_args.py:5 read_csv(error_bad_lines=False):")
for c in match.get_candidates(example, LIBRARY, settings, client=CLIENT, model=MODEL)[:3]:
    print(f"  {c['rank']} | fused {c['score']:.4f} | semantic #{c['semantic_rank']} | keyword #{c['keyword_rank']} | {c['chunk']['text'][:75]}")

print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
