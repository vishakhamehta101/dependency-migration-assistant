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
QUOTE = "Removed deprecated Series.append() , DataFrame.append()"

def answer_json(verdict="affected", explanation="DataFrame.append() was removed in this version.", fix="Use concat() instead.",
                citations=((1, QUOTE),)):
    return json.dumps({"verdict": verdict, "explanation": explanation, "suggested_fix": fix,
                       "citations": [{"note": n, "quote": q} for n, q in citations]})

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
check("the note is numbered and has separate Source and Text lines",
      "Note 1\nSource: version 2.0.0, " in prompt and f"\nText: {APPEND['text']}" in prompt)
check("the long chunk id is NOT in the prompt (the model cannot mangle what it never sees)", APPEND["chunk_id"] not in prompt)
two = match.match_usage(usage("read_csv", keywords=["error_bad_lines", "sep"]), [candidate(READ_CSV_ARGS, 1), candidate(TO_DICT, 2)])
two_prompt = explain.build_user_prompt({**two, "matches": [{"chunk": READ_CSV_ARGS}, {"chunk": TO_DICT}]}, "pandas")
check("several notes are numbered 1, 2 in order", two_prompt.index("Note 1") < two_prompt.index(READ_CSV_ARGS["text"]) < two_prompt.index("Note 2") < two_prompt.index(TO_DICT["text"]))
check("prompt shows keyword arguments (or 'none')", "Keyword arguments passed: none" in prompt and "Keyword arguments passed: error_bad_lines, sep" in two_prompt)
check("a confirmed object is stated as confirmed", "Object confirmed to be from pandas: yes" in prompt)
unconfirmed = explain.build_user_prompt(match.match_usage(usage("append", resolved=False), [candidate(APPEND)]), "pandas")
check("an unconfirmed object says so clearly", "NO - the scanner could not confirm this object is from pandas" in unconfirmed)
check("the matcher's evidence labels are NOT shown to the model", not any(w in prompt for w in ("keyword_match", "name_match", "evidence")))
check("the system prompt asks for note numbers, short exact quotes, Text-only, and 'unsure' when in doubt",
      all(part in explain.SYSTEM_PROMPT for part in ('"note": 1', "5 to 15 words", "never from its Source line", 'When in doubt, answer "unsure"')))

# ---- parsing --------------------------------------------------------------------
check("valid JSON parses", explain.parse_answer(answer_json()).verdict == "affected")
check("JSON inside a ```json fence parses", explain.parse_answer("```json\n" + answer_json() + "\n```").verdict == "affected")
check("optional fields default (fix None, no citations)", explain.parse_answer('{"verdict": "unsure", "explanation": "Not enough."}').citations == [])
for label, raw in (("an invalid verdict", '{"verdict": "maybe", "explanation": "x"}'), ("a missing explanation", '{"verdict": "unsure"}'),
                   ("plain prose instead of JSON", "I think it is affected."), ("an empty explanation", '{"verdict": "unsure", "explanation": ""}'),
                   ("a citation that uses a long id instead of a note number", '{"verdict": "affected", "explanation": "x", "citations": [{"chunk_id": "pandas-changelog_v2.0.0-20-22", "quote": "a b c d"}]}'),
                   ("note number 0", '{"verdict": "affected", "explanation": "x", "citations": [{"note": 0, "quote": "a b c d"}]}')):
    try:
        explain.parse_answer(raw)
        check(f"{label} is rejected", False)
    except Exception:
        check(f"{label} is rejected", True)

# ---- the citation checker ---------------------------------------------------------
notes = APPEND_RESULT["matches"]
def problems_for(**kwargs):
    return explain.check_answer(explain.parse_answer(answer_json(**kwargs)), notes)

check("an exact quote from the right note passes", problems_for() == [])
check("a quote with different spacing around punctuation passes (the changelog has 'append() , DataFrame')",
      problems_for(citations=((1, "Removed deprecated Series.append(), DataFrame.append()"),)) == [])
