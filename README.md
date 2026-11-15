# Dependency Migration Assistant

A tool that reads a Python library's official changelog and migration guide, scans a real codebase, and flags the lines that will break when you upgrade that library. Every finding comes with an explanation and a quote from the library's own documentation, checked word for word.

Built as a 4-person college project. It currently targets **pandas 1.x → 2.0** and is **Module 1** of a larger planned system (see [Project Vision](#project-vision)).

## Current Status

| Stage | File | Status |
|---|---|---|
| Ingestion (fetch and clean changelog and guide pages) | `src/ingest.py` | Working |
| Chunking (split into searchable, labelled pieces) | `src/chunk.py` | Working: 832 chunks |
| Retrieval (hybrid keyword + meaning search) | `src/retrieve.py` | Working |
| Codebase scanner | `src/scan.py` | Working |
| Matcher (evidence tiers and statuses) | `src/match.py` | Working. A reranker was tested and not adopted (gap 25) |
| Answer generation (local AI model, verified citations) | `src/explain.py`, `src/llm.py` | Working |
| Markdown report | `src/report.py` | Working |
| Pipeline and local web app | `src/pipeline.py`, `src/app.py` | Working |
| Note-type classifier (our own trained model) | `src/classify.py` | Working: 91.7% cross-validated accuracy |
| Evaluation against real GitHub migration PRs | none yet | **Not built yet** (planned) |

## How it works

**Part 1: build the knowledge base (done once per library)**

1. **Ingestion** (`src/ingest.py`) downloads the library's changelog and migration guide pages and strips navigation clutter with BeautifulSoup. URLs and version patterns come from the library's settings file (`configs/<library>/`), not from the code.
2. **Chunking** (`src/chunk.py`) cuts each page along its real HTML structure (sections, bullets, paragraphs, code examples). Each chunk records its source, version, section, category, issue references and the library function it names. Today that is the pandas 2.0.0 to 2.0.3 changelogs and the Copy-on-Write guide: 832 chunks (781 changelog, 51 guide).
3. **Embedding and indexing** (`src/retrieve.py`) turns each chunk into meaning-numbers with the BGE-small model and stores them in a Qdrant index. A BM25 keyword index covers exact words. By default Qdrant runs embedded (a folder on disk), so Docker is not needed.

**Part 2: check a codebase**

4. **Scanner** (`src/scan.py`) reads the code with Python's `ast` module and lists every place the library is used: the function name, the line and the keyword arguments. It decides something is the library's by following imports and tracking variables assigned from library calls.
5. **Matcher** (`src/match.py`) turns each usage into a query (function name plus argument names). It searches the chunks with keyword and meaning search combined by Reciprocal Rank Fusion, keeps only breaking-change and deprecation notes, and assigns evidence: `removed`, `keyword_match` or `name_match`.
6. **Answer generator** (`src/explain.py`) gives a small local model (Ollama, `qwen2.5:3b`) the code line and the numbered notes, and asks whether this call is affected. The model never has the last word: its answer must be valid JSON, every citation must name a real note, every quote must appear word for word in that note, and a suggested fix is kept only if it is grounded in the notes or the code. If a check fails the finding becomes "unsure". Only usages backed by hard evidence reach the model.
7. **Report** (`src/report.py`) writes a Markdown report: a summary, a table of confirmed breaks, and details by file with the quote and a source link.

### The five verdicts

| Verdict | Meaning |
|---|---|
| Affected | A documented change matches this exact call, and the quote was checked word for word |
| Needs review | The changelog mentions this function, but no note could be tied to this exact call. Check by hand |
| Unsure | Could not be confirmed (the object may not be from the library, or the AI's answer could not be verified) |
| Not affected | The notes concern something this call does not use |
| No documented change | Nothing in the ingested changelog mentions it |

## Our own trained model: note-type classifier

`src/classify.py` trains a TF-IDF + Logistic Regression classifier that predicts a changelog note's category (breaking change, deprecation, bug fix, enhancement, performance, regression fix) from the note's own text. It never sees the section heading, because the training labels come from the heading.

- Trained on 775 labelled pandas changelog notes. **Cross-validated accuracy: 91.7%.** The weakest class is deprecation (28 examples).
- It can label notes whose heading gave no category, and a confidence score marks uncertain ones "needs review". At confidence 0.50 or above, the cross-validated predictions were right 99.6% of the time.
- Limits: trained on pandas only and not tested on another library. It reads wording, not meaning, so "Removed deprecated X" notes are sometimes mistaken for deprecations. It is not yet connected to the pipeline.

```bash
python3 src/classify.py train
python3 src/classify.py predict "DataFrame.append has been removed, use concat instead"
```

## Setup

Requires Python 3.12 and [Ollama](https://ollama.com).

```bash
git clone https://github.com/vishakhamehta101/dependency-migration-assistant.git
cd dependency-migration-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
ollama pull qwen2.5:3b
```

## Usage

Rebuild the knowledge base (optional: the chunk file is already in `data/chunks/`):

```bash
python3 src/ingest.py   # fetch and clean the pandas changelog and guide pages
python3 src/chunk.py    # chunk them into data/chunks/pandas_chunks.json
```

Check a codebase from the web app (open it in your browser at `http://127.0.0.1:8000`):

```bash
python3 src/app.py --online --open
```

Use `--online` the first time so the embedding model can download. The search index is built automatically on the first run. You can give the app a folder, a zip file or pasted code.

Or from the command line, with the Ollama server running (`ollama serve` in a second Terminal tab):

```bash
python3 src/pipeline.py samples/pandas1_sample
```

The report is written to `reports/`. A sample is in `reports/sample_report.md`: on the hand-built sample project, all 7 planted breaks are reported as affected.

## Tests

Each file in `tests/` checks one component and prints PASS or FAIL for each check:

```bash
python3 tests/verify_scan.py
python3 tests/verify_match.py
python3 tests/verify_classify.py
```

`tests/gates_ai.py` runs the pass/fail gates for the AI stage against the saved matcher results and needs the Ollama server running.

## Known limitations

The full list, with how production would fix each item, is in [`docs/production_gap_log.md`](docs/production_gap_log.md). The main ones:

- **Not yet evaluated on real code.** The sample project is hand-built. Scoring against real GitHub migration PRs (the diff as the answer key) is planned.
- **Matching is by function name.** A behaviour change that does not name the function is missed or only listed for review.
- **The scanner uses a heuristic** to decide which variables are library objects. Attributes such as `self.df` and tuple unpacking are not tracked.
- **The citation check proves a quote exists in a note, not that the note applies to the code.** A small 3B model sometimes cites a real quote for the wrong code, so only usages with hard evidence reach the model.
- **One small local model** on a laptop: roughly 3 to 5 seconds per call.

## Project Vision

This module is part of a larger planned system: one shared core engine (chunking, retrieval, citation) reused across three problem domains: dependency migration (this module), incident response, and cross-repo impact analysis, with a shared trust and verification layer on top. Only Module 1 is built; the others are intentionally deferred until this one works end to end and is evaluated.

## Tech Stack

- Python 3.12
- `requests`, `beautifulsoup4`: fetching and cleaning pages
- `sentence-transformers` (BGE-small), Qdrant (embedded), `rank-bm25`: hybrid retrieval
- Ollama with `qwen2.5:3b`: local answer generation
- `scikit-learn`: the note-type classifier
- `FastAPI`, `uvicorn`, `pydantic`: web app and data shapes

## Team

Vishakha Mehta (project lead), Soumya Mishra, Vidit Tanay, Aryan Khurana
