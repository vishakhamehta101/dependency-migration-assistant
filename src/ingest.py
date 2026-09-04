"""
Dependency Migration Assistant — Ingestion Module

Fetches and cleans changelog / migration-guide pages for a target library.
Every library-specific detail (URLs, version patterns) lives in that
library's settings file (e.g. configs/pandas/pandas_settings.json) —
nothing library-specific is hardcoded here.
"""

import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10
DATA_DIR = Path("data")


def fetch(url: str, filename: str) -> bool:
    """
    Download the content at `url` and save it to `filename`.
    Returns True on success, False on any failure — never raises,
    so callers can decide how to react instead of the program crashing.
    """
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()  # treats 404/500/etc. as errors too, not just network failures
    except requests.exceptions.RequestException as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return False

    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)  # create data/ if it doesn't exist yet
    path.write_text(response.text, encoding="utf-8")
    logger.info("Downloaded %s -> %s", url, filename)
    return True


def clean(filename: str) -> str | None:
    """
    Strip navigation/breadcrumb clutter from a saved HTML file and save
    the plain article text to a sibling clean_*.txt file.
    Returns the cleaned text, or None if the file couldn't be parsed.
    """
    path = Path(filename)
    raw_html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(raw_html, "html.parser")

    breadcrumb = soup.find("div", class_="bd-header-article")
    if breadcrumb:
        breadcrumb.decompose()

    main_content = soup.find("main")
    if main_content is None:
        logger.warning("No <main> tag found in %s — skipping", filename)
        return None

    clean_text = main_content.get_text(separator=" ", strip=True)

    clean_path = Path(str(path).replace("raw_", "clean_")).with_suffix(".txt")
    clean_path.write_text(clean_text, encoding="utf-8")
    logger.info("Cleaned %s -> %s", filename, clean_path)
    return clean_text


def discover_changelog_urls(settings: dict) -> dict:
    """
    Scan a library's changelog index page and return every changelog URL
    matching the library's configured version pattern, keyed by version.
    """
    discovered = {}
    index_url = settings["index_page_url"]

    try:
        response = requests.get(index_url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning("Failed to reach index page %s: %s", index_url, e)
        return discovered  # empty dict — caller can check for this

    soup = BeautifulSoup(response.text, "html.parser")
    version_pattern = settings["target_version"]

    for link in soup.find_all("a"):
        href = link.get("href")
        if not href or "#" in href:
            continue  # skip missing hrefs and anchor-jump duplicates early
        if not re.search(version_pattern, href):
            continue  # not a version link we care about

        version_match = re.search(r"v(\d+\.\d+\.\d+)", href)
        if not version_match:
            continue
        version = version_match.group(1)

        discovered[f"changelog_v{version}"] = {
            "url": urljoin(index_url, href),
            "filename": str(DATA_DIR / f"raw_changelog_v{version}.html"),
        }

    return discovered


def load_settings(settings_path: str) -> dict | None:
    """Load a library's settings file. Returns None (not an exception) on failure."""
    try:
        with open(settings_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        logger.error("Settings file not found: %s", settings_path)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in %s: %s", settings_path, e)
    return None


def build_sources(settings: dict) -> dict:
    """Combine discovered changelog URLs with the library's migration guide URLs."""
    sources = discover_changelog_urls(settings)

    for guide_url in settings.get("migration_guide_urls", []):
        guide_filename = guide_url.rstrip("/").split("/")[-1]
        key = f"migration_guide_{guide_filename.removesuffix('.html')}"
        sources[key] = {
            "url": guide_url,
            "filename": str(DATA_DIR / f"raw_{guide_filename}"),
        }

    return sources


def ingest(settings_path: str) -> None:
    """Run the full ingestion pipeline (fetch + clean) for one library."""
    settings = load_settings(settings_path)
    if settings is None:
        return

    sources = build_sources(settings)
    if not sources:
        logger.warning("No sources discovered for %s — nothing to fetch", settings_path)
        return

    for source in sources.values():
        fetch(source["url"], source["filename"])

    for source in sources.values():
        clean(source["filename"])


if __name__ == "__main__":
    ingest("configs/pandas/pandas_settings.json")