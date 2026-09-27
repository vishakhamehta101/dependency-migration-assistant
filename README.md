# Dependency Migration Assistant

A tool that reads a Python library's official changelog, scans a real codebase, and flags exactly which lines will break when upgrading that library — with an explanation and a citation back to the library's own documentation.

Built as part of a 4-person college project team. Currently focused on **Module 1** of a larger planned system (see [Project Vision](#project-vision) below).

## Current Status

| Stage | Status |
|---|---|
| Ingestion (fetch changelog/migration guide pages) | ✅ Working |
| Cleaning (strip HTML clutter) | ✅ Working |
| Chunking (split into searchable pieces) | ✅ Working |
| Retrieval (search chunks) | 🔜 Next |
| Codebase scanning | 🔜 Planned |
| Answer generation (with citations) | 🔜 Planned |
| Evaluation (against real GitHub PRs) | 🔜 Planned |

Currently ingests and chunks Pandas' 2.0.0–2.0.3 changelogs and Copy-on-Write migration guide, producing ~1,100 structured, searchable chunks.

## How it works

1. **Ingestion** (`src/ingest.py`) — automatically discovers and downloads a library's changelog and migration guide pages, using a per-library settings file (`configs/<library>/`) rather than hardcoded URLs, so adding a new library later doesn't require code changes.
2. **Cleaning** — strips navigation/breadcrumb clutter from raw HTML using BeautifulSoup, keeping only the real article content.
3. **Chunking** (`src/chunk.py`) — splits cleaned content into small, labelled pieces. Each section of a page is inspected individually: bulleted sections split one-chunk-per-bullet, prose sections split by paragraph with recursive re-splitting for oversized pieces. Each chunk is tagged with its source, category, referenced issue number, and function name.

## Setup

```bash
git clone https://github.com/vishakhamehta101/dependency-migration-assistant.git
cd dependency-migration-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python3 src/ingest.py   # fetch and clean Pandas changelog/guide pages
python3 src/chunk.py     # chunk the cleaned content
```

Output lands in `data/` (raw and cleaned pages) and `data/chunks/` (final structured chunks, as JSON).

## Project Vision

This module is part of a larger planned system: one shared core engine (chunking, retrieval, reranking, citation) reused across three problem domains — dependency migration (this module), incident response, and cross-repo impact analysis — with a shared trust/verification layer on top. Only Module 1 is being built for now; the others are intentionally deferred until this one works end-to-end.

## Tech Stack

- Python 3.12
- `requests` — fetching web pages
- `beautifulsoup4` — parsing/cleaning HTML
- Planned: Qdrant/pgvector (retrieval), an LLM API (answer generation)

## Team

Vishakha Mehta (project lead), Soumya Mishra, Vidit Tanay, Aryan Khurana