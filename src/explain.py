"""
Dependency Migration Assistant — Answer generator

For each usage the matcher flagged, an AI model reads the code line next to the matched
changelog notes and decides whether THIS call is affected. The model never gets the last
word: everything it says is checked here, in code.

  - its answer must be valid JSON in a fixed shape (ModelAnswer)
  - every citation must name a note we gave it, and its quote must appear word for word in
    that note (ignoring spaces); "affected" needs at least one valid citation
  - the explanation is at most 3 sentences
  - a suggested fix is kept only if every function it calls appears in a provided note or in
    the code line
  - one retry with the reasons if a check fails; after that the finding becomes "unsure"
  - if the scanner could not confirm the object is from the library, "affected" is shown as
    "unsure" and the explanation says why

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
CALLED_NAME = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

SYSTEM_PROMPT = """You check whether ONE line of Python code is affected by documented library changes.
You are given the code line and numbered changelog notes. Use ONLY these notes. Do not use your own knowledge of the library.

Answer with JSON only, in exactly this shape:
{"verdict": "affected" | "not_affected" | "unsure",
 "explanation": "at most 3 short sentences, plain English",
 "suggested_fix": "text, or null",
 "citations": [{"chunk_id": "<id of a note>", "quote": "<words copied exactly from that note>"}]}

Rules:
- "affected": a note says something this exact code uses was removed, renamed or changed. Cite it.
- "not_affected": the notes concern something this code does not use (for example an argument the call does not pass).
- "unsure": the notes do not settle it. When in doubt, answer "unsure".
- A removed or changed ARGUMENT only affects the call if the call passes that argument.
- Copy quotes exactly, word for word, from the note text. Never invent or reword a quote.
- "suggested_fix": only if a note itself states what to use instead (for example "use concat() instead"). Otherwise null.
- If it is not confirmed that the object comes from the library, say so in the explanation and do not answer "affected"."""


class Citation(BaseModel):
    chunk_id: str
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
    for match in match_result["matches"]:
        chunk = match["chunk"]
        where = chunk["section"] + (f" > {chunk['subsection']}" if chunk.get("subsection") else "")
        lines.append(f"[{chunk['chunk_id']}] version {chunk.get('version') or 'n/a'} | {where}")
        lines.append(chunk["text"])
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


def check_answer(answer: ModelAnswer, notes: list) -> list:
    """Reasons the answer must be rejected (an empty list means it passed)."""
    text_by_id = {note["chunk"]["chunk_id"]: note["chunk"]["text"] for note in notes}
    problems, valid = [], 0

    for citation in answer.citations:
        note_text = text_by_id.get(citation.chunk_id)
        if note_text is None:
            problems.append(f"the citation names a note that was not provided: {citation.chunk_id!r}")
        elif len(citation.quote.split()) < MIN_QUOTE_WORDS:
            problems.append(f"a quote must have at least {MIN_QUOTE_WORDS} words: {citation.quote!r}")
        elif squash(citation.quote) not in squash(note_text):
            problems.append(f"this quote is not word for word in note {citation.chunk_id}: {citation.quote!r}")
        else:
            valid += 1

    if answer.verdict == "affected" and valid == 0:
        problems.append("an 'affected' verdict needs at least one valid citation")
    if sentence_count(answer.explanation) > MAX_SENTENCES:
        problems.append(f"the explanation has more than {MAX_SENTENCES} sentences")
    return problems


def ground_fix(fix, notes: list, usage: dict):
    """(fix to show, was it dropped). Every function the fix calls must appear in a note or in the code line."""
    if not fix or not fix.strip():
        return None, False
    known = " ".join([note["chunk"]["text"] for note in notes] + [usage.get("snippet", "")])
    unknown = [name for name in CALLED_NAME.findall(fix)
               if not re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", known)]
    return (None, True) if unknown else (fix.strip(), False)


# ---- one finding per usage ---------------------------------------------------

def explain_usage(match_result: dict, llm_call, library: str = "the library") -> dict:
    """
    Turn one matched usage into a finding:
    {"usage", "status", "confidence", "matches", "verdict", "explanation", "suggested_fix", "citations", "flags"}.
    """
    usage, notes = match_result["usage"], match_result["matches"]
    base = {"usage": usage, "status": match_result["status"], "confidence": match_result["confidence"], "matches": notes}

    if match_result["status"] == "no_documented_change" or not notes:
        return {**base, "verdict": "no_documented_change", "explanation": "No documented change in the changelog mentions this.",
                "suggested_fix": None, "citations": [], "flags": []}

    user = build_user_prompt(match_result, library)
    schema = ModelAnswer.model_json_schema()
    problems, answer, failure, attempts = [], None, None, 0

    for attempt in (1, 2):
        attempts = attempt
        message = user
        if problems:
            message += "\nYour previous answer was rejected for these reasons:\n- " + "\n- ".join(problems) + "\nAnswer again and fix them.\n"
        try:
            candidate = parse_answer(llm_call(SYSTEM_PROMPT, message, schema))
        except Exception as error:  # model unreachable, not JSON, or the wrong shape
            problems, failure = [f"the answer was not valid JSON in the required shape ({type(error).__name__})"], "model_error"
            continue
        problems = check_answer(candidate, notes)
        if not problems:
            answer = candidate
            break
        failure = "citation_check_failed"

    if answer is None:
        reason = ("The AI model did not return a usable answer" if failure == "model_error"
                  else "The AI's answer could not be verified against the changelog notes")
        return {**base, "verdict": "unsure", "explanation": f"{reason}, so no verdict is given. The matched notes are attached for manual review.",
                "suggested_fix": None, "citations": [], "flags": [failure]}

    flags = ["needed_retry"] if attempts == 2 else []
    fix, dropped = ground_fix(answer.suggested_fix, notes, usage)
    if dropped:
        flags.append("fix_dropped")

    verdict, explanation = answer.verdict, answer.explanation.strip()
    if match_result["confidence"] == "low":
        if verdict == "affected" and CAP_LOW_CONFIDENCE:
            verdict = "unsure"
            flags.append("capped_unconfirmed_type")
        explanation += f" (The scanner could not confirm this object is from {library}, so treat this as a possibility.)"

    return {**base, "verdict": verdict, "explanation": explanation, "suggested_fix": fix,
            "citations": [citation.model_dump() for citation in answer.citations], "flags": flags}


def explain_usages(match_results: list, llm_call, library: str = "the library") -> list:
    """One finding per matched usage, in order. Usages with no documented change never reach the model."""
    findings = [explain_usage(result, llm_call, library) for result in match_results]
    counts = Counter(finding["verdict"] for finding in findings)
    logger.info("Explained %d usage(s): %s", len(findings), ", ".join(f"{n} {verdict}" for verdict, n in sorted(counts.items())))
    return findings
