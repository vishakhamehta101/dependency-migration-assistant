"""Checks for the pipeline and the embedded store (no real models, no Docker). Run from the project root: python3 tests/verify_pipeline.py"""
import hashlib, json, re, sys, tempfile
from collections import Counter
from pathlib import Path
sys.path.insert(0, "src")
import numpy as np
import pipeline, retrieve

ok_all = True

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

class StubEmbedder:
    """Stands in for the embedding model: a repeatable pseudo-vector per text."""
    def encode(self, texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False):
        out = []
        for t in texts:
            v = np.random.default_rng(int(hashlib.md5(t.encode()).hexdigest()[:8], 16)).normal(size=384)
            out.append(v / np.linalg.norm(v))
        return np.array(out)

class HonestModel:
    """Says 'affected' on whatever it is shown, quoting the first words of Note 1 exactly."""
    def __init__(self):
        self.calls = 0
    def __call__(self, system, user, schema):
        self.calls += 1
        note1 = re.search(r"Note 1\nSource: [^\n]*\nText: (.*?)(?:\n\nNote 2|\Z)", user, re.S).group(1)
        return json.dumps({"verdict": "affected", "explanation": "This call uses what the note says was removed.", "suggested_fix": None,
                           "citations": [{"note": 1, "quote": " ".join(note1.split()[:8])}]})

class DownModel:
    def __call__(self, system, user, schema):
        raise ConnectionError("the AI server is down")

SETTINGS_PATH = "configs/pandas/pandas_settings.json"
settings = json.load(open(SETTINGS_PATH))
workdir = tempfile.mkdtemp()

# ---- the embedded store ---------------------------------------------------------------------------------------
store_path = str(Path(workdir) / "nested" / "q")
client = pipeline.open_store("embedded", store_path)
check("the embedded store creates its folder and needs no server", Path(store_path).is_dir() and client is not None)
check("the server store is only created on request", type(pipeline.open_store("server")).__name__ == "QdrantClient")
try:
    pipeline.open_store("cloud")
    check("an unknown store name is refused", False)
except ValueError as error:
    check("an unknown store name is refused, with the valid choices in the message", "embedded" in str(error) and "server" in str(error))

count, built = pipeline.ensure_index(client, settings, SETTINGS_PATH, model=StubEmbedder())
check("the first call builds the index from the saved chunks", built and count == 832, f"{count} notes, built={built}")
real_build = retrieve.build_index
retrieve.build_index = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not rebuild"))
count2, built2 = pipeline.ensure_index(client, settings, SETTINGS_PATH, model=StubEmbedder())
retrieve.build_index = real_build
check("the second call reuses it and never rebuilds", not built2 and count2 == 832)

# ---- releasing the embedding model ---------------------------------------------------------------------------------
retrieve._MODEL_CACHE["dummy"] = object()
retrieve.release_model()
check("release_model empties the embedding model cache", retrieve._MODEL_CACHE == {})

# ---- a full run -------------------------------------------------------------------------------------------------------
events = []
llm_model = HonestModel()
result = pipeline.run("samples/pandas1_sample", settings, llm_model, client, embedding_model=StubEmbedder(),
                      progress=lambda stage, done, total, message: events.append((stage, done, total, message)),
                      model_name="fake-model", generated="2026-10-17")
statuses = Counter(r["status"] for r in result["match_results"])
check("the run finds the 24 usages in the sample project", len(result["usages"]) == 24)
check("the matcher results are the known ones: 10 documented, 10 review, 4 nothing",
      statuses == {"documented_change": 10, "review": 10, "no_documented_change": 4}, str(dict(statuses)))
check("the findings are the known ones: 7 affected, 10 needs_review, 3 unsure, 4 no documented change",
      result["counts"] == {"affected": 7, "needs_review": 10, "unsure": 3, "no_documented_change": 4}, str(result["counts"]))
check("the AI is asked only about the 10 documented changes (12 calls would mean retries; the fake never needs them)", llm_model.calls == 10, str(llm_model.calls))
check("the report is included and is the real report", result["report"].startswith("# Migration report: pandas → 2.0.0") and "| **AFFECTED** | 7 |" in result["report"]
      and "fake-model" in result["report"] and "generated 2026-10-17" in result["report"])
check("the result carries the library, the target and the model name", result["library"] == "pandas" and result["target"] == "samples/pandas1_sample" and result["model"] == "fake-model")
check("timings are recorded for every stage", set(result["timings"]) == {"scan", "match", "explain", "report"} and all(v >= 0 for v in result["timings"].values()))

stages = [e[0] for e in events]
check("progress goes scan -> match -> explain -> report, in that order", stages == sorted(stages, key=["scan", "match", "explain", "report"].index) and set(stages) == {"scan", "match", "explain", "report"})
explain_events = [e for e in events if e[0] == "explain"]
check("explain reports one step per usage, counting up to the total", [e[1] for e in explain_events] == list(range(1, 25)) and all(e[2] == 24 for e in explain_events))
check("the last event is the report being done", events[-1][:3] == ("report", 1, 1))
check("the scan message says how many usages were found", any(e[0] == "scan" and "24 usage(s) found" in e[3] for e in events))

# ---- memory: the embedding model is released after matching ------------------------------------------------------------
released = []
retrieve.release_model = lambda: released.append(True)
pipeline.run("samples/pandas1_sample", settings, HonestModel(), client, embedding_model=StubEmbedder())
pipeline.run("samples/pandas1_sample", settings, HonestModel(), client, embedding_model=StubEmbedder(), release_model=False)
check("the embedding model is released after matching, unless told not to", released == [True])
retrieve.release_model = lambda: None

# ---- repeatable, and robust --------------------------------------------------------------------------------------------------
again = pipeline.run("samples/pandas1_sample", settings, HonestModel(), client, embedding_model=StubEmbedder(), model_name="fake-model", generated="2026-10-17")
check("the same folder gives the same findings and the same report", [(f["usage"]["line"], f["verdict"], f["citations"]) for f in again["findings"]] ==
      [(f["usage"]["line"], f["verdict"], f["citations"]) for f in result["findings"]] and again["report"] == result["report"])

down = pipeline.run("samples/pandas1_sample", settings, DownModel(), client, embedding_model=StubEmbedder())
check("with the AI server down, the run still finishes: every asked usage is 'unsure' and flagged",
      down["counts"].get("unsure") == 10 and down["counts"].get("needs_review") == 10 and all(f["flags"] == ["model_error"] for f in down["findings"] if f["verdict"] == "unsure"))

empty = Path(workdir) / "nothing_here"
empty.mkdir()
(empty / "notes.txt").write_text("no python in here")
silent = HonestModel()
nothing = pipeline.run(str(empty), settings, silent, client, embedding_model=StubEmbedder())
check("a folder without Python files gives an empty, honest report and asks no one", nothing["usages"] == [] and silent.calls == 0 and "nothing to report" in nothing["report"])

try:
    pipeline.run(str(Path(workdir) / "does_not_exist"), settings, HonestModel(), client, embedding_model=StubEmbedder())
    check("a folder that does not exist is refused", False)
except FileNotFoundError as error:
    check("a folder that does not exist is refused with a clear message", "Folder not found" in str(error))

client.close()
print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
