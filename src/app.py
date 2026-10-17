"""
Dependency Migration Assistant — the local web app

A small FastAPI server and one HTML page (ui/index.html). You give it a folder, a zip or pasted code; it
runs the pipeline in the background and the page shows progress and the findings.

  - local only: it listens on 127.0.0.1, so nothing outside this computer can reach it
  - one run at a time (the AI model and the embedded index are single-user)
  - uploaded zips are unpacked defensively (no paths outside the temp folder, only .py files, size limits)
  - the page builds everything with textContent, never innerHTML, because code and changelog text are data

  python3 src/app.py [--port 8000] [--online] [--open]
"""

import datetime
import logging
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import pipeline
import report
import scan

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
MAX_PASTE_CHARS = 200_000
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_ZIP_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_PY_FILES = 2000
UI_FILE = Path(__file__).resolve().parent.parent / "ui" / "index.html"
AI_DOWN = "The AI model server is not running. In a second Terminal tab run: ollama serve"


class JobRequest(BaseModel):
    kind: Literal["path", "paste"]
    path: str = ""
    code: str = ""


def configure_environment(online: bool) -> None:
    """Unless told otherwise, stop the model libraries from contacting the internet (the models are already on disk)."""
    if not online:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")


def count_python_files(folder: Path, limit: int) -> int:
    """Python files below a folder, skipping the folders the scanner skips; stops counting once past `limit`."""
    count = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in scan.SKIP_DIRS]
        count += sum(1 for name in files if name.endswith(".py"))
        if count > limit:
            return count
    return count


