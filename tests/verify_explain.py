"""Checks for the answer generator's logic (no AI model needed). Run from the project root: python3 tests/verify_explain.py"""
import json, sys
sys.path.insert(0, "src")
import explain, match

ok_all = True

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

chunks = json.load(open("data/chunks/pandas_chunks.json"))

def real_chunk(prefix):
    return next(c for c in chunks if c["text"].startswith(prefix))

APPEND = real_chunk("Removed deprecated Series.append()")
READ_CSV_ARGS = real_chunk("Removed arguments prefix , squeeze , error_bad_lines")
TO_DICT = real_chunk("Disallow passing abbreviations for orient in DataFrame.to_dict()")

def candidate(chunk, rank=1):
    return {"rank": rank, "score": 0.03, "semantic_rank": rank, "keyword_rank": rank, "chunk": chunk}

def usage(function, line=9, snippet="x", keywords=(), resolved=True):
    return {"file": "loader.py", "line": line, "snippet": snippet, "function": function, "keywords": list(keywords),
            "resolved": resolved, "resolution": "tracked_variable" if resolved else "unknown", "kind": "call"}

APPEND_USAGE = usage("append", snippet="df = df.append(extra)")
APPEND_RESULT = match.match_usage(APPEND_USAGE, [candidate(APPEND)])
APPEND_ID = APPEND["chunk_id"]
QUOTE = "Removed deprecated Series.append() , DataFrame.append()"

def answer_json(verdict="affected", explanation="DataFrame.append() was removed in this version.", fix="Use concat() instead.",
                citations=((APPEND_ID, QUOTE),)):
    return json.dumps({"verdict": verdict, "explanation": explanation, "suggested_fix": fix,
                       "citations": [{"chunk_id": c, "quote": q} for c, q in citations]})

