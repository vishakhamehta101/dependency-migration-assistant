"""
Dependency Migration Assistant — Markdown report (D17)

Turns findings (from explain.explain_usages) into a report a developer can act on:
  - a summary, and a quick table of the confirmed breaks
  - details grouped by file: the code line, the verdict, the explanation, the fix the changelog suggests,
    and the evidence (the cited quote with its version and source link; or, for usages that need a human,
    the notes that matched)
  - the usages with no documented change
  - how to read the report, including what it cannot tell you

  python3 src/report.py [--matches FILE] [--findings FILE] [--out FILE]
"""

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

NOTE_CHARS = 240
VERDICTS = ("affected", "needs_review", "unsure", "not_affected", "no_documented_change")
LABEL = {"affected": "AFFECTED", "needs_review": "NEEDS REVIEW", "unsure": "UNSURE",
         "not_affected": "NOT AFFECTED", "no_documented_change": "NO DOCUMENTED CHANGE"}
MEANING = {
    "affected": "A documented change matches this exact call, and the quote was checked word for word",
    "needs_review": "The changelog mentions this function, but no note could be tied to this exact call: check by hand",
    "unsure": "Could not be confirmed (the object may not be from the library, or the AI's answer could not be verified)",
    "not_affected": "The notes do not apply to this call",
    "no_documented_change": "Nothing in the ingested changelog mentions it",
}


def cell(text: str) -> str:
    """Make text safe inside a Markdown table cell."""
    return re.sub(r"\s+", " ", str(text)).replace("|", "\\|").strip()


