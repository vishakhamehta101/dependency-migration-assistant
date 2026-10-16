"""Checks that the AI gates judge correctly (no model needed). Run from the project root: python3 tests/verify_gates_ai.py"""
import copy, json, sys
sys.path.insert(0, "src"); sys.path.insert(0, "tests")
import gates_ai, snapshot

ok_all = True

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

# ---- the keys match the real saved data -----------------------------------------------------
results = snapshot.load_matches()
by_key = {(r["usage"]["file"], r["usage"]["line"], r["usage"]["function"]): r for r in results}
check("there are 7 recall keys and 4 false-alarm keys", len(gates_ai.G1_KEYS) == 7 and len(gates_ai.G2_KEYS) == 4)
check("every gate key exists in the saved matcher results", all(k in by_key for k in list(gates_ai.G1_KEYS) + gates_ai.G2_KEYS))
check("every recall key is a confirmed (high-confidence) documented change",
      all(by_key[k]["status"] == "documented_change" and by_key[k]["confidence"] == "high" for k in gates_ai.G1_KEYS))
check("the expected note is among the notes the model is shown, for every recall key",
      all(any(m["chunk"]["text"].startswith(prefix) for m in by_key[k]["matches"]) for k, prefix in gates_ai.G1_KEYS.items()))
check("every false-alarm key goes to the model (status 'review')", all(by_key[k]["status"] == "review" for k in gates_ai.G2_KEYS))
check("the two key lists do not overlap", not set(gates_ai.G1_KEYS) & set(gates_ai.G2_KEYS))

# ---- synthetic findings to test the judge ------------------------------------------------------
def make_finding(result, verdict, citations=True, flags=(), fix=None, explanation=None):
    usage = result["usage"]
    cites = [{"note": 1, "chunk_id": result["matches"][0]["chunk"]["chunk_id"], "quote": " ".join(result["matches"][0]["chunk"]["text"].split()[:6])}] if citations and result["matches"] else []
    text = explanation or "Because of the note."
    if result["confidence"] == "low":
        text += " (The scanner could not confirm this object is from pandas, so treat this as a possibility.)"
    return {"usage": usage, "status": result["status"], "confidence": result["confidence"], "matches": result["matches"], "verdict": verdict,
            "explanation": text, "suggested_fix": fix, "citations": cites, "flags": list(flags), "trace": []}

def good_run():
    out = []
    for r in results:
        if r["status"] == "no_documented_change":
            continue
        key = (r["usage"]["file"], r["usage"]["line"], r["usage"]["function"])
        verdict = "affected" if key in gates_ai.G1_KEYS else ("unsure" if r["confidence"] == "low" else "not_affected")
        out.append(make_finding(r, verdict, citations=verdict != "not_affected"))
    return out

run = good_run()
check("the good run has the 20 flagged usages", len(run) == 20)
gates = gates_ai.evaluate_gates(run, copy.deepcopy(run))
check("a good run passes all four gates", all(gates[g]["passed"] for g in ("G1", "G2", "G3", "G4")), str({g: gates[g]["passed"] for g in gates}))
check("G1 reports how many cited the expected note", "7/7 'affected' (7/7 cited the expected note)" in gates["G1"]["summary"], gates["G1"]["summary"])
check("G2 says how many of its calls were low-confidence and already capped", "1 of them low-confidence" in gates["G2"]["summary"], gates["G2"]["summary"])
check("without a second run, G4 is 'not done', not a pass", gates_ai.evaluate_gates(run)["G4"]["passed"] is None)

def with_change(key, **changes):
    changed = copy.deepcopy(run)
    for f in changed:
        if gates_ai.key_of(f) == key:
            f.update(changes)
    return changed

k1 = ("loader.py", 9, "append")
g = gates_ai.evaluate_gates(with_change(k1, verdict="unsure"))
check("G1 fails when a documented break is 'unsure', and names it", not g["G1"]["passed"] and "loader.py" in g["G1"]["failures"][0])
check("G1 fails when a recall usage is missing", not gates_ai.evaluate_gates([f for f in run if gates_ai.key_of(f) != k1])["G1"]["passed"])
g = gates_ai.evaluate_gates(with_change(("cleaning.py", 15, "to_dict"), verdict="affected"))
check("G2 fails on a false alarm", not g["G2"]["passed"] and "to_dict" in g["G2"]["failures"][0])
g = gates_ai.evaluate_gates(with_change(k1, citations=[]))
check("G3 fails when 'affected' has no citation", not g["G3"]["passed"])
low_key = ("loader.py", 10, "append")
g = gates_ai.evaluate_gates(with_change(low_key, verdict="affected"))
check("G3 fails when an unconfirmed object is shown as 'affected'", not g["G3"]["passed"] and "unconfirmed" in g["G3"]["failures"][0])
g = gates_ai.evaluate_gates(with_change(low_key, explanation="The call is on a list."))
check("G3 fails when an unconfirmed object's explanation does not say so", not g["G3"]["passed"] and "does not say so" in g["G3"]["failures"][0])
g = gates_ai.evaluate_gates(with_change(k1, suggested_fix="Use df.concat([extra], ignore_index=True)"))
check("G3 fails when an ungrounded fix is shown", not g["G3"]["passed"] and "not grounded" in g["G3"]["failures"][0])
g = gates_ai.evaluate_gates(with_change(k1, suggested_fix="Use concat() instead."))
check("G3 accepts a grounded fix", g["G3"]["passed"])

second = copy.deepcopy(run); second[0]["verdict"] = "unsure" if second[0]["verdict"] != "unsure" else "affected"
g = gates_ai.evaluate_gates(run, second)
check("G4 passes with 1 of 20 verdicts different (95%)", g["G4"]["passed"] and "95%" in g["G4"]["summary"], g["G4"]["summary"])
second[1]["verdict"] = "unsure" if second[1]["verdict"] != "unsure" else "affected"
g = gates_ai.evaluate_gates(run, second)
check("G4 fails with 2 of 20 different (90%), and lists them", not g["G4"]["passed"] and len(g["G4"]["failures"]) == 2)
check("G4 treats a missing finding in the second run as a difference", not gates_ai.evaluate_gates(run, run[:5])["G4"]["passed"])

# ---- G6 (D29) and the raw-verdict statistics ------------------------------------------------------
check("a good run passes G6", gates_ai.evaluate_gates(run)["G6"]["passed"], gates_ai.evaluate_gates(run)["G6"]["summary"])
g = gates_ai.evaluate_gates(with_change(("cleaning.py", 6, "read_csv"), verdict="affected"))
check("G6 fails when a review-level usage is shown 'affected', and names it", not g["G6"]["passed"] and "cleaning.py" in g["G6"]["failures"][0])
check("'affected' on a confirmed documented change is not a G6 violation", gates_ai.evaluate_gates(run)["G6"]["failures"] == [])
stated = copy.deepcopy(run)
for f in stated:
    if gates_ai.key_of(f) in (k1, ("cleaning.py", 6, "read_csv"), ("cleaning.py", 5, "DataFrame")):
        f["raw_verdict"] = "affected"
stats = gates_ai.raw_stats(stated)
check("raw_stats counts what the model said (3), how many were confirmed breaks (1) and what is shown (7)",
      stats == {"said_affected": 3, "said_affected_confirmed": 1, "shown_affected": 7}, str(stats))

print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
