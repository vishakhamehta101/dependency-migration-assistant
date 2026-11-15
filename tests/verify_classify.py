"""Checks for the note-type classifier. Run from the project root: python3 tests/verify_classify.py
Needs a trained model first: python3 src/classify.py train"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "src")
import classify

ok_all = True

def check(label, passed, detail=""):
    global ok_all
    ok_all &= bool(passed)
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))

# --- which notes are used for training -------------------------------------------------
chunks = classify.load_chunks()
labelled, unlabelled = classify.split_notes(chunks)
check("training notes come from the changelog only (migration-guide chunks are explanations, not notes)",
      labelled and all(c["source_type"] == "changelog" for c in labelled + unlabelled))
check("no training note has the 'uncategorized' label", all(c["category"] != "uncategorized" for c in labelled))
check("every uncategorized changelog note goes to the unlabelled list, none are lost",
      len(labelled) + len(unlabelled) == sum(1 for c in chunks if c["source_type"] == "changelog"),
      f"{len(labelled)} labelled + {len(unlabelled)} unlabelled")
check("all six categories are present in the training data",
      {c["category"] for c in labelled} == {"breaking_change", "bug_fix", "deprecation", "enhancement", "performance", "regression_fix"})

# --- the model itself, on a tiny made-up set (fast and does not depend on the saved file) -
toy = [("Removed the old function, it no longer exists", "breaking_change"),
       ("Removed the old argument, it no longer exists", "breaking_change"),
       ("Fixed a bug that crashed on empty input", "bug_fix"),
       ("Fixed a bug that returned wrong values", "bug_fix"),
       ("Performance improvement when sorting large data", "performance"),
       ("Performance improvement when grouping large data", "performance")]
toy_model = classify.build_model()
toy_model.fit([t for t, _ in toy], [c for _, c in toy])
check("the model learns a clear pattern on made-up notes",
      toy_model.predict(["Removed the old keyword, it no longer exists"])[0] == "breaking_change"
      and toy_model.predict(["Fixed a bug that crashed on large input"])[0] == "bug_fix")
check("the model reads only the note text (one text in, one category out)",
      len(toy_model.predict(["Performance improvement when merging data"])) == 1)

# --- the saved model ----------------------------------------------------------------------
model = classify.load_model()
result = classify.predict_note("Removed deprecated Series.append(), use concat() instead", model)
check("a prediction has a category, a confidence and the scores of every class",
      set(result) == {"category", "confidence", "needs_review", "scores"} and len(result["scores"]) == 6)
check("the predicted category is one of the six real categories", result["category"] in result["scores"])
check("the scores add up to 1 and the confidence is the highest score",
      abs(sum(result["scores"].values()) - 1) < 0.01 and result["confidence"] == max(result["scores"].values()))
check("needs_review is true exactly when confidence is below the threshold",
      result["needs_review"] == (result["confidence"] < classify.REVIEW_CONFIDENCE))

# --- a missing model gives a clear message, not a crash trace ---------------------------------
real_path = classify.MODEL_PATH
with tempfile.TemporaryDirectory() as d:
    classify.MODEL_PATH = Path(d) / "nope.joblib"
    try:
        classify.load_model()
        message = ""
    except SystemExit as e:
        message = str(e)
    classify.MODEL_PATH = real_path
check("a missing model file says how to train one", "classify.py train" in message)

# --- the saved accuracy report -----------------------------------------------------------
metrics = json.loads(classify.METRICS_PATH.read_text())
check("the saved report covers six classes and lists a confusion matrix of the same size",
      len(metrics["classes"]) == 6 and len(metrics["confusion_matrix"]) == 6)
check("cross-validated accuracy has not dropped below the 0.85 floor", metrics["cv_accuracy"] >= 0.85,
      f"{metrics['cv_accuracy']}")

print("\nALL CHECKS PASSED:", ok_all)
sys.exit(0 if ok_all else 1)
