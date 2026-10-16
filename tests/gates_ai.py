"""
AI stage gates (D18). Run from the project root, with the Ollama server running:  python3 -u tests/gates_ai.py

Asks the model about every usage the matcher flagged (from data/sample_matches.json), twice, and judges:
  G1 Recall        all 7 documented breaks come back 'affected'
  G2 False alarms  4 calls whose notes concern things they do not use are never 'affected'
  G3 Honesty       every 'affected' has a verified citation; unconfirmed objects are never 'affected' and say so;
                   every shown fix is grounded
  G4 Stability     two runs agree on at least 95% of the model-judged verdicts (D30: only documented changes reach the model)
  G5 Cost          reported only (time, retries, failures, swap)
  G6 Evidence rule (D29, added after the first run)  no 'review'-level usage is ever shown 'affected'
The keys below were fixed before any model results existed.
"""
import json, re, subprocess, sys, time

sys.path.insert(0, "src")
import explain

# (file, line, function) -> the start of the note that documents the break
G1_KEYS = {
    ("loader.py", 9, "append"): "Removed deprecated Series.append()",
    ("io_tools.py", 7, "save"): "Removed deprecated methods ExcelWriter.write_cells()",
    ("io_tools.py", 9, "Int64Index"): "Removed Int64Index",
    ("io_tools.py", 10, "Int64Index"): "Removed Int64Index",
    ("legacy_args.py", 5, "read_csv"): "Removed arguments prefix , squeeze , error_bad_lines",
    ("legacy_args.py", 6, "groupby"): "Remove argument squeeze from DataFrame.groupby()",
    ("legacy_args.py", 7, "to_excel"): "Removed arguments verbose and encoding from DataFrame.to_excel()",
}
G2_KEYS = [("cleaning.py", 15, "to_dict"), ("io_tools.py", 6, "to_excel"), ("io_tools.py", 5, "ExcelWriter"), ("cleaning.py", 9, "groupby")]
STABILITY_MIN = 0.95


def key_of(finding: dict) -> tuple:
    usage = finding["usage"]
    return usage["file"], usage["line"], usage["function"]


def cited_note_texts(finding: dict) -> list:
    return [finding["matches"][c["note"] - 1]["chunk"]["text"] for c in finding["citations"]]


def evaluate_gates(run1: list, run2: list | None = None) -> dict:
    """Judge one run (G1-G3) and, if a second run is given, stability (G4). Each gate: {"passed", "summary", "failures"}."""
    by_key = {key_of(f): f for f in run1}
    gates = {}

    failures = [f"{key}: {by_key[key]['verdict'] if key in by_key else 'no finding'}" for key in G1_KEYS
                if key not in by_key or by_key[key]["verdict"] != "affected"]
    right_note = sum(1 for key, prefix in G1_KEYS.items()
                     if key in by_key and any(text.startswith(prefix) for text in cited_note_texts(by_key[key])))
    gates["G1"] = {"passed": not failures, "failures": failures,
                   "summary": f"{len(G1_KEYS) - len(failures)}/{len(G1_KEYS)} 'affected' ({right_note}/{len(G1_KEYS)} cited the expected note)"}

    failures = [f"{key}: {by_key[key]['verdict']}" for key in G2_KEYS if key in by_key and by_key[key]["verdict"] == "affected"]
    failures += [f"{key}: no finding" for key in G2_KEYS if key not in by_key]
    capped = [key for key in G2_KEYS if key in by_key and by_key[key]["confidence"] == "low"]
    gates["G2"] = {"passed": not failures, "failures": failures,
                   "summary": f"{len(G2_KEYS) - len(failures)}/{len(G2_KEYS)} not 'affected'"
                              + (f" ({len(capped)} of them low-confidence, so already capped by D19)" if capped else "")}

    failures = []
    for finding in run1:
        key = key_of(finding)
        if finding["verdict"] == "affected" and not finding["citations"]:
            failures.append(f"{key}: 'affected' without a verified citation")
        if finding["confidence"] == "low":
            if finding["verdict"] == "affected":
                failures.append(f"{key}: unconfirmed object shown as 'affected'")
            if finding["status"] != "no_documented_change" and "could not confirm" not in finding["explanation"]:
                failures.append(f"{key}: unconfirmed object, but the explanation does not say so")
        fix = finding.get("suggested_fix")
        if fix and explain.ground_fix(fix, finding["matches"], finding["usage"]) != (fix, False):
            failures.append(f"{key}: shown fix is not grounded in the notes")
    affected = sum(1 for f in run1 if f["verdict"] == "affected")
    gates["G3"] = {"passed": not failures, "failures": failures,
                   "summary": f"{affected} 'affected' finding(s), all with verified citations; {sum(1 for f in run1 if f['confidence'] == 'low')} unconfirmed object(s) handled" if not failures
                              else f"{len(failures)} problem(s)"}

    review = [f for f in run1 if f["status"] == "review"]
    violations = [f"{key_of(f)}: review-level usage shown 'affected'" for f in review if f["verdict"] == "affected"]
    gates["G6"] = {"passed": not violations, "failures": violations,
                   "summary": f"none of the {len(review)} review-level usages is shown 'affected' (D29)" if not violations else f"{len(violations)} violation(s)"}

    if run2 is None:
        gates["G4"] = {"passed": None, "failures": [], "summary": "second run not done"}
    else:
        second = {key_of(f): f["verdict"] for f in run2}
        judged = [f for f in run1 if f["status"] == "documented_change"]   # the only usages the model is asked about
        differ = [f"{key_of(f)}: {f['verdict']} then {second.get(key_of(f), 'missing')}" for f in judged if second.get(key_of(f)) != f["verdict"]]
        agreement = 1 - len(differ) / len(judged) if judged else 0.0
        gates["G4"] = {"passed": agreement >= STABILITY_MIN, "failures": differ,
                       "summary": f"{agreement:.0%} of {len(judged)} model-judged verdicts agree between the two runs"}
    return gates


