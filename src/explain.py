"""
Dependency Migration Assistant — Answer generator

For each usage the matcher flagged, an AI model reads the code line next to the matched
changelog notes and decides whether THIS call is affected. The model never gets the last
word: everything it says is checked here, in code.

  - the notes are numbered (Note 1, Note 2, ...) and the model cites them by number
  - its answer must be valid JSON in a fixed shape (ModelAnswer)
  - every citation must name a note we gave it, and its quote must appear word for word in the
    Text of that note (ignoring spaces); "affected" needs at least one valid citation
  - the explanation is at most 3 sentences
  - a suggested fix is kept only if every code-like piece of it (call names, dotted names such as
    df.concat, argument names such as ignore_index) appears verbatim in a note or the code line
  - one retry with the reasons if a check fails; after that the finding becomes "unsure"
  - if the scanner could not confirm the object is from the library, "affected" is shown as
    "unsure" and the explanation says why
  - D29: a usage whose notes only share a NAME with the call (status "review") can never be shown as "affected";
    only the matcher's hard evidence (status "documented_change") can confirm a break
  - D30: usages with only name-level evidence (status "review") are not sent to the model at all; they are reported
    as "needs_review" with the matched notes. Only hard evidence ("documented_change") reaches the model
  - every finding keeps a trace of each attempt (the model's raw reply and the problems we found) and the
    verdict the model itself stated (raw_verdict), before any cap

The model is a function passed in:  llm_call(system: str, user: str, schema: dict) -> str
so tests can use fake models and a different model can be swapped in later.
"""

import logging
import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MIN_QUOTE_WORDS = 3
MAX_SENTENCES = 3
CAP_LOW_CONFIDENCE = True  # an unconfirmed object can be "unsure" at most, never "affected"
CAP_REVIEW_STATUS = True   # D29: no "affected" unless the matcher found hard evidence
MODEL_JUDGES_REVIEW = False  # D30: review usages are not sent to the model (True = ask anyway, with the D29 cap; for experiments)
UNCONFIRMED_TEXT = " (The scanner could not confirm this object is from {library}, so treat this as a possibility.)"
REVIEW_CAP_TEXT = ("The changelog has {n} note(s) that mention this function, but none could be matched to this exact call, "
                   "so it needs a manual check.")
TRACE_REPLY_CHARS = 2000

DOTTED_NAME = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")   # df.concat
CALLED_NAME = re.compile(r"\b([A-Za-z_]\w*)\s*\(")                # concat(
ARGUMENT_NAME = re.compile(r"\b([A-Za-z_]\w*)\s*=(?!=)")          # ignore_index=
ALLOWED_DOTTED = {"e.g", "i.e"}

SYSTEM_PROMPT = """You check whether ONE line of Python code is affected by documented library changes.
You are given the code line and numbered changelog notes. Use ONLY these notes. Do not use your own knowledge of the library.

Answer with JSON only, in exactly this shape:
{"verdict": "affected" | "not_affected" | "unsure",
 "explanation": "at most 3 short sentences, plain English",
 "suggested_fix": "text, or null",
 "citations": [{"note": 1, "quote": "5 to 15 words copied exactly from that note's Text"}]}

Rules:
- "affected": a note says something this exact code uses was removed, renamed or changed. Cite it.
- "not_affected": the notes concern something this code does not use (for example an argument the call does not pass).
- "unsure": the notes do not settle it. When in doubt, answer "unsure".
- A removed or changed ARGUMENT only affects the call if the call passes that argument.
- "note" is the note's number (1, 2, 3...). Quote only from the Text line of that note, never from its Source line. Copy the words exactly; never reword or invent a quote.
- "suggested_fix": only if a note itself states what to use instead (for example "use concat() instead"); copy its wording. Never invent function names or arguments. Otherwise null.
- If it is not confirmed that the object comes from the library, say so in the explanation and do not answer "affected"."""


class Citation(BaseModel):
    note: int = Field(ge=1)
    quote: str


class ModelAnswer(BaseModel):
    verdict: Literal["affected", "not_affected", "unsure"]
    explanation: str = Field(min_length=1)
    suggested_fix: str | None = None
    citations: list[Citation] = Field(default_factory=list)


# ---- prompt ----------------------------------------------------------------

