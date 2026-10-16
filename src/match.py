"""
Dependency Migration Assistant — Matcher

Turns each usage found by the scanner into a search query, retrieves the documented
changes that might affect it, and decides which of them matter.

For every candidate note the matcher asks: does it mention the function at all? If not,
it is dropped. If so it gets one kind of evidence:
  removed        the note says the function itself was removed
  keyword_match  the note names the function AND an argument this call passes
  name_match     the note names the function, but nothing ties it to this call
Each usage then gets a status:
  documented_change     at least one "removed" or "keyword_match" note
  review                only "name_match" notes (the final judgement belongs to System 6)
  no_documented_change  nothing survived

Which chunks count as migration-relevant (categories, versions) comes from the
library's settings file ("migration_filters"), so nothing library-specific is hardcoded here.
"""

import logging
import re
from collections import Counter

from qdrant_client import QdrantClient

import retrieve

logger = logging.getLogger(__name__)

MATCH_POOL = 50  # candidates examined per usage
MAX_MATCHES = 5  # matches kept per usage
EVIDENCE_ORDER = {"removed": 0, "keyword_match": 1, "name_match": 2}
STRONG_EVIDENCE = {"removed", "keyword_match"}


def function_name(usage: dict) -> str:
    """The name a changelog would use: 'api.types.is_scalar' -> 'is_scalar'."""
    return usage.get("function", "").split(".")[-1]


def build_query(usage: dict) -> str:
    """
    Search text for one usage: the function name plus the keyword arguments the call passes.
    The code line itself is left out on purpose: names like `df` and `extra` are noise for search.
    """
    parts = [function_name(usage)] + list(usage.get("keywords", []))
    query_parts = []
    for part in parts:
        if part and part not in query_parts:
            query_parts.append(part)
    return " ".join(query_parts)


def migration_filters(settings: dict) -> dict | None:
    """Which chunks count as migration-relevant for this library, from its settings file."""
    return settings.get("migration_filters") or None


def get_candidates(usage: dict, library: str, settings: dict, pool: int = MATCH_POOL, client=None, model=None) -> list:
    """Documented changes that might affect one usage, best first (hybrid search, migration filters applied)."""
    query = build_query(usage)
    if not query:
        return []
    return retrieve.search(query, library, top_k=pool, filters=migration_filters(settings),
                           candidate_pool=pool, client=client, model=model)


# ---- evidence -------------------------------------------------------------

def mentions(text: str, word: str) -> bool:
    """True if `word` appears in the text as a whole word ('save' does not match 'autosave')."""
    return bool(word) and re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) is not None


def names_the_function_itself(text: str, name: str) -> bool:
    """True if `name` is the LAST part of a dotted name: ExcelWriter.save names 'save', not 'ExcelWriter'."""
    return bool(name) and re.search(r"(?<![\w.])(?:\w+\.)*" + re.escape(name) + r"(?![\w.])", text) is not None


def is_whole_removal(text: str, name: str) -> bool:
    """
    True if the note says the function itself was removed: it starts with Removed/Remove/Removal
    and names the function before any ' from / in / of / with ' (which would make it argument-level,
    like 'Removed arguments squeeze from read_csv()').
    """
    if not re.match(r"(Removed|Remove|Removal)\b", text):
        return False
    cut = re.search(r"\s(from|in|of|with)\s", text)
    head = text[:cut.start()] if cut else text
    return names_the_function_itself(head, name)


def classify(text: str, name: str, keywords: list) -> tuple:
    """(evidence, matched keyword arguments) for one note, or (None, []) if it does not mention the function."""
    if not mentions(text, name):
        return None, []
    matched = [keyword for keyword in keywords if mentions(text, keyword)]
    if is_whole_removal(text, name):
        return "removed", matched
    if matched:
        return "keyword_match", matched
    return "name_match", []


# ---- matching -------------------------------------------------------------

def match_usage(usage: dict, candidates: list) -> dict:
    """
    Decide which candidate notes matter for one usage.
    Returns {"usage", "status", "confidence", "matches"}; each match carries its evidence,
    the call's keyword arguments it mentions, its search scores, and the chunk itself.
    """
    name = function_name(usage)
    keywords = list(usage.get("keywords", []))

    matches = []
    for candidate in candidates:
        evidence, matched = classify(candidate["chunk"]["text"], name, keywords)
        if evidence is None:
            continue
        matches.append({
            "evidence": evidence,
            "matched_keywords": matched,
            "score": candidate["score"],
            "hybrid_rank": candidate["rank"],
            "semantic_rank": candidate["semantic_rank"],
            "keyword_rank": candidate["keyword_rank"],
            "chunk": candidate["chunk"],
        })

    matches.sort(key=lambda m: (EVIDENCE_ORDER[m["evidence"]], m["hybrid_rank"]))
    matches = matches[:MAX_MATCHES]
    for position, match in enumerate(matches, start=1):
        match["rank"] = position

    if any(m["evidence"] in STRONG_EVIDENCE for m in matches):
        status = "documented_change"
    elif matches:
        status = "review"
    else:
        status = "no_documented_change"

    return {
        "usage": usage,
        "status": status,
        "confidence": "high" if usage.get("resolved", True) else "low",
        "matches": matches,
    }


def match_usages(usages: list, library: str, settings: dict, pool: int = MATCH_POOL, client=None, model=None) -> list:
    """Run the matcher over every usage the scanner found. Usages with the same query share one search."""
    client = client or QdrantClient(host=retrieve.QDRANT_HOST, port=retrieve.QDRANT_PORT)
    candidates_by_query = {}
    results = []
    for usage in usages:
        query = build_query(usage)
        if query not in candidates_by_query:
            candidates_by_query[query] = get_candidates(usage, library, settings, pool=pool, client=client, model=model)
        results.append(match_usage(usage, candidates_by_query[query]))

    counts = Counter(result["status"] for result in results)
    logger.info("Matched %d usage(s): %d documented_change, %d review, %d no_documented_change",
                len(results), counts["documented_change"], counts["review"], counts["no_documented_change"])
    return results


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    import scan
    from ingest import load_settings

    target = sys.argv[1] if len(sys.argv) > 1 else "samples/pandas1_sample"
    library_settings = load_settings("configs/pandas/pandas_settings.json")
    if library_settings is not None:
        library_name = library_settings["library"]
        chunks_file = Path("data/chunks") / f"{library_name}_chunks.json"
        known = None
        if chunks_file.exists():
            known = scan.known_names_from_chunks(json.loads(chunks_file.read_text(encoding="utf-8")),
                                                 library_settings["function_pattern"])
        found = scan.scan(target, library_settings, known)
        for result in match_usages(found, library_name, library_settings):
            usage, top = result["usage"], (result["matches"][0] if result["matches"] else None)
            print(f"{usage['file']}:{usage['line']:<4} {result['status']:<21} {result['confidence']:<5} "
                  f"{usage['function']:<30} {len(result['matches'])} match(es)")
            if top and result["status"] == "documented_change":
                print(f"      -> [{top['evidence']}] {top['chunk']['text'][:100]}")