check("an invented quote is caught", len(problems_for(citations=((1, "DataFrame.append() will be removed in version 3.0"),))) >= 1)
out_of_range = problems_for(citations=((7, QUOTE),))
check("a note number that does not exist is caught, and the message lists the valid numbers", any("note 7 does not exist" in p and "valid note numbers are 1" in p for p in out_of_range))
check("a quote shorter than 3 words is caught", len(problems_for(citations=((1, "Removed deprecated"),))) >= 1)
check("a quote that glues the Source heading onto the Text is caught (the real mistake qwen2.5:3b made)",
      any("not word for word in the Text of note 1" in p for p in problems_for(citations=((1, "Removal of prior version deprecations/changes\nRemoved deprecated Series.append() , DataFrame.append() , use concat() instead ( GH 35407 )"),))))
check("'affected' with no citation is rejected", "needs at least one valid citation" in " ".join(problems_for(citations=())))
check("'affected' with only an invalid citation is rejected", len(problems_for(citations=((1, "totally invented quote text"),))) >= 2)
check("'not_affected' without citations is allowed", problems_for(verdict="not_affected", citations=(), fix=None) == [])
check("'unsure' without citations is allowed", problems_for(verdict="unsure", citations=(), fix=None) == [])
check("'not_affected' with an invalid citation is still rejected", len(problems_for(verdict="not_affected", citations=((1, "totally invented quote text"),), fix=None)) >= 1)
check("3 sentences pass", problems_for(explanation="One. Two. Three.") == [])
check("4 sentences are rejected", any("more than 3 sentences" in p for p in problems_for(explanation="One. Two. Three. Four.")))
check("dots inside names do not count as sentences", explain.sentence_count("DataFrame.append() and Series.append() were removed.") == 1)

# ---- suggested fixes: every code-like piece must appear verbatim ------------------------------
def fix(text):
    return explain.ground_fix(text, notes, APPEND_USAGE)
check("a fix that repeats the note's wording is kept", fix("Use concat() instead.") == ("Use concat() instead.", False))
check("a fix that invents a method is dropped", fix("Use df.merge(extra) instead.") == (None, True))
check("the real invented fix from qwen2.5:3b is dropped: df.concat(...) and ignore_index do not appear in any note",
      fix("use df.concat([extra], ignore_index=True)") == (None, True))
check("an invented argument name alone is enough to drop a fix", fix("Use concat(sort=False) instead.") == (None, True))
check("a fix may use names from the code line itself", fix("Replace append(extra) with concat() as the note says.") == ("Replace append(extra) with concat() as the note says.", False))
check("'e.g.' in a fix is not mistaken for code", fix("Use concat(), e.g. as the note says.") == ("Use concat(), e.g. as the note says.", False))
check("version numbers in a fix are not mistaken for code", fix("Removed in 2.0.0, use concat().") == ("Removed in 2.0.0, use concat().", False))
check("no fix stays no fix", fix(None) == (None, False) and fix("  ") == (None, False))

# ---- one finding, with fake models ------------------------------------------------------
model = FakeModel(answer_json())
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("a good answer: verdict, fix and citation kept, one call, no flags",
      finding["verdict"] == "affected" and finding["suggested_fix"] == "Use concat() instead." and len(finding["citations"]) == 1
      and finding["flags"] == [] and len(model.calls) == 1)
check("each citation carries the note number, the real chunk id and the quote",
      finding["citations"] == [{"note": 1, "chunk_id": APPEND["chunk_id"], "quote": QUOTE}])
check("the model is given the system prompt, the user prompt and a schema whose citations use 'note'",
      model.calls[0]["system"] == explain.SYSTEM_PROMPT and "Note 1" in model.calls[0]["user"]
      and "note" in json.dumps(model.calls[0]["schema"]) and "chunk_id" not in json.dumps(model.calls[0]["schema"]))
check("the finding carries the usage, status and matched notes for the report",
      finding["usage"] == APPEND_USAGE and finding["status"] == "documented_change" and finding["matches"] == notes)
check("the trace records the attempt: its raw reply and no problems", len(finding["trace"]) == 1 and finding["trace"][0]["attempt"] == 1
      and finding["trace"][0]["problems"] == [] and json.loads(finding["trace"][0]["reply"])["verdict"] == "affected")

