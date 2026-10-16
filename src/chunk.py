"""
Dependency Migration Assistant — Chunking Module

Splits raw HTML changelog and migration-guide pages into small, retrievable
chunks. The page is cut into units (one per <section>: a main section such as
"Bug fixes", or a subsection nested inside it). Inside each unit, every
bullet, paragraph, code example and table is captured:

  - a code example stays attached to the sentence that introduces it
  - tiny list items (bare function names) are merged into the sentence that
    introduces them, so they keep their meaning
  - anything still longer than MAX_CHUNK_WORDS is split recursively

Library-specific patterns (issue refs, function names, sections to skip) are
loaded from that library's settings file — nothing library-specific is
hardcoded here.
"""

import copy
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ingest import load_settings  # reuse rather than duplicate settings-loading logic

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MAX_CHUNK_WORDS = 150   # pieces longer than this get split further
SHORT_ITEM_WORDS = 3    # list items this short are merged into their introducing sentence

HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
BLOCK_TAGS = ["p", "li", "pre", "table"]

# Keyword-containment mapping: if any keyword appears in a section's heading
# (case-insensitive), the section is labeled with that category.
CATEGORY_KEYWORDS = {
    # ORDER MATTERS: the first match wins, so list the most specific keywords first.
    # e.g. "Removal of prior version deprecations" must hit "removal" before "deprecat".
    "removal": "breaking_change",
    "incompatib": "breaking_change",
    "regression": "regression_fix",
    "bug": "bug_fix",
    "enhancement": "enhancement",
    "deprecat": "deprecation",
    "perform": "performance",
}


def map_category(heading_text: str) -> str:
    """Map a raw heading (e.g. 'Fixed regressions') to a normalized category."""
    lowered = heading_text.lower()
    for keyword, category in CATEGORY_KEYWORDS.items():
        if keyword in lowered:
            return category
    logger.warning("No category match for heading %r — using 'uncategorized'", heading_text)
    return "uncategorized"


def section_heading(section) -> str:
    """The visible title of a <section>, without the trailing '#' permalink symbol."""
    tag = section.find(HEADING_TAGS)
    return tag.get_text(separator=" ", strip=True).rstrip("# ") if tag else "Unknown"


def get_units(soup: BeautifulSoup, source_type: str) -> list:
    """
    Split a page into units, one per <section>.

    Pandas pages wrap everything in one outer <section> (the page title), with
    main sections (h2) one level inside it and subsections (h3) one level deeper.
    Each unit records its main section and, if nested, its subsection.
    """
    main = soup.find("main")
    if main is None:
        return []

    units = []
    for section in main.find_all("section"):
        ancestors = section.find_parents("section")  # nearest first; the last is the outer wrapper
        if not ancestors:
            if source_type == "changelog":
                continue  # changelog intro is boilerplate ("These are the changes in ...")
            top, sub = section, None  # a guide's intro paragraphs are real content
        elif len(ancestors) == 1:
            top, sub = section, None
        else:
            top, sub = ancestors[-2], section

        units.append({
            "element": section,
            "section": section_heading(top),
            "subsection": section_heading(sub) if sub is not None else None,
        })
    return units


def own_blocks(section) -> list:
    """Content tags that belong directly to this section, not to a nested subsection."""
    blocks = []
    for el in section.find_all(BLOCK_TAGS):
        if el.find_parent("section") is not section:
            continue  # belongs to a nested subsection, which is its own unit
        if el.name in ("p", "pre") and el.find_parent(["li", "table"]):
            continue  # already part of its list item or table
        if "admonition-title" in el.get("class", []):
            continue  # the bare "Note" / "Warning" label carries no information
        blocks.append(el)
    return blocks


