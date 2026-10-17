"""Checks for the local web app's backend (fake AI, stub embedder, temporary store; no Docker, no model, no internet).
Run from the project root: python3 tests/verify_app.py"""
import hashlib, io, json, re, sys, tempfile, threading, time, zipfile
from pathlib import Path
sys.path.insert(0, "src")
import numpy as np
from fastapi.testclient import TestClient
import logging
import app as webapp, pipeline
logging.getLogger("app").setLevel(logging.CRITICAL)   # the deliberate failures below would otherwise print stack traces

ok_all = True

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

class StubEmbedder:
    def encode(self, texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False):
        out = []
        for t in texts:
            v = np.random.default_rng(int(hashlib.md5(t.encode()).hexdigest()[:8], 16)).normal(size=384)
            out.append(v / np.linalg.norm(v))
        return np.array(out)

class HonestModel:
    """Says 'affected' on whatever it is shown, quoting the first words of Note 1 exactly. Can be made to wait."""
    gate = None
    def __call__(self, system, user, schema):
        if HonestModel.gate is not None:
            HonestModel.gate.wait(10)
        note1 = re.search(r"Note 1\nSource: [^\n]*\nText: (.*?)(?:\n\nNote 2|\Z)", user, re.S).group(1)
        return json.dumps({"verdict": "affected", "explanation": "This call uses what the note says was removed.", "suggested_fix": None,
                           "citations": [{"note": 1, "quote": " ".join(note1.split()[:8])}]})

class BrokenModel:
    def __call__(self, system, user, schema):
        raise RuntimeError("boom")

SETTINGS_PATH = "configs/pandas/pandas_settings.json"
settings = json.load(open(SETTINGS_PATH))
workdir = tempfile.mkdtemp()
tmp_root = Path(workdir) / "tmp"
tmp_root.mkdir()
store = pipeline.open_store("embedded", str(Path(workdir) / "q"))
pipeline.ensure_index(store, settings, SETTINGS_PATH, model=StubEmbedder())
health = {"up": True, "model_ready": True}
factory = {"model": HonestModel}

def make_app(**kwargs):
    options = dict(settings=settings, store=store, llm_factory=lambda: factory["model"](), llm_health=lambda: dict(health),
                   embedding_model=StubEmbedder(), model_name="fake-model", index_notes=832, tmp_root=str(tmp_root))
    options.update(kwargs)
    return webapp.create_app(**options)

client = TestClient(make_app())

