"""
Dependency Migration Assistant — saved matcher results

The matcher needs Qdrant (and so Docker) to find notes; the AI stage does not. Saving the matcher's
output to a file lets the AI stage run on its own — with Docker closed to free memory — and gives
every run the identical input.

  python3 src/snapshot.py [folder-to-scan] [output-file]
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TARGET = "samples/pandas1_sample"
DEFAULT_OUTPUT = "data/sample_matches.json"


def save_matches(results: list, path: str, library: str, target: str) -> None:
    """Write matcher results (from match.match_usages) to a JSON file."""
    payload = {"library": library, "target": target, "count": len(results), "results": results}
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Saved %d matched usage(s) -> %s", len(results), path)


def load_matches(path: str = DEFAULT_OUTPUT) -> list | None:
    """Read matcher results back. Returns None, after logging why, if the file cannot be used."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))["results"]
    except FileNotFoundError:
        logger.error("Saved matches not found: %s (create them with: python3 src/snapshot.py)", path)
    except (json.JSONDecodeError, KeyError) as error:
        logger.error("Saved matches in %s are unreadable: %s", path, error)
    return None


if __name__ == "__main__":
    import sys
    from collections import Counter

    import match
    import scan
    from ingest import load_settings

    folder = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET
    output = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT
    settings = load_settings("configs/pandas/pandas_settings.json")
    if settings is not None:
        chunks_path = Path("data/chunks") / f"{settings['library']}_chunks.json"
        known = None
        if chunks_path.exists():
            known = scan.known_names_from_chunks(json.loads(chunks_path.read_text(encoding="utf-8")), settings["function_pattern"])
        results = match.match_usages(scan.scan(folder, settings, known), settings["library"], settings)
        save_matches(results, output, settings["library"], folder)
        print("by status:", dict(Counter(r["status"] for r in results)))
