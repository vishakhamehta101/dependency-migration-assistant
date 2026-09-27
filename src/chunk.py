"""
Dependency Migration Assistant — Chunking Module

Splits cleaned/raw HTML changelog and migration-guide pages into small,
retrievable chunks. Each section of a page is inspected individually:
sections containing a bullet list are split one-chunk-per-bullet;
prose sections are split by paragraph, recursively re-split if still
too large. Library-specific patterns (issue refs, function names) are
loaded from that library's settings file — nothing library-specific
is hardcoded here.
"""

import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ingest import load_settings  # reuse rather than duplicate settings-loading logic

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MAX_CHUNK_WORDS = 150  # prose pieces larger than this get split further

# Keyword-containment mapping: if any keyword appears in a section's heading
# (case-insensitive), the section is labeled with that category.
CATEGORY_KEYWORDS = {
    "regression": "regression_fix",
    "bug": "bug_fix",
    "enhancement": "enhancement",
    "deprecat": "deprecation",
    "perform": "performance",
    "incompatib": "breaking_change",
    "removal": "breaking_change",
}


def map_category(heading_text: str) -> str:
    """Map a raw heading (e.g. 'Fixed regressions') to a normalized category."""
    lowered = heading_text.lower()
    for keyword, category in CATEGORY_KEYWORDS.items():
        if keyword in lowered:
            return category
    logger.warning("No category match for heading %r — using 'uncategorized'", heading_text)
    return "uncategorized"


def get_sections(soup: BeautifulSoup) -> list:
    """
    Split the page's <main> content into sections, one per real heading.

    Pandas' pages wrap the *entire* page content in one outer <section>
    (tied to the page's own <h1> title), with the real content sections
    ("Enhancements", "Bug fixes", etc.) nested exactly one level inside
    it, and their own subsections nested one level deeper still.

    So: skip the single outermost wrapper (it has no section ancestor
    of its own), keep sections nested exactly one level deep (their
    parent has no section ancestor), and skip anything nested deeper
    (their parent DOES have a section ancestor).
    """
    main_content = soup.find("main")
    if main_content is None:
        return []

    all_sections = main_content.find_all("section")
    top_level = []

    for section in all_sections:
        parent_section = section.find_parent("section")
        if parent_section is None:
            continue  # this IS the outer page-wide wrapper — not real content, skip it

        grandparent_section = parent_section.find_parent("section")
        if grandparent_section is None:
            top_level.append(section)  # nested exactly one level deep — a real content section

    return top_level


def section_has_list(section) -> bool:
    """Check whether a section contains a bullet/numbered list."""
    return section.find("li") is not None


def chunk_bullets(section) -> list:
    """One chunk per <li> — used for bullet-style sections."""
    return [li.get_text(separator=" ", strip=True) for li in section.find_all("li")]


def split_text_recursive(text: str, max_words: int = MAX_CHUNK_WORDS) -> list:
    """
    Split `text` into pieces no longer than `max_words`, by repeatedly
    halving at the nearest sentence boundary. Calls itself on each half
    until every piece is small enough (or can't be split further).
    """
    words = text.split()
    if len(words) <= max_words:
        return [text]

    midpoint = len(text) // 2
    split_at = text.find(". ", midpoint)  # look for a sentence boundary near the middle
    if split_at == -1:
        split_at = midpoint  # no clean sentence break found — split bluntly instead

    first_half = text[:split_at + 1].strip()
    second_half = text[split_at + 1:].strip()

    if not first_half or not second_half:
        return [text]  # couldn't actually split it — stop to avoid an infinite loop

    return split_text_recursive(first_half, max_words) + split_text_recursive(second_half, max_words)


def chunk_prose(section) -> list:
    """Split a non-bulleted section by paragraph, recursively re-splitting oversized ones."""
    paragraphs = [p.get_text(separator=" ", strip=True) for p in section.find_all("p")]
    paragraphs = [p for p in paragraphs if p]  # drop any empty ones

    pieces = []
    for paragraph in paragraphs:
        pieces.extend(split_text_recursive(paragraph))
    return pieces


def extract_details(text: str, settings: dict) -> dict:
    """Pull out an issue reference and a function name, using this library's patterns."""
    issue_matches = re.findall(settings["issue_pattern"], text)
    function_match = re.search(settings["function_pattern"], text)

    return {
        "issue_refs": issue_matches,
        "library_function": function_match.group(0) if function_match else None,
    }


def chunk_file(filename: str, source_type: str, source_url: str, settings: dict) -> list:
    """Run the full chunking process on one already-fetched HTML file."""
    path = Path(filename)
    raw_html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw_html, "html.parser")

    sections = get_sections(soup)
    if not sections:
        logger.warning("No sections found in %s", filename)
        return []

    chunks = []
    for index, section in enumerate(sections):
        heading_tag = section.find(["h2", "h3"])
        heading_text = heading_tag.get_text(separator=" ", strip=True).rstrip("#") if heading_tag else "Unknown"
        category = map_category(heading_text)

        if section_has_list(section):
            texts = chunk_bullets(section)
        else:
            texts = chunk_prose(section)

        for sub_index, text in enumerate(texts):
            details = extract_details(text, settings)
            chunks.append({
                "chunk_id": f"{settings['library']}-{source_type}-{index:02d}-{sub_index:02d}",
                "text": text,
                "source_type": source_type,
                "source_url": source_url,
                "library": settings["library"],
                "section": heading_text,
                "category": category,
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
    version_match = re.search(r"v(\d+\.\d+\.\d+)", filename)
    if version_match:
        return urljoin(settings["index_page_url"], f"v{version_match.group(1)}.html")

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
    for raw_file in Path("data").glob("raw_*.html"):
        source_type = "migration_guide" if "guide" in raw_file.name or "write" in raw_file.name else "changelog"
        source_url = guess_source_url(raw_file.name, settings)
        all_chunks.extend(chunk_file(str(raw_file), source_type, source_url, settings))

    save_chunks(all_chunks, f"data/chunks/{settings['library']}_chunks.json")


if __name__ == "__main__":
    run("configs/pandas/pandas_settings.json")