def build_user_prompt(match_result: dict, library: str = "the library") -> str:
    """What the model is shown: the code line, the keyword arguments, and the numbered notes. Nothing else."""
    usage = match_result["usage"]
    keywords = ", ".join(usage.get("keywords", [])) or "none"
    if usage.get("resolved", True):
        confirmed = "yes"
    else:
        confirmed = f"NO - the scanner could not confirm this object is from {library}"

    lines = [
        f"Code: {usage.get('file', '?')}:{usage.get('line', '?')}",
        f"Line: {usage.get('snippet', '')}",
        f"Function or attribute: {usage.get('function', '')}",
        f"Keyword arguments passed: {keywords}",
        f"Object confirmed to be from {library}: {confirmed}",
        "",
        "Notes:",
    ]
    for number, match in enumerate(match_result["matches"], start=1):
        chunk = match["chunk"]
        where = chunk["section"] + (f" > {chunk['subsection']}" if chunk.get("subsection") else "")
        lines.append(f"Note {number}")
        lines.append(f"Source: version {chunk.get('version') or 'n/a'}, {where}")
        lines.append(f"Text: {chunk['text']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---- checking the model's answer ---------------------------------------------

def squash(text: str) -> str:
    """Remove all whitespace, so spacing artifacts like 'append() , DataFrame' cannot cause false rejections."""
    return re.sub(r"\s+", "", text)


def sentence_count(text: str) -> int:
    """Sentences, where dots inside names such as DataFrame.append() do not count."""
    return len([part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part])


def parse_answer(raw: str) -> ModelAnswer:
    """Parse the model's reply as JSON in the required shape (a ```json fence around it is tolerated)."""
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    return ModelAnswer.model_validate_json(text)


def valid_numbers(notes: list) -> str:
    return ", ".join(str(number) for number in range(1, len(notes) + 1))


def check_answer(answer: ModelAnswer, notes: list) -> list:
    """Reasons the answer must be rejected (an empty list means it passed)."""
    problems, valid = [], 0

    for citation in answer.citations:
        if not 1 <= citation.note <= len(notes):
            problems.append(f"note {citation.note} does not exist; the valid note numbers are {valid_numbers(notes)}")
            continue
        note_text = notes[citation.note - 1]["chunk"]["text"]
        if len(citation.quote.split()) < MIN_QUOTE_WORDS:
            problems.append(f"a quote must have at least {MIN_QUOTE_WORDS} words: {citation.quote!r}")
        elif squash(citation.quote) not in squash(note_text):
            problems.append(f"this quote is not word for word in the Text of note {citation.note}: {citation.quote!r}")
        else:
            valid += 1

    if answer.verdict == "affected" and valid == 0:
        problems.append("an 'affected' verdict needs at least one valid citation")
    if sentence_count(answer.explanation) > MAX_SENTENCES:
        problems.append(f"the explanation has more than {MAX_SENTENCES} sentences")
    return problems


def code_tokens(text: str) -> set:
    """The code-like pieces of a text: dotted names, called names and argument names."""
    tokens = set(DOTTED_NAME.findall(text)) - ALLOWED_DOTTED
    tokens.update(CALLED_NAME.findall(text))
    tokens.update(ARGUMENT_NAME.findall(text))
    return tokens


def ground_fix(fix, notes: list, usage: dict):
    """(fix to show, was it dropped). Every code-like piece of the fix must appear verbatim in a note or the code line."""
    if not fix or not fix.strip():
        return None, False
    known = " ".join([note["chunk"]["text"] for note in notes] + [usage.get("snippet", "")])
    unknown = [token for token in code_tokens(fix) if not re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", known)]
    return (None, True) if unknown else (fix.strip(), False)


# ---- one finding per usage ---------------------------------------------------

def explain_usage(match_result: dict, llm_call, library: str = "the library") -> dict:
    """
    Turn one matched usage into a finding:
    {"usage", "status", "confidence", "matches", "verdict", "explanation", "suggested_fix", "citations", "flags", "trace", "raw_verdict"}.
    """
    usage, notes = match_result["usage"], match_result["matches"]
    base = {"usage": usage, "status": match_result["status"], "confidence": match_result["confidence"], "matches": notes}

    if match_result["status"] == "no_documented_change" or not notes:
        return {**base, "verdict": "no_documented_change", "explanation": "No documented change in the changelog mentions this.",
                "suggested_fix": None, "citations": [], "flags": [], "trace": [], "raw_verdict": None}

    if match_result["status"] == "review" and not MODEL_JUDGES_REVIEW:
        # D30: the notes only share a name with this call. A small model can neither confirm nor safely rule out a
        # break from that, so it is not asked at all; the matched notes go to a person.
        text = REVIEW_CAP_TEXT.format(n=len(notes))
        if match_result["confidence"] == "low":
            text += UNCONFIRMED_TEXT.format(library=library)
        return {**base, "verdict": "needs_review", "explanation": text, "suggested_fix": None, "citations": [],
                "flags": [], "trace": [], "raw_verdict": None}

    user = build_user_prompt(match_result, library)
    schema = ModelAnswer.model_json_schema()
    problems, answer, failure, attempts, trace, last_stated = [], None, None, 0, [], None

    for attempt in (1, 2):
        attempts = attempt
        message = user
        if problems:
            message += ("\nYour previous answer was rejected for these reasons:\n- " + "\n- ".join(problems)
                        + f"\nThe valid note numbers are: {valid_numbers(notes)}.\nAnswer again and fix them.\n")
        try:
            raw = llm_call(SYSTEM_PROMPT, message, schema)
        except Exception as error:  # model unreachable, timed out, not downloaded...
            problems, failure = [f"the model call failed ({type(error).__name__})"], "model_error"
            trace.append({"attempt": attempt, "reply": None, "problems": [f"the model call failed: {type(error).__name__}: {str(error)[:200]}"]})
            continue
        try:
            candidate = parse_answer(raw)
        except Exception as error:  # not JSON, or the wrong shape
            problems, failure = ["the answer was not valid JSON in the required shape"], "model_error"
            trace.append({"attempt": attempt, "reply": str(raw)[:TRACE_REPLY_CHARS], "problems": problems})
            continue
        last_stated = candidate.verdict
        problems = check_answer(candidate, notes)
        trace.append({"attempt": attempt, "reply": str(raw)[:TRACE_REPLY_CHARS], "problems": list(problems)})
        if not problems:
            answer = candidate
            break
        failure = "citation_check_failed"

    if answer is None:
        reason = ("The AI model did not return a usable answer" if failure == "model_error"
                  else "The AI's answer could not be verified against the changelog notes")
        return {**base, "verdict": "unsure", "explanation": f"{reason}, so no verdict is given. The matched notes are attached for manual review.",
                "suggested_fix": None, "citations": [], "flags": [failure], "trace": trace, "raw_verdict": last_stated}

    flags = ["needed_retry"] if attempts == 2 else []
    verdict, explanation = answer.verdict, answer.explanation.strip()
    citations = [{"note": c.note, "chunk_id": notes[c.note - 1]["chunk"]["chunk_id"], "quote": c.quote} for c in answer.citations]
    fix, dropped = ground_fix(answer.suggested_fix, notes, usage)

    if CAP_REVIEW_STATUS and match_result["status"] == "review" and verdict == "affected":
        # D29: the notes only share a name with this call; a small model must not turn that into a confirmed break.
        # Its words (and its citation, which can be a real quote that does not apply) stay in the trace only.
        verdict, fix, dropped, citations = "unsure", None, False, []
        explanation = REVIEW_CAP_TEXT.format(n=len(notes))
        flags.append("capped_review_status")
    if dropped:
        flags.append("fix_dropped")

    if match_result["confidence"] == "low":
        if verdict == "affected" and CAP_LOW_CONFIDENCE:
            verdict = "unsure"
            flags.append("capped_unconfirmed_type")
        explanation += UNCONFIRMED_TEXT.format(library=library)

    return {**base, "verdict": verdict, "explanation": explanation, "suggested_fix": fix, "citations": citations,
            "flags": flags, "trace": trace, "raw_verdict": last_stated}


def explain_usages(match_results: list, llm_call, library: str = "the library") -> list:
    """One finding per matched usage, in order. Usages with no documented change never reach the model."""
    findings = [explain_usage(result, llm_call, library) for result in match_results]
    counts = Counter(finding["verdict"] for finding in findings)
    logger.info("Explained %d usage(s): %s", len(findings), ", ".join(f"{n} {verdict}" for verdict, n in sorted(counts.items())))
    return findings