def one_line(text: str, limit: int = NOTE_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def fence(code: str, language: str = "") -> str:
    """A fenced code block whose fence is longer than any run of backticks inside it."""
    longest = max((len(run) for run in re.findall(r"`+", code)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{language}\n{code.rstrip()}\n{ticks}"


def source_text(chunk: dict, refs: dict) -> str:
    """'pandas 2.0.0, *Section* ([source][1])' — the URL itself is listed once, at the end, under its number."""
    where = chunk.get("section", "")
    if chunk.get("subsection"):
        where += f" > {chunk['subsection']}"
    label = f"{chunk.get('library', '')} {chunk.get('version') or ''}".strip()
    link = ""
    if chunk.get("source_url"):
        link = f" ([source][{refs.setdefault(chunk['source_url'], len(refs) + 1)}])"
    return f"{label}, *{one_line(where, 90)}*{link}"


def location(finding: dict) -> str:
    usage = finding["usage"]
    return f"{usage['file']}:{usage['line']}"


def sort_key(finding: dict) -> tuple:
    usage = finding["usage"]
    return usage["file"], usage["line"], usage.get("function", "")


def cited_chunks(finding: dict) -> list:
    return [(c, finding["matches"][c["note"] - 1]["chunk"]) for c in finding["citations"]]


def finding_section(finding: dict, library: str, refs: dict) -> list:
    usage, verdict = finding["usage"], finding["verdict"]
    explanation = finding["explanation"]
    if "capped_unconfirmed_type" in finding.get("flags", []):
        explanation = (f"The changelog notes below would apply if this is a {library} object. The scanner could not confirm that it is one "
                       "(it may be, for example, a plain list), so this is shown as unsure.")
    lines = [f"#### {LABEL[verdict]} · line {usage['line']} · `{usage['function']}`", "", fence(usage.get("snippet", ""), "python"), "",
             explanation, ""]
    if finding.get("suggested_fix"):
        lines += [f"**Fix suggested by the changelog:** {finding['suggested_fix']}", ""]
    if verdict == "affected":
        lines.append("**Evidence**")
        for citation, chunk in cited_chunks(finding):
            lines += ["", f"> {one_line(citation['quote'], 400)}", f"> — {source_text(chunk, refs)}"]
        lines.append("")
    elif verdict in ("needs_review", "unsure") and finding["matches"]:
        lines.append("**Matched notes** (read these to decide)")
        lines.append("")
        for match in finding["matches"]:
            lines.append(f"- {one_line(match['chunk']['text'])} — {source_text(match['chunk'], refs)}")
        lines.append("")
    return lines


def build_report(findings: list, library: str, target: str, versions: str = "", model_name: str = "", generated: str = "") -> str:
    """The whole report as Markdown. The same input always gives the same text."""
    counts = Counter(f["verdict"] for f in findings)
    refs = {}
    title = f"# Migration report: {library}" + (f" → {versions}" if versions else "")
    meta = f"Scanned: `{target}` · {len(findings)} usage(s) of {library} found"
    if generated:
        meta += f" · generated {generated}"
    out = [title, "", meta, ""]

    if not findings:
        out += ["No usages of the library were found, so there is nothing to report.", ""]
    else:
        out += ["## Summary", "", "| Result | Usages | What it means |", "|---|---:|---|"]
        for verdict in VERDICTS:
            if counts[verdict]:
                out.append(f"| **{LABEL[verdict]}** | {counts[verdict]} | {MEANING[verdict]} |")
        out += [f"| **Total** | {len(findings)} | |", ""]

    affected = sorted((f for f in findings if f["verdict"] == "affected"), key=sort_key)
    if affected:
        out += ["## Confirmed breaks", "", "| Location | Code | What changed (from the changelog) | Fix suggested by the changelog |", "|---|---|---|---|"]
        for f in affected:
            note = cited_chunks(f)[0][1]["text"] if f["citations"] else ""
            out.append(f"| `{f['usage']['file']}:{f['usage']['line']}` | `{cell(f['usage'].get('snippet', ''))}` | {cell(one_line(note, 140))} | {cell(f.get('suggested_fix') or 'see the note')} |")
        out.append("")

    detailed = [f for f in findings if f["verdict"] != "no_documented_change"]
    if detailed:
        out += ["## Details by file", ""]
        by_file = defaultdict(list)
        for f in detailed:
            by_file[f["usage"]["file"]].append(f)
        for file in sorted(by_file):
            out += [f"### {file}", ""]
            for f in sorted(by_file[file], key=sort_key):
                out += finding_section(f, library, refs)

    quiet = sorted((f for f in findings if f["verdict"] == "no_documented_change"), key=sort_key)
    if quiet:
        out += ["## No documented change", "", "The changelog does not mention these. That is not a guarantee they are safe (see below).", ""]
        out += [f"- `{f['usage']['file']}:{f['usage']['line']}` `{cell(f['usage'].get('snippet', ''))}`" for f in quiet]
        out.append("")

    model = f" ({model_name})" if model_name else ""
    out += ["## How to read this report", "",
            "- **Affected** needs two things: the matcher found a removal or a changed argument in the changelog that matches this call, "
            f"and the AI model{model} explained it with a quote that was checked word for word against that note. "
            "The model is used only for these confirmed matches.",
            "- **Needs review** means the changelog mentions the function by name, but no note could be tied to this exact call. "
            "The model is deliberately not asked about these: a small model can neither confirm nor safely rule out a break from a name match. "
            "Read the listed notes yourself.",
            "- **Unsure** means the object may not be from the library (for example a plain list's `.append`), or the AI's answer could not be verified.",
            "- **No documented change does not mean safe.** Only changes written in the ingested changelog pages are checked, "
            "and only for calls the scanner found. Behaviour changes that the changelog describes without naming the function can be missed.",
            "- Suggested fixes appear only when the changelog itself states one.", ""]
    if refs:
        out += ["## Sources", ""]
        out += [f"- [{number}] {url}" for url, number in refs.items()]
        out.append("")
        out += [f"[{number}]: {url}" for url, number in refs.items()]
        out.append("")
    return "\n".join(out)


def write_report(findings: list, path: str, **kwargs) -> str:
    text = build_report(findings, **kwargs)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    logger.info("Report written: %s (%d usage(s))", path, len(findings))
    return text


if __name__ == "__main__":
    import argparse
    import datetime
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    import explain
    import llm

    parser = argparse.ArgumentParser(description="Build the migration report")
    parser.add_argument("--matches", default="data/sample_matches.json", help="saved matcher results (from snapshot.py)")
    parser.add_argument("--findings", help="render from saved findings (JSON) instead of asking the model")
    parser.add_argument("--out", default="reports/sample_report.md")
    args = parser.parse_args()

    try:
        header = json.loads(Path(args.matches).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        logger.error("Cannot read %s: %s", args.matches, error)
        sys.exit(1)
    library, target = header["library"], header["target"]
    config = llm.load_llm_config()
    model_name = (config or {}).get("model", "")

    if args.findings:
        findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    else:
        if config is None or not llm.is_server_up(config["base_url"]):
            print("The Ollama server is not running. In your second Terminal tab run: ollama serve")
            sys.exit(1)
        findings = explain.explain_usages(header["results"], llm.OllamaModel.from_config(config), library)

    versions = ""
    try:
        settings = json.loads(Path(f"configs/{library}/{library}_settings.json").read_text(encoding="utf-8"))
        versions = ", ".join(settings.get("migration_filters", {}).get("version", []))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    write_report(findings, args.out, library=library, target=target, versions=versions, model_name=model_name,
                 generated=datetime.date.today().isoformat())
    print(f"report: {args.out} | " + ", ".join(f"{n} {v}" for v, n in Counter(f["verdict"] for f in findings).most_common()))