def block_text(el) -> str:
    """Readable text for one block. Code keeps its line breaks; tables become rows."""
    if el.name == "pre":
        return el.get_text().strip("\n")
    if el.name == "table":
        rows = [
            " | ".join(cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"]))
            for tr in el.find_all("tr")
        ]
        return "\n".join(row for row in rows if row.strip(" |"))
    if el.name == "li" and el.find(["ul", "ol"]):
        el = copy.copy(el)
        for nested in el.find_all(["ul", "ol"]):
            nested.decompose()  # nested items become their own blocks
    return el.get_text(separator=" ", strip=True)


def build_entries(section) -> list:
    """Turn a section's blocks into entries, attaching code and merging tiny list items."""
    entries = []  # each: {"text": str, "kind": "p" | "li" | "table" | "pre" | "merged"}
    for el in own_blocks(section):
        text = block_text(el)
        if not text:
            continue

        if el.name == "pre":
            if entries:
                entries[-1]["text"] += "\n" + text  # keep an example with the sentence before it
            else:
                entries.append({"text": text, "kind": "pre"})
        elif (el.name == "li" and not el.find(["ul", "ol"])
              and len(text.split()) <= SHORT_ITEM_WORDS
              and entries and entries[-1]["kind"] in ("p", "merged")):
            if entries[-1]["kind"] == "merged":
                joiner = ", "
            elif entries[-1]["text"].rstrip().endswith(":"):
                joiner = " "  # the sentence already ends with a colon
            else:
                joiner = ": "
            entries[-1]["text"] += joiner + text  # "The following functions gained X: read_csv(), ..."
            entries[-1]["kind"] = "merged"
        else:
            entries.append({"text": text, "kind": el.name})
    return [entry["text"] for entry in entries]


def find_boundary(text: str, start: int) -> int:
    """Nearest sentence end or line break at or after `start`, or -1 if there is none."""
    found = [i for i in (text.find(". ", start), text.find("\n", start)) if i != -1]
    return min(found) if found else -1


def split_text_recursive(text: str, max_words: int = MAX_CHUNK_WORDS) -> list:
    """
    Split `text` into pieces no longer than `max_words`, by repeatedly halving at
    the nearest sentence end or line break. Calls itself on each half until every
    piece is small enough (or can't be split further).
    """
    if len(text.split()) <= max_words:
        return [text]

    midpoint = len(text) // 2
    split_at = find_boundary(text, midpoint)
    if split_at == -1:
        split_at = midpoint  # no clean break found — split bluntly instead

    first_half = text[:split_at + 1].strip()
    second_half = text[split_at + 1:].strip()

    if not first_half or not second_half:
        return [text]  # couldn't actually split it — stop to avoid an infinite loop

    return split_text_recursive(first_half, max_words) + split_text_recursive(second_half, max_words)


def extract_details(text: str, settings: dict) -> dict:
    """Pull out issue references and a function name, using this library's patterns."""
    issue_matches = re.findall(settings["issue_pattern"], text)
    function_match = re.search(settings["function_pattern"], text)

    return {
        "issue_refs": issue_matches,
        "library_function": function_match.group(0) if function_match else None,
    }


def extract_version(filename: str) -> str | None:
    """Pull a version like '2.0.1' out of a filename, or None for non-versioned files."""
    match = re.search(r"v(\d+\.\d+\.\d+)", filename)
    return match.group(1) if match else None


def chunk_file(filename: str, source_type: str, source_url: str, settings: dict) -> list:
    """Run the full chunking process on one already-fetched HTML file."""
    path = Path(filename)
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")

    units = get_units(soup, source_type)
    if not units:
        logger.warning("No sections found in %s", filename)
        return []

    skip = {name.lower() for name in settings.get("skip_sections", [])}
    version = extract_version(filename)
    file_id = path.stem.removeprefix("raw_")  # e.g. changelog_v2.0.1
    categories = {}  # heading -> category, so each heading is mapped (and warned about) once

    chunks = []
    for unit_index, unit in enumerate(units):
        section = unit["section"]
        if section.lower() in skip:
            continue  # config-driven: skip sections like Contributors

        if section not in categories:
            categories[section] = map_category(section)

        pieces = []
        for entry in build_entries(unit["element"]):
            pieces.extend(split_text_recursive(entry))

        for sub_index, text in enumerate(pieces):
            details = extract_details(text, settings)
            chunks.append({
                "chunk_id": f"{settings['library']}-{file_id}-{unit_index:02d}-{sub_index:02d}",
                "text": text,
                "source_type": source_type,
                "source_url": source_url,
                "library": settings["library"],
                "version": version,
                "section": section,
                "subsection": unit["subsection"],
                "category": categories[section],
                "issue_refs": details["issue_refs"],
                "library_function": details["library_function"],
            })

    return chunks


def save_chunks(chunks: list, output_path: str) -> None:
    """Save a list of chunk dictionaries as JSON."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chunks, indent=2), encoding="utf-8")
    logger.info("Saved %d chunks -> %s", len(chunks), output_path)


def guess_source_url(filename: str, settings: dict) -> str | None:
    """Reconstruct the original URL a raw file was fetched from, using its filename."""
    version = extract_version(filename)
    if version:
        return urljoin(settings["index_page_url"], f"v{version}.html")

    for guide_url in settings.get("migration_guide_urls", []):
        guide_filename = guide_url.rstrip("/").split("/")[-1]
        if guide_filename.replace(".html", "") in filename:
            return guide_url

    return None


def run(settings_path: str) -> None:
    """Chunk every raw file belonging to one library, and save the combined result."""
    settings = load_settings(settings_path)
    if settings is None:
        return

    all_chunks = []
    for raw_file in sorted(Path("data").glob("raw_*.html")):
        source_type = "migration_guide" if "guide" in raw_file.name or "write" in raw_file.name else "changelog"
        source_url = guess_source_url(raw_file.name, settings)
        all_chunks.extend(chunk_file(str(raw_file), source_type, source_url, settings))

    save_chunks(all_chunks, f"data/chunks/{settings['library']}_chunks.json")


if __name__ == "__main__":
    run("configs/pandas/pandas_settings.json")