def raw_stats(run: list) -> dict:
    """What the model itself said, before any cap, against what is shown."""
    said = [f for f in run if f.get("raw_verdict") == "affected"]
    return {"said_affected": len(said),
            "said_affected_confirmed": sum(1 for f in said if f["status"] == "documented_change" and f["confidence"] == "high"),
            "shown_affected": sum(1 for f in run if f["verdict"] == "affected")}


def swap_used_mb():
    try:
        out = subprocess.run(["sysctl", "vm.swapusage"], capture_output=True, text=True, timeout=5).stdout
        found = re.search(r"used = ([\d.]+)M", out)
        return float(found.group(1)) if found else None
    except Exception:
        return None


def run_once(results: list, model, label: str):
    flagged = [r for r in results if r["status"] != "no_documented_change"]
    asked = sum(1 for r in flagged if r["status"] == "documented_change")
    print(f"\n--- {label}: {len(flagged)} flagged usages; {asked} go to the model, {len(flagged) - asked} are reported as needs_review ---", flush=True)
    findings, started, calls_before = [], time.perf_counter(), len(model.stats)
    for index, result in enumerate(flagged, start=1):
        before = len(model.stats)
        finding = explain.explain_usage(result, model, "pandas")
        seconds = sum(s["seconds"] for s in model.stats[before:])
        usage = finding["usage"]
        print(f"  [{index:2d}/{len(flagged)}] {usage['file']}:{usage['line']} {usage['function']:22s} -> {finding['verdict']:12s} {finding['flags'] or ''} ({seconds:.1f}s)", flush=True)
        findings.append(finding)
    return findings, time.perf_counter() - started, len(model.stats) - calls_before


def describe(finding: dict) -> None:
    print(f"    explanation: {finding['explanation']}")
    for attempt in finding["trace"]:
        if attempt["problems"]:
            print(f"    attempt {attempt['attempt']} rejected: {attempt['problems']}")
            if attempt["reply"]:
                print("      its reply:", attempt["reply"][:400].replace("\n", " "))


if __name__ == "__main__":
    import llm
    import snapshot

    config, results = llm.load_llm_config(), snapshot.load_matches()
    if config is None or results is None:
        sys.exit(1)
    if not llm.is_server_up(config["base_url"]):
        print("The Ollama server is not running. In your second Terminal tab run: ollama serve")
        sys.exit(1)
    if config["model"] not in llm.list_models(config["base_url"]):
        print(f"The model {config['model']} is not downloaded. Run: ollama pull {config['model']}")
        sys.exit(1)

    model = llm.OllamaModel.from_config(config)
    print(f"model: {config['model']} | swap in use at the start: {swap_used_mb()} MB")
    runs, stats = [], []
    for label in ("run 1", "run 2"):
        findings, seconds, calls = run_once(results, model, label)
        runs.append(findings)
        stats.append((seconds, calls, swap_used_mb()))
        with open(f"/tmp/ai_findings_{label.replace(' ', '')}.json", "w") as handle:
            json.dump(findings, handle, indent=2)

    gates = evaluate_gates(runs[0], runs[1])
    by_key = {key_of(f): f for f in runs[0]}
    print("\n=== gates ===")
    for name, title in (("G1", "Recall"), ("G2", "No false alarms"), ("G3", "Honesty"), ("G4", "Stability"), ("G6", "Evidence rule")):
        gate = gates[name]
        print(f"{name} {title:16s} {'PASS' if gate['passed'] else 'FAIL'}  {gate['summary']}")
        for failure in gate["failures"]:
            print(f"      - {failure}")
            key = next((k for k in by_key if str(k) in failure), None)
            if key:
                describe(by_key[key])

    counts = {}
    for finding in runs[0]:
        counts[finding["verdict"]] = counts.get(finding["verdict"], 0) + 1
    flags = {}
    for finding in runs[0]:
        for flag in finding["flags"]:
            flags[flag] = flags.get(flag, 0) + 1
    print("\n=== G5 cost and reliability (reported, not judged) ===")
    for label, (seconds, calls, swap) in zip(("run 1", "run 2"), stats):
        print(f"{label}: {seconds:.0f}s for {len(runs[0])} usages | {calls} model calls | swap in use afterwards: {swap} MB")
    print(f"average speed: {sum(s['tokens_per_second'] for s in model.stats) / len(model.stats):.1f} tokens/s")
    print("verdicts in run 1:", counts, "| flags in run 1:", flags or "none")
    stated = raw_stats(runs[0])
    print(f"the model itself said 'affected' on {stated['said_affected']} of {len(runs[0])} usages "
          f"({stated['said_affected_confirmed']} on confirmed documented changes); shown as 'affected': {stated['shown_affected']}")
    print("\nall findings (run 1):")
    for finding in runs[0]:
        key = key_of(finding)
        marker = "G1" if key in G1_KEYS else ("G2" if key in G2_KEYS else "  ")
        print(f"  {marker} {key[0]}:{key[1]} {key[2]:22s} {finding['verdict']:12s} matcher={finding['status']:18s} confidence={finding['confidence']:5s} model said={finding.get('raw_verdict')}")