def wait_done(c, job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise TimeoutError("job did not finish")

def leftovers():
    return [p for p in tmp_root.iterdir()]

# ---- the page and the status ----------------------------------------------------------------------------
page = client.get("/")
check("the page is served as HTML", page.status_code == 200 and "text/html" in page.headers["content-type"] and "Dependency Migration Assistant" in page.text)
check("the page offers the three ways in: folder, zip, paste", all(f'data-mode="{m}"' in page.text for m in ("path", "zip", "paste")))
check("the page uses no external resources (nothing is loaded from the internet)", not re.search(r'(src|href)="https?://|@import|url\(http', page.text))
check("the page never writes HTML strings from data (no innerHTML anywhere)", "innerHTML" not in page.text and "insertAdjacentHTML" not in page.text and "document.write" not in page.text)
check("the prototype banner and the how-to-read panel exist in the page", 'id="banner"' in page.text and 'id="howto"' in page.text)
check("the API docs endpoints are switched off", client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404)
check("the app listens on this computer only", webapp.HOST == "127.0.0.1")

status = client.get("/api/status").json()
check("status reports library, version, model, index size and readiness", status["library"] == "pandas" and status["versions"] == "2.0.0" and status["model"] == "fake-model"
      and status["index_notes"] == 832 and status["ai_up"] and status["ai_model_ready"] and status["busy"] is False)
check("status states the limits", status["limits"]["max_py_files"] == 2000 and status["limits"]["max_zip_mb"] == 20)

# ---- a run on a folder ------------------------------------------------------------------------------------------
response = client.post("/api/jobs", json={"kind": "path", "path": "samples/pandas1_sample"})
check("starting a run on a folder returns a job id", response.status_code == 200 and re.fullmatch(r"[0-9a-f]{12}", response.json()["job_id"]))
job = wait_done(client, response.json()["job_id"])
result = job["result"]
check("the run finishes with the known counts", job["status"] == "done" and result["counts"] == {"affected": 7, "needs_review": 10, "unsure": 3, "no_documented_change": 4}, str(result and result["counts"]))
check("the job reports its stage, progress and label", job["stage"] == "report" and job["done"] == job["total"] == 1 and job["label"] == "samples/pandas1_sample")
check("the result has 24 findings, the model name, the version and the report", len(result["findings"]) == 24 and result["model"] == "fake-model" and result["versions"] == "2.0.0" and result["report"].startswith("# Migration report"))
check("the result carries the how-to-read bullets", len(result["how_to_read"]) == 5 and "No documented change does not mean safe" in " ".join(result["how_to_read"]))
check("the busy flag clears after the run", client.get("/api/status").json()["busy"] is False)

download = client.get(f"/api/jobs/{job['id']}/report.md")
check("the report downloads as a Markdown attachment, identical to the one in the result", download.status_code == 200 and "attachment" in download.headers["content-disposition"]
      and download.text == result["report"] and "markdown" in download.headers["content-type"])

# ---- what the page is given for each finding ---------------------------------------------------------------------------
by_verdict = {}
for f in result["findings"]:
    by_verdict.setdefault(f["verdict"], []).append(f)
affected = by_verdict["affected"][0]
check("an affected finding carries its quote, the full note, the version and a source URL, and no review notes",
      affected["citations"] and affected["citations"][0]["note_text"].startswith("Removed") and affected["citations"][0]["version"] == "2.0.0"
      and affected["citations"][0]["url"].startswith("http") and affected["notes"] == [])
review = by_verdict["needs_review"][0]
check("a needs-review finding lists its matched notes and carries no citations", len(review["notes"]) >= 1 and review["citations"] == [] and all(n["url"].startswith("http") for n in review["notes"]))
check("no finding exposes internal fields (trace, flags, raw model output, matcher scores)", all(set(f) == {"file", "line", "function", "snippet", "verdict", "explanation", "suggested_fix", "confidence", "citations", "notes"} for f in result["findings"]))
capped = [f for f in by_verdict["unsure"] if "could not confirm that it is one" in f["explanation"]]
check("an unconfirmed object is described plainly in the page too", len(capped) >= 1 and all("uses what the note says" not in f["explanation"] for f in capped))

# ---- paste and zip -------------------------------------------------------------------------------------------------------
before = len(leftovers())
code = "import pandas as pd\ndf = pd.DataFrame()\ndf = df.append(df)\n"
response = client.post("/api/jobs", json={"kind": "paste", "code": code})
paste_job = wait_done(client, response.json()["job_id"])
check("pasted code is checked and labelled 'pasted code', not with a temporary path", paste_job["status"] == "done" and paste_job["label"] == "pasted code"
      and "pasted code" in paste_job["result"]["report"] and "dma_" not in paste_job["result"]["report"])
check("the pasted code's break is found", any(f["verdict"] == "affected" and f["function"] == "append" for f in paste_job["result"]["findings"]))
check("the temporary folder is deleted afterwards", len(leftovers()) == before)

def make_zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()

good = make_zip({"app/main.py": code, "README.md": "hello", "venv/lib/skipped.py": "import pandas as pd\npd.DataFrame().append(1)\n"})
response = client.post("/api/jobs/zip", files={"file": ("my_project.zip", good, "application/zip")})
zip_job = wait_done(client, response.json()["job_id"])
check("an uploaded zip is checked, labelled with the zip's name, and files in skipped folders are ignored",
      zip_job["status"] == "done" and zip_job["label"] == "my_project.zip" and {f["file"] for f in zip_job["result"]["findings"]} == {"app/main.py"})
check("the zip's temporary folder is deleted too", len(leftovers()) == before)

for label, data, expected in (
        ("a path that climbs out of the folder (../)", make_zip({"../evil.py": "x=1", "ok.py": "y=2"}), "unsafe path"),
        ("an absolute path", make_zip({"/etc/evil.py": "x=1"}), "unsafe path"),
        ("a Windows drive path", make_zip({"C:\\evil.py": "x=1"}), "unsafe path"),
        ("a zip with no Python files", make_zip({"notes.txt": "hi"}), "No Python files"),
        ("a file that is not a zip", b"this is not a zip file", "not a zip")):
    r = client.post("/api/jobs/zip", files={"file": ("x.zip", data, "application/zip")})
    check(f"a zip with {label} is refused with a clear message, and nothing is left behind", r.status_code == 400 and expected in r.json()["detail"] and len(leftovers()) == before, r.json().get("detail", "")[:60])
small = TestClient(make_app(max_py_files=2))
r = small.post("/api/jobs/zip", files={"file": ("x.zip", make_zip({f"f{i}.py": "x=1" for i in range(3)}), "application/zip")})
check("a zip with too many Python files is refused", r.status_code == 400 and "limit is 2" in r.json()["detail"])
original_limit = webapp.MAX_ZIP_BYTES
webapp.MAX_ZIP_BYTES = 100
r = client.post("/api/jobs/zip", files={"file": ("big.zip", good, "application/zip")})
webapp.MAX_ZIP_BYTES = original_limit
check("an upload over the size limit is refused", r.status_code == 413 and "too large" in r.json()["detail"])
try:
    bomb_dir = Path(workdir) / "bomb"; bomb_dir.mkdir()
    webapp.extract_zip(make_zip({"a.py": "x" * 5000}), bomb_dir, max_unpacked=1000)
    check("a zip that unpacks to far too much is refused", False)
except ValueError as error:
    check("a zip that unpacks to far too much is refused", "too large" in str(error))

# ---- input validation --------------------------------------------------------------------------------------------------------
check("a folder that does not exist is refused", client.post("/api/jobs", json={"kind": "path", "path": "/no/such/folder"}).json()["detail"].startswith("Folder not found"))
check("an empty folder path is refused", client.post("/api/jobs", json={"kind": "path", "path": "  "}).status_code == 400)
empty_dir = Path(workdir) / "no_python"; empty_dir.mkdir(); (empty_dir / "a.txt").write_text("x")
check("a folder without Python files is refused with a clear message", "No Python files" in client.post("/api/jobs", json={"kind": "path", "path": str(empty_dir)}).json()["detail"])
many = Path(workdir) / "many"; many.mkdir()
for i in range(3):
    (many / f"m{i}.py").write_text("x=1")
check("a folder with too many Python files is refused", "more than 2" in small.post("/api/jobs", json={"kind": "path", "path": str(many)}).json()["detail"])
check("empty pasted code is refused", client.post("/api/jobs", json={"kind": "paste", "code": "   \n"}).status_code == 400)
r = client.post("/api/jobs", json={"kind": "paste", "code": "x" * (webapp.MAX_PASTE_CHARS + 1)})
check("too much pasted code is refused", r.status_code == 413 and len(leftovers()) == before)
check("an unknown kind is refused", client.post("/api/jobs", json={"kind": "ftp"}).status_code == 422)
check("an unknown job id gives 404, for the state and for the report", client.get("/api/jobs/nope").status_code == 404 and client.get("/api/jobs/nope/report.md").status_code == 404)

# ---- the AI server being down, one run at a time, errors ---------------------------------------------------------------------------
health["up"] = False
r = client.post("/api/jobs", json={"kind": "paste", "code": code})
check("with the AI server down, no run starts and the message says how to start it", r.status_code == 503 and "ollama serve" in r.json()["detail"] and len(leftovers()) == before)
check("status shows the AI server as down", client.get("/api/status").json()["ai_up"] is False)
health.update(up=True, model_ready=False)
check("with the model missing, the message says how to download it", "ollama pull fake-model" in client.post("/api/jobs", json={"kind": "paste", "code": code}).json()["detail"])
health.update(up=True, model_ready=True)

HonestModel.gate = threading.Event()
first = client.post("/api/jobs", json={"kind": "paste", "code": code}).json()["job_id"]
time.sleep(0.3)
second = client.post("/api/jobs", json={"kind": "paste", "code": code})
check("while a run is in progress, a second one is refused", second.status_code == 409 and "in progress" in second.json()["detail"] and client.get("/api/status").json()["busy"] is True)
check("the report of an unfinished run is not available yet", client.get(f"/api/jobs/{first}/report.md").status_code == 409)
HonestModel.gate.set()
wait_done(client, first)
HonestModel.gate = None
check("after it finishes, a new run is accepted", client.post("/api/jobs", json={"kind": "paste", "code": code}).status_code == 200)
wait_done(client, next(iter(client.app.state.jobs)))
time.sleep(1.0)

factory["model"] = BrokenModel
r = client.post("/api/jobs", json={"kind": "paste", "code": code})
broken = wait_done(client, r.json()["job_id"])
check("a model that fails on every call still gives a finished run, with the affected usage shown as unsure (never a crash)",
      broken["status"] == "done" and any(f["verdict"] == "unsure" for f in broken["result"]["findings"]))
factory["model"] = HonestModel
original_run = pipeline.run
pipeline.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the scanner exploded"))
r = client.post("/api/jobs", json={"kind": "paste", "code": code})
failed = wait_done(client, r.json()["job_id"])
pipeline.run = original_run
check("an unexpected error ends the run with a message, and the temporary folder is still removed", failed["status"] == "error" and "scanner exploded" in failed["error"] and len(leftovers()) == before)
check("the app is usable after an error", client.get("/api/status").json()["busy"] is False and client.post("/api/jobs", json={"kind": "paste", "code": code}).status_code == 200)
time.sleep(1.5)

# ---- environment ----------------------------------------------------------------------------------------------------------------------
import os
os.environ.pop("HF_HUB_OFFLINE", None)
webapp.configure_environment(online=False)
check("by default the model libraries are told not to use the internet", os.environ.get("HF_HUB_OFFLINE") == "1")
os.environ.pop("HF_HUB_OFFLINE", None)
webapp.configure_environment(online=True)
check("--online leaves them alone", "HF_HUB_OFFLINE" not in os.environ)

store.close()
print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