class FakeModel:
    """A stand-in for the AI: replays canned replies (or raises) and records every call."""
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
    def __call__(self, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

# ---- the prompt ---------------------------------------------------------------
prompt = explain.build_user_prompt(APPEND_RESULT, "pandas")
check("prompt shows the file, line, code line and function",
      all(part in prompt for part in ("loader.py:9", "df = df.append(extra)", "Function or attribute: append")))
check("prompt lists the note with its id and full text", f"[{APPEND_ID}]" in prompt and APPEND["text"] in prompt)
check("prompt shows keyword arguments (or 'none')", "Keyword arguments passed: none" in prompt
      and "Keyword arguments passed: error_bad_lines, sep" in explain.build_user_prompt(
          match.match_usage(usage("read_csv", keywords=["error_bad_lines", "sep"]), [candidate(READ_CSV_ARGS)]), "pandas"))
check("a confirmed object is stated as confirmed", "Object confirmed to be from pandas: yes" in prompt)
unconfirmed = explain.build_user_prompt(match.match_usage(usage("append", resolved=False), [candidate(APPEND)]), "pandas")
check("an unconfirmed object says so clearly", "NO - the scanner could not confirm this object is from pandas" in unconfirmed)
check("the matcher's evidence labels are NOT shown to the model", not any(w in prompt for w in ("keyword_match", "name_match", "removed\n", "evidence")))
check("the system prompt tells the model to answer 'unsure' when in doubt", "When in doubt, answer \"unsure\"" in explain.SYSTEM_PROMPT)

# ---- parsing --------------------------------------------------------------------
check("valid JSON parses", explain.parse_answer(answer_json()).verdict == "affected")
check("JSON inside a ```json fence parses", explain.parse_answer("```json\n" + answer_json() + "\n```").verdict == "affected")
check("optional fields default (fix None, no citations)", explain.parse_answer('{"verdict": "unsure", "explanation": "Not enough."}').citations == [])
for label, raw in (("an invalid verdict", '{"verdict": "maybe", "explanation": "x"}'), ("a missing explanation", '{"verdict": "unsure"}'),
                   ("plain prose instead of JSON", "I think it is affected."), ("an empty explanation", '{"verdict": "unsure", "explanation": ""}')):
    try:
        explain.parse_answer(raw)
        check(f"{label} is rejected", False)
    except Exception:
        check(f"{label} is rejected", True)

# ---- the citation checker ---------------------------------------------------------
notes = APPEND_RESULT["matches"]
def problems_for(**kwargs):
    return explain.check_answer(explain.parse_answer(answer_json(**kwargs)), notes)

check("an exact quote passes", problems_for() == [])
check("a quote with different spacing around punctuation passes (the changelog has 'append() , DataFrame')",
      problems_for(citations=((APPEND_ID, "Removed deprecated Series.append(), DataFrame.append()"),)) == [])
check("an invented quote is caught", len(problems_for(citations=((APPEND_ID, "DataFrame.append() will be removed in version 3.0"),))) >= 1)
check("a note that was not provided is caught", len(problems_for(citations=(("pandas-made-up-99", QUOTE),))) >= 1)
check("a quote shorter than 3 words is caught", len(problems_for(citations=((APPEND_ID, "Removed deprecated"),))) >= 1)
check("'affected' with no citation is rejected", "needs at least one valid citation" in " ".join(problems_for(citations=())))
check("'affected' with only an invalid citation is rejected", len(problems_for(citations=((APPEND_ID, "totally invented quote text"),))) >= 2)
check("'not_affected' without citations is allowed", problems_for(verdict="not_affected", citations=(), fix=None) == [])
check("'unsure' without citations is allowed", problems_for(verdict="unsure", citations=(), fix=None) == [])
check("'not_affected' with an invalid citation is still rejected", len(problems_for(verdict="not_affected", citations=((APPEND_ID, "totally invented quote text"),), fix=None)) >= 1)
check("3 sentences pass", problems_for(explanation="One. Two. Three.") == [])
check("4 sentences are rejected", any("more than 3 sentences" in p for p in problems_for(explanation="One. Two. Three. Four.")))
check("dots inside names do not count as sentences", explain.sentence_count("DataFrame.append() and Series.append() were removed.") == 1)

# ---- suggested fixes ----------------------------------------------------------------
check("a fix that calls a function named in the note is kept", explain.ground_fix("Use concat() instead.", notes, APPEND_USAGE) == ("Use concat() instead.", False))
check("a fix that invents a function is dropped", explain.ground_fix("Use df.merge(extra) instead.", notes, APPEND_USAGE) == (None, True))
check("a fix may call the function from the code line itself", explain.ground_fix("Replace append(extra) with concat([df, extra]).", notes, APPEND_USAGE) == ("Replace append(extra) with concat([df, extra]).", False))
check("no fix stays no fix", explain.ground_fix(None, notes, APPEND_USAGE) == (None, False) and explain.ground_fix("  ", notes, APPEND_USAGE) == (None, False))

# ---- one finding, with fake models ------------------------------------------------------
model = FakeModel(answer_json())
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("a good answer: verdict, fix and citation kept, one call, no flags",
      finding["verdict"] == "affected" and finding["suggested_fix"] == "Use concat() instead." and len(finding["citations"]) == 1
      and finding["flags"] == [] and len(model.calls) == 1)
check("the model is given the system prompt, the user prompt and the JSON schema",
      model.calls[0]["system"] == explain.SYSTEM_PROMPT and APPEND_ID in model.calls[0]["user"] and "verdict" in model.calls[0]["schema"]["properties"])
check("the finding carries the usage, status and matched notes for the report",
      finding["usage"] == APPEND_USAGE and finding["status"] == "documented_change" and finding["matches"] == notes)

liar = answer_json(citations=((APPEND_ID, "DataFrame.append() will be removed in version 3.0"),))
model = FakeModel(liar, answer_json())
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("a lying answer, then an honest one: retried once and accepted", len(model.calls) == 2 and finding["verdict"] == "affected" and finding["flags"] == ["needed_retry"])
check("the retry message repeats the notes and says why the first answer was rejected",
      model.calls[1]["user"].startswith(model.calls[0]["user"]) and "rejected" in model.calls[1]["user"] and "not word for word" in model.calls[1]["user"])

model = FakeModel(liar, liar)
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("two lying answers: exactly 2 calls, downgraded to 'unsure', flagged, no citations shown",
      len(model.calls) == 2 and finding["verdict"] == "unsure" and finding["flags"] == ["citation_check_failed"] and finding["citations"] == [])
check("...and the explanation says it could not be verified", "could not be verified" in finding["explanation"])

model = FakeModel(RuntimeError("connection refused"), ConnectionError("still down"))
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("a model that is down twice: 'unsure' with a model_error flag, no crash", finding["verdict"] == "unsure" and finding["flags"] == ["model_error"] and len(model.calls) == 2)

model = FakeModel("I think it is affected.", answer_json())
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("prose instead of JSON, then valid JSON: retried and accepted", finding["verdict"] == "affected" and finding["flags"] == ["needed_retry"])

nothing = {"usage": usage("tolist"), "status": "no_documented_change", "confidence": "high", "matches": []}
model = FakeModel(answer_json())
finding = explain.explain_usage(nothing, model, "pandas")
check("no documented change: the model is never called", len(model.calls) == 0 and finding["verdict"] == "no_documented_change")

low = match.match_usage(usage("append", resolved=False), [candidate(APPEND)])
finding = explain.explain_usage(low, FakeModel(answer_json()), "pandas")
check("unconfirmed object: 'affected' is shown as 'unsure', flagged, with the reason in the text",
      finding["verdict"] == "unsure" and "capped_unconfirmed_type" in finding["flags"] and "could not confirm" in finding["explanation"] and finding["confidence"] == "low")
finding = explain.explain_usage(low, FakeModel(answer_json(verdict="unsure", citations=(), fix=None)), "pandas")
check("unconfirmed object, model already unsure: the reason is still stated, no cap flag",
      finding["verdict"] == "unsure" and "could not confirm" in finding["explanation"] and "capped_unconfirmed_type" not in finding["flags"])
check("a confirmed object keeps 'affected'", explain.explain_usage(APPEND_RESULT, FakeModel(answer_json()), "pandas")["verdict"] == "affected")

finding = explain.explain_usage(APPEND_RESULT, FakeModel(answer_json(fix="Use df.merge(extra) instead.")), "pandas")
check("an invented fix is dropped but the verdict stays", finding["suggested_fix"] is None and "fix_dropped" in finding["flags"] and finding["verdict"] == "affected")

to_dict_result = match.match_usage(usage("to_dict", snippet="rows = df.to_dict()"), [candidate(TO_DICT)])
finding = explain.explain_usage(to_dict_result, FakeModel(answer_json(verdict="not_affected", explanation="The note is about the orient argument, which this call does not pass.",
                                                                       fix=None, citations=((TO_DICT["chunk_id"], "Disallow passing abbreviations for orient"),))), "pandas")
check("a real 'review' case: 'not_affected' with a valid citation is accepted", to_dict_result["status"] == "review" and finding["verdict"] == "not_affected" and finding["flags"] == [])

# ---- many usages -------------------------------------------------------------------------------
TO_DICT_ANSWER = answer_json(verdict="not_affected", explanation="The note is about the orient argument, which this call does not pass.",
                             fix=None, citations=((TO_DICT["chunk_id"], "Disallow passing abbreviations for orient"),))

class AnswersByUsage:
    """A fake model that answers according to the code it is shown, and records its calls."""
    def __init__(self):
        self.calls = []
    def __call__(self, system, user, schema):
        self.calls.append(user)
        return TO_DICT_ANSWER if "Function or attribute: to_dict" in user else answer_json()

model = AnswersByUsage()
findings = explain.explain_usages([APPEND_RESULT, nothing, to_dict_result], model, "pandas")
check("explain_usages: one finding per usage, in the order given", [f["verdict"] for f in findings] == ["affected", "no_documented_change", "not_affected"])
check("explain_usages: the model is called only for flagged usages (2 of 3, one call each)", len(model.calls) == 2)

print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
