"""
Dependency Migration Assistant — the pipeline

One call runs everything: scan a folder for uses of the library, match each use against the changelog
notes, ask the AI about the confirmed ones, and build the report. The web page, the command line and the
evaluation all use this one function, so they cannot drift apart.

The retrieval store runs "embedded" by default: a folder on disk, no Docker, no server. The embedding
model is released as soon as matching is done, so the AI model has the memory.

  python3 src/pipeline.py FOLDER [--out FILE] [--store embedded|server] [--library pandas]
"""

import json
import logging
import time
from collections import Counter
from pathlib import Path

from qdrant_client import QdrantClient

import explain
import match
import report
import retrieve
import scan

logger = logging.getLogger(__name__)

EMBEDDED_PATH = "data/qdrant_local"


def open_store(mode: str = "embedded", path: str = EMBEDDED_PATH):
    """The retrieval store: 'embedded' (a folder, no server) or 'server' (Qdrant in Docker)."""
    if mode == "server":
        return QdrantClient(host=retrieve.QDRANT_HOST, port=retrieve.QDRANT_PORT)
    if mode != "embedded":
        raise ValueError(f"unknown store {mode!r}: use 'embedded' or 'server'")
    Path(path).mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=path)


def ensure_index(client, settings: dict, settings_path: str, model=None) -> tuple:
    """(number of notes in the index, whether it had to be built). Builds only when the index is missing or empty."""
    name = retrieve.collection_name(settings)
    if client.collection_exists(name):
        count = client.count(name).count
        if count > 0:
            return count, False
    return retrieve.build_index(settings_path, client=client, model=model), True


def run(target: str, settings: dict, llm_call, client, embedding_model=None, progress=None, model_name: str = "",
        release_model: bool = True, generated: str = "") -> dict:
    """
    Scan `target`, match, explain, report. `progress(stage, done, total, message)` is called along the way
    (stages: scan, match, explain, report). Returns the usages, matcher results, findings, the report text,
    verdict counts and timings per stage.
    """
    library = settings["library"]
    folder = Path(target)
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {target}")
    notify = progress or (lambda stage, done, total, message: None)
    timings = {}

    started = time.perf_counter()
    notify("scan", 0, 1, "looking for uses of the library")
    chunks_path = Path("data/chunks") / f"{library}_chunks.json"
    known = None
    if chunks_path.exists():
        known = scan.known_names_from_chunks(json.loads(chunks_path.read_text(encoding="utf-8")), settings["function_pattern"])
    else:
        logger.warning("No chunk file at %s; scanning without the list of known names", chunks_path)
    usages = scan.scan(str(folder), settings, known)
    timings["scan"] = time.perf_counter() - started
    notify("scan", 1, 1, f"{len(usages)} usage(s) found")

    started = time.perf_counter()
    notify("match", 0, len(usages), "finding changelog notes")
    match_results = match.match_usages(usages, library, settings, client=client, model=embedding_model) if usages else []
    if release_model:
        retrieve.release_model()
    timings["match"] = time.perf_counter() - started
    by_status = Counter(r["status"] for r in match_results)
    notify("match", len(usages), len(usages), ", ".join(f"{n} {status}" for status, n in sorted(by_status.items())) or "nothing to match")

    started = time.perf_counter()
    findings = []
    for index, result in enumerate(match_results, start=1):
        finding = explain.explain_usage(result, llm_call, library)
        findings.append(finding)
        notify("explain", index, len(match_results), f"{finding['usage']['file']}:{finding['usage']['line']} -> {finding['verdict']}")
    timings["explain"] = time.perf_counter() - started

    started = time.perf_counter()
    notify("report", 0, 1, "building the report")
    versions = ", ".join(settings.get("migration_filters", {}).get("version", []))
    text = report.build_report(findings, library=library, target=str(target), versions=versions, model_name=model_name, generated=generated)
    timings["report"] = time.perf_counter() - started
    notify("report", 1, 1, "done")

    return {"library": library, "target": str(target), "usages": usages, "match_results": match_results, "findings": findings,
            "report": text, "counts": dict(Counter(f["verdict"] for f in findings)), "timings": timings, "model": model_name}


if __name__ == "__main__":
    import argparse
    import datetime
    import sys

    import llm
    from ingest import load_settings

    parser = argparse.ArgumentParser(description="Scan a folder and write a migration report")
    parser.add_argument("target", help="the folder to scan")
    parser.add_argument("--library", default="pandas")
    parser.add_argument("--store", default="embedded", choices=("embedded", "server"))
    parser.add_argument("--out", help="where to write the report (default: reports/<folder name>_report.md)")
    args = parser.parse_args()

    settings_path = f"configs/{args.library}/{args.library}_settings.json"
    settings = load_settings(settings_path)
    config = llm.load_llm_config()
    if settings is None or config is None:
        sys.exit(1)
    if not llm.is_server_up(config["base_url"]):
        print("The Ollama server is not running. In a second Terminal tab run: ollama serve")
        sys.exit(1)
    if config["model"] not in llm.list_models(config["base_url"]):
        print(f"The model {config['model']} is not downloaded. Run: ollama pull {config['model']}")
        sys.exit(1)

    if not Path(args.target).is_dir():
        print(f"Folder not found: {args.target}")
        sys.exit(1)
    out = args.out or f"reports/{Path(args.target).resolve().name}_report.md"
    store = open_store(args.store)
    try:
        started = time.perf_counter()
        count, built = ensure_index(store, settings, settings_path)
        print(f"index: {count} notes ({'built now' if built else 'already there'}, {time.perf_counter() - started:.1f}s)", flush=True)
        result = run(args.target, settings, llm.OllamaModel.from_config(config), store,
                     progress=lambda stage, done, total, message: print(f"  [{stage}] {done}/{total} {message}", flush=True),
                     model_name=config["model"], generated=datetime.date.today().isoformat())
    finally:
        store.close()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(result["report"], encoding="utf-8")
    print(f"report: {out}")
    print("verdicts:", result["counts"])
    print("seconds:", {stage: round(seconds, 1) for stage, seconds in result["timings"].items()})