def extract_zip(data: bytes, destination: Path, max_files: int = MAX_PY_FILES, max_unpacked: int = MAX_ZIP_UNPACKED_BYTES) -> int:
    """Unpack only the .py files of a zip, safely. Returns how many were written; raises ValueError with a message for people."""
    import io
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValueError("That file is not a zip archive.")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        wanted = []
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in parts:
                raise ValueError(f"The zip contains an unsafe path: {info.filename}")
            if info.is_dir() or not name.lower().endswith(".py") or any(part in scan.SKIP_DIRS for part in parts[:-1]):
                continue
            wanted.append((info, parts))
        if not wanted:
            raise ValueError("No Python files found in the zip.")
        if len(wanted) > max_files:
            raise ValueError(f"The zip has {len(wanted)} Python files; the limit is {max_files}.")
        if sum(info.file_size for info, _ in wanted) > max_unpacked:
            raise ValueError("The Python files in the zip are too large once unpacked.")
        root = destination.resolve()
        for info, parts in wanted:
            target = destination.joinpath(*parts)
            if root not in target.resolve().parents:
                raise ValueError(f"The zip contains an unsafe path: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
    return len(wanted)


def source_of(chunk: dict) -> dict:
    return {"version": chunk.get("version"), "section": chunk.get("section"), "subsection": chunk.get("subsection"), "url": chunk.get("source_url")}


def to_ui_finding(finding: dict, library: str) -> dict:
    """What the page needs for one finding: no trace, no flags, no raw model output."""
    usage, verdict = finding["usage"], finding["verdict"]
    out = {"file": usage["file"], "line": usage["line"], "function": usage["function"], "snippet": usage.get("snippet", ""),
           "verdict": verdict, "explanation": report.display_explanation(finding, library), "suggested_fix": finding.get("suggested_fix"),
           "confidence": finding["confidence"], "citations": [], "notes": []}
    for citation in finding["citations"]:
        chunk = finding["matches"][citation["note"] - 1]["chunk"]
        out["citations"].append({"quote": citation["quote"], "note_text": chunk["text"], **source_of(chunk)})
    if verdict in ("needs_review", "unsure"):
        out["notes"] = [{"text": m["chunk"]["text"], **source_of(m["chunk"])} for m in finding["matches"]]
    return out


def create_app(settings: dict, store, llm_factory, llm_health, embedding_model=None, model_name: str = "", index_notes: int = 0,
               max_py_files: int = MAX_PY_FILES, ui_file: Path = UI_FILE, tmp_root: str | None = None) -> FastAPI:
    """Build the web app. llm_factory() gives the AI function for a run; llm_health() says whether the AI server and model are ready."""
    app = FastAPI(title="Dependency Migration Assistant", docs_url=None, redoc_url=None, openapi_url=None)
    library = settings["library"]
    versions = ", ".join(settings.get("migration_filters", {}).get("version", []))
    jobs, current, lock = {}, {"id": None}, threading.Lock()
    app.state.jobs, app.state.current = jobs, current

    def run_job(job: dict, folder: Path, label: str, cleanup: Path | None) -> None:
        def progress(stage, done, total, message):
            with lock:
                job.update(stage=stage, done=done, total=total, message=message)
        outcome = {}
        try:
            result = pipeline.run(str(folder), settings, llm_factory(), store, embedding_model=embedding_model, progress=progress,
                                  model_name=model_name, generated=datetime.date.today().isoformat(), target_label=label)
            outcome = {"status": "done", "result": {
                "label": label, "library": library, "versions": versions, "model": model_name, "counts": result["counts"],
                "timings": {k: round(v, 1) for k, v in result["timings"].items()}, "report": result["report"],
                "findings": [to_ui_finding(f, library) for f in result["findings"]], "how_to_read": report.how_to_read(model_name)}}
        except Exception as error:  # the page shows the message; the app stays usable
            logger.exception("Run failed")
            outcome = {"status": "error", "error": f"{type(error).__name__}: {error}"}
        finally:
            if cleanup is not None:
                shutil.rmtree(cleanup, ignore_errors=True)
            with lock:
                current["id"] = None
        # The status flips last: by the time anyone sees "done", the temporary folder is gone and a new run can start.
        job.update({k: v for k, v in outcome.items() if k != "status"})
        job["status"] = outcome["status"]

    def start(folder: Path, label: str, cleanup: Path | None) -> dict:
        health = llm_health()
        if not health["up"]:
            raise HTTPException(503, AI_DOWN)
        if not health["model_ready"]:
            raise HTTPException(503, f"The AI model {model_name} is not downloaded. Run: ollama pull {model_name}")
        with lock:
            if current["id"] is not None:
                raise HTTPException(409, "Another run is in progress. Wait for it to finish.")
            job_id = uuid.uuid4().hex[:12]
            job = {"id": job_id, "status": "running", "stage": "scan", "done": 0, "total": 1, "message": "starting", "label": label,
                   "error": None, "result": None}
            jobs[job_id] = job
            current["id"] = job_id
        threading.Thread(target=run_job, args=(job, folder, label, cleanup), daemon=True).start()
        return {"job_id": job_id}

    def refuse_if_busy() -> None:
        if current["id"] is not None:
            raise HTTPException(409, "Another run is in progress. Wait for it to finish.")

    @app.get("/")
    def page():
        return FileResponse(ui_file, media_type="text/html")

    @app.get("/api/status")
    def status():
        health = llm_health()
        return {"library": library, "versions": versions, "model": model_name, "ai_up": health["up"], "ai_model_ready": health["model_ready"],
                "index_notes": index_notes, "busy": current["id"] is not None,
                "limits": {"max_py_files": max_py_files, "max_paste_chars": MAX_PASTE_CHARS, "max_zip_mb": MAX_ZIP_BYTES // (1024 * 1024)}}

    @app.post("/api/jobs")
    def create_job(request: JobRequest):
        refuse_if_busy()
        if request.kind == "path":
            folder = Path(request.path).expanduser()
            if not request.path.strip() or not folder.is_dir():
                raise HTTPException(400, f"Folder not found: {request.path}")
            found = count_python_files(folder, max_py_files)
            if found == 0:
                raise HTTPException(400, "No Python files found in that folder.")
            if found > max_py_files:
                raise HTTPException(400, f"That folder has more than {max_py_files} Python files. Pick a smaller one.")
            return start(folder, str(folder), None)
        if not request.code.strip():
            raise HTTPException(400, "Paste some Python code first.")
        if len(request.code) > MAX_PASTE_CHARS:
            raise HTTPException(413, f"That is too much code to paste (the limit is {MAX_PASTE_CHARS:,} characters).")
        folder = Path(tempfile.mkdtemp(prefix="dma_", dir=tmp_root))
        (folder / "pasted.py").write_text(request.code, encoding="utf-8")
        try:
            return start(folder, "pasted code", folder)
        except HTTPException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    @app.post("/api/jobs/zip")
    async def create_zip_job(file: UploadFile):
        refuse_if_busy()
        data = await file.read(MAX_ZIP_BYTES + 1)
        if len(data) > MAX_ZIP_BYTES:
            raise HTTPException(413, f"That zip is too large (the limit is {MAX_ZIP_BYTES // (1024 * 1024)} MB).")
        folder = Path(tempfile.mkdtemp(prefix="dma_", dir=tmp_root))
        try:
            extract_zip(data, folder, max_files=max_py_files)
            return start(folder, Path(file.filename or "upload.zip").name, folder)
        except ValueError as error:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(400, str(error))
        except HTTPException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    @app.get("/api/jobs/{job_id}")
    def job_state(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such run.")
        with lock:
            return dict(job)

    @app.get("/api/jobs/{job_id}/report.md")
    def job_report(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such run.")
        if job["status"] != "done":
            raise HTTPException(409, "The run is not finished yet.")
        return Response(job["result"]["report"], media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="migration_report.md"'})

    return app


def main() -> None:
    import argparse
    import threading as _threading
    import webbrowser

    parser = argparse.ArgumentParser(description="Run the local web app")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--library", default="pandas")
    parser.add_argument("--store", default="embedded", choices=("embedded", "server"))
    parser.add_argument("--online", action="store_true", help="allow the model libraries to contact the internet")
    parser.add_argument("--open", action="store_true", help="open the page in your browser")
    args = parser.parse_args()
    configure_environment(args.online)

    import uvicorn

    import llm
    from ingest import load_settings

    settings_path = f"configs/{args.library}/{args.library}_settings.json"
    settings, config = load_settings(settings_path), llm.load_llm_config()
    if settings is None or config is None:
        raise SystemExit(1)
    store = pipeline.open_store(args.store)
    count, built = pipeline.ensure_index(store, settings, settings_path)
    print(f"index: {count} notes ({'built now' if built else 'already there'})", flush=True)

    def health():
        up = llm.is_server_up(config["base_url"])
        return {"up": up, "model_ready": up and config["model"] in llm.list_models(config["base_url"])}

    app = create_app(settings, store, lambda: llm.OllamaModel.from_config(config), health, model_name=config["model"], index_notes=count)
    url = f"http://{HOST}:{args.port}"
    print(f"Open {url}   (Ctrl+C here to stop)", flush=True)
    if args.open:
        _threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
    finally:
        store.close()


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    main()
