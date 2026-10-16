"""Checks for the Markdown report (no model needed). Run from the project root: python3 tests/verify_report.py"""
import json, re, sys, tempfile
from collections import Counter
from pathlib import Path
sys.path.insert(0, "src")
import explain, report, snapshot

ok_all = True

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

class HonestModel:
    """A fake model that says 'affected' on whatever it is shown, quoting the first words of Note 1 exactly."""
    def __call__(self, system, user, schema):
        note1 = re.search(r"Note 1\nSource: [^\n]*\nText: (.*?)(?:\n\nNote 2|\Z)", user, re.S).group(1)
        return json.dumps({"verdict": "affected", "explanation": "This call uses what the note says was removed.", "suggested_fix": None,
                           "citations": [{"note": 1, "quote": " ".join(note1.split()[:8])}]})

results = snapshot.load_matches()
findings = explain.explain_usages(results, HonestModel(), "pandas")
counts = Counter(f["verdict"] for f in findings)
text = report.build_report(findings, library="pandas", target="samples/pandas1_sample", versions="2.0.0", model_name="qwen2.5:3b")

# ---- header and summary -----------------------------------------------------------------------------
check("the title names the library and the version", text.startswith("# Migration report: pandas → 2.0.0"))
check("the header says what was scanned and how many usages", "Scanned: `samples/pandas1_sample` · 24 usage(s) of pandas found" in text)
for verdict in ("affected", "needs_review", "unsure", "no_documented_change"):
    check(f"the summary counts {verdict} correctly", f"| **{report.LABEL[verdict]}** | {counts[verdict]} |" in text, str(counts[verdict]))
check("the summary total is the number of usages", "| **Total** | 24 |" in text)
check("the summary leaves out verdicts that do not occur", "NOT AFFECTED" not in text.split("## Confirmed breaks")[0])

# ---- confirmed breaks --------------------------------------------------------------------------------------
table = text.split("## Confirmed breaks")[1].split("## Details by file")[0]
rows = [line for line in table.splitlines() if line.startswith("| `")]
check("one quick-table row per confirmed break", len(rows) == counts["affected"] == 7, str(len(rows)))
check("the quick table shows the cited note's own text, not a cut-off quote", "Removed arguments prefix , squeeze , error_bad_lines and warn_bad_lines from read_csv()" in table)

# ---- details by file ------------------------------------------------------------------------------------------
details = text.split("## Details by file")[1].split("## No documented change")[0]
files = re.findall(r"^### (\S+)$", details, re.M)
check("each file with findings has one heading, in alphabetical order", files == sorted(set(files)) and len(files) == len(set(files)) and len(files) >= 4, str(files))
def lines_in(file):
    block = re.split(r"^### ", details, flags=re.M)
    section = next(b for b in block if b.startswith(file + "\n"))
    return [int(n) for n in re.findall(r"^#### [A-Z ]+ · line (\d+)", section, re.M)]
check("findings inside a file are in line order", all(lines_in(f) == sorted(lines_in(f)) for f in files))

affected = [f for f in findings if f["verdict"] == "affected"]
evidence_ok = all(c["quote"] in text for f in affected for c in f["citations"])
check("every confirmed break's cited quote appears in the report", evidence_ok)
check("each evidence block names the library version and a numbered source link", text.count("> — pandas 2.0.0, *") >= len(affected) and "([source][1])" in text)
used = set(re.findall(r"\[source\]\[(\d+)\]", text))
defined = set(re.findall(r"^\[(\d+)\]: https?://", text, re.M))
check("every numbered source link has a URL defined, and the URLs are also listed visibly", used <= defined and all(f"- [{n}] http" in text for n in used))

review = [f for f in findings if f["verdict"] == "needs_review"]
section = text.split("#### NEEDS REVIEW")[1]
check("a needs-review usage says why and lists its matched notes", "none could be matched to this exact call" in section and section.count("\n- ") >= len(review[0]["matches"]))
bullets = sum(1 for f in review for _ in f["matches"])
check("every matched note of every needs-review usage is listed", len(re.findall(r"^- .* — pandas 2\.0\.0, \*", details, re.M)) >= bullets, str(bullets))

capped = [f for f in findings if "capped_unconfirmed_type" in f["flags"]]
check("the data has unconfirmed objects that the model called affected", len(capped) >= 1)
capped_block = text.split("#### UNSURE")[1].split("####")[0]
check("an unconfirmed object is explained plainly, without repeating the model's claim",
      "could not confirm that it is one" in capped_block and "uses what the note says" not in capped_block)
check("internal flags and traces never appear in the report", "capped_" not in text and "needed_retry" not in text and "citation_check_failed" not in text)

quiet = text.split("## No documented change")[1].split("## How to read")[0]
check("usages with no documented change are listed on their own", quiet.count("\n- `") == counts["no_documented_change"] == 4)
check("...with the warning that this is not a guarantee", "not a guarantee" in quiet)

how = text.split("## How to read this report")[1]
check("the model's name appears in 'how to read'", "qwen2.5:3b" in how)
check("the limits are stated: no documented change does not mean safe", "No documented change does not mean safe" in how)

# ---- suggested fixes, escaping, fences ------------------------------------------------------------------------------
base = json.loads(json.dumps(affected[0]))
with_fix = {**base, "suggested_fix": "Use concat() instead."}
without_fix = {**base, "suggested_fix": None}
check("a suggested fix is shown, labelled as coming from the changelog", "**Fix suggested by the changelog:** Use concat() instead." in report.build_report([with_fix], "pandas", "t"))
check("no suggested fix, no fix line", "Fix suggested by the changelog:**" not in report.build_report([without_fix], "pandas", "t"))
pipe = json.loads(json.dumps(base)); pipe["usage"]["snippet"] = "x = a | b"; pipe["suggested_fix"] = "use a | b"
row = [l for l in report.build_report([pipe], "pandas", "t").splitlines() if l.startswith("| `")][0]
check("a pipe character inside a table cell is escaped, so the table keeps its 4 columns", len(re.findall(r"(?<!\\)\|", row)) == 5, row)
tick = json.loads(json.dumps(base)); tick["usage"]["snippet"] = "s = ```code```"
check("a code line containing backticks gets a longer fence", "````python" in report.build_report([tick], "pandas", "t"))
check("a code line is shown in a python code block", "```python\ndf = df.append(extra)\n```" in text)

# ---- edge cases --------------------------------------------------------------------------------------------------------
empty = report.build_report([], "pandas", "somewhere")
check("no usages: a plain message, no crash, no empty tables", "nothing to report" in empty and "| Result |" not in empty)
check("the same input always gives the same report", text == report.build_report(findings, library="pandas", target="samples/pandas1_sample", versions="2.0.0", model_name="qwen2.5:3b"))
check("a generated date is included only when given", "generated 2026-10-17" in report.build_report([], "pandas", "t", generated="2026-10-17") and "generated" not in empty)
with tempfile.TemporaryDirectory() as d:
    path = str(Path(d) / "reports" / "nested" / "r.md")
    written = report.write_report(findings, path, library="pandas", target="x")
    check("write_report creates folders and writes exactly what build_report returns", Path(path).read_text(encoding="utf-8") == written)

print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