liar = answer_json(citations=((1, "DataFrame.append() will be removed in version 3.0"),))
model = FakeModel(liar, answer_json())
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("a lying answer, then an honest one: retried once and accepted", len(model.calls) == 2 and finding["verdict"] == "affected" and finding["flags"] == ["needed_retry"])
check("the retry message repeats the notes, quotes the rejected words and lists the valid note numbers",
      model.calls[1]["user"].startswith(model.calls[0]["user"]) and "rejected" in model.calls[1]["user"]
      and "DataFrame.append() will be removed in version 3.0" in model.calls[1]["user"] and "valid note numbers are: 1" in model.calls[1]["user"])
check("the trace shows both attempts, the first with its problem", len(finding["trace"]) == 2 and finding["trace"][0]["problems"] and finding["trace"][1]["problems"] == [])

model = FakeModel(liar, liar)
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("two lying answers: exactly 2 calls, downgraded to 'unsure', flagged, no citations shown",
      len(model.calls) == 2 and finding["verdict"] == "unsure" and finding["flags"] == ["citation_check_failed"] and finding["citations"] == [])
check("...and the explanation says it could not be verified, and the trace explains why", "could not be verified" in finding["explanation"] and len(finding["trace"]) == 2)

model = FakeModel(RuntimeError("connection refused"), ConnectionError("still down"))
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("a model that is down twice: 'unsure' with a model_error flag, no crash, trace keeps the error",
      finding["verdict"] == "unsure" and finding["flags"] == ["model_error"] and len(model.calls) == 2 and "connection refused" in finding["trace"][0]["problems"][0])

model = FakeModel("I think it is affected.", answer_json())
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("prose instead of JSON, then valid JSON: retried and accepted", finding["verdict"] == "affected" and finding["flags"] == ["needed_retry"])

OLD_STYLE = json.dumps({"verdict": "affected", "explanation": "x", "suggested_fix": None,
                        "citations": [{"chunk_id": APPEND["chunk_id"], "quote": QUOTE}]})
model = FakeModel(OLD_STYLE, OLD_STYLE)
finding = explain.explain_usage(APPEND_RESULT, model, "pandas")
check("a reply that cites by long id (the old shape) is refused cleanly, not crashed on", finding["verdict"] == "unsure" and finding["flags"] == ["model_error"])

nothing = {"usage": usage("tolist"), "status": "no_documented_change", "confidence": "high", "matches": []}
model = FakeModel(answer_json())
finding = explain.explain_usage(nothing, model, "pandas")
check("no documented change: the model is never called", len(model.calls) == 0 and finding["verdict"] == "no_documented_change" and finding["trace"] == [])

low = match.match_usage(usage("append", resolved=False), [candidate(APPEND)])
finding = explain.explain_usage(low, FakeModel(answer_json()), "pandas")
check("unconfirmed object: 'affected' is shown as 'unsure', flagged, with the reason in the text",
      finding["verdict"] == "unsure" and "capped_unconfirmed_type" in finding["flags"] and "could not confirm" in finding["explanation"] and finding["confidence"] == "low")
finding = explain.explain_usage(low, FakeModel(answer_json(verdict="unsure", citations=(), fix=None)), "pandas")
check("unconfirmed object, model already unsure: the reason is still stated, no cap flag",
      finding["verdict"] == "unsure" and "could not confirm" in finding["explanation"] and "capped_unconfirmed_type" not in finding["flags"])
check("a confirmed object keeps 'affected'", explain.explain_usage(APPEND_RESULT, FakeModel(answer_json()), "pandas")["verdict"] == "affected")

finding = explain.explain_usage(APPEND_RESULT, FakeModel(answer_json(fix="use df.concat([extra], ignore_index=True)")), "pandas")
check("the real invented fix is dropped, flagged, and the verdict stays", finding["suggested_fix"] is None and "fix_dropped" in finding["flags"] and finding["verdict"] == "affected")

TO_DICT_ANSWER = answer_json(verdict="not_affected", explanation="The note is about the orient argument, which this call does not pass.",
                             fix=None, citations=((1, "Disallow passing abbreviations for orient"),))
to_dict_result = match.match_usage(usage("to_dict", snippet="rows = df.to_dict()"), [candidate(TO_DICT)])
finding = explain.explain_usage(to_dict_result, FakeModel(TO_DICT_ANSWER), "pandas")
check("a real 'review' case: 'not_affected' with a valid citation is accepted", to_dict_result["status"] == "review" and finding["verdict"] == "not_affected" and finding["flags"] == [])

# ---- many usages -------------------------------------------------------------------------------
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
