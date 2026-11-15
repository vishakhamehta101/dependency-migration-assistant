"""
Dependency Migration Assistant — Note-type classifier (System 9, our own trained model)

Predicts a changelog note's category (breaking_change, deprecation, bug_fix, enhancement,
performance, regression_fix) from the note's OWN text. It never sees the section heading,
because the training labels come from the heading: if the heading were an input, the model
would just copy the label and learn nothing.

Two jobs in the pipeline:
  1. label notes whose heading gave no category ("uncategorized"), with a confidence score
     and a "needs review" flag when the model is not sure
  2. flag notes whose text strongly disagrees with the category their heading gave them,
     so a human can check them

Model: TF-IDF (word 1-2 grams) -> Logistic Regression (class_weight="balanced").

Usage:
  python3 src/classify.py train
  python3 src/classify.py predict "DataFrame.append has been removed, use concat instead"
"""

import json
import logging
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_PATH = ROOT / "data" / "chunks" / "pandas_chunks.json"
MODEL_PATH = ROOT / "models" / "note_classifier.joblib"
METRICS_PATH = ROOT / "reports" / "classifier_metrics.json"

RANDOM_STATE = 42
FOLDS = 5
REVIEW_CONFIDENCE = 0.50   # below this the prediction is only a suggestion: "needs review"
DISAGREEMENTS_TO_SHOW = 8


def load_chunks(path: Path = CHUNKS_PATH) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def split_notes(chunks: list) -> tuple:
    """Changelog notes only (migration-guide chunks are explanations, not notes).
    Returns (labelled, unlabelled): labelled have a real category, unlabelled are 'uncategorized'."""
    notes = [c for c in chunks if c["source_type"] == "changelog"]
    labelled = [c for c in notes if c["category"] != "uncategorized"]
    unlabelled = [c for c in notes if c["category"] == "uncategorized"]
    return labelled, unlabelled


def build_model():
    return make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
        LogisticRegression(max_iter=2000, class_weight="balanced"),
    )


def excerpt(text: str, width: int = 110) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= width else one_line[: width - 3] + "..."


def train() -> None:
    chunks = load_chunks()
    labelled, unlabelled = split_notes(chunks)
    texts = [c["text"] for c in labelled]
    labels = [c["category"] for c in labelled]

    print(f"\nTraining notes: {len(labelled)}  |  uncategorized changelog notes: {len(unlabelled)}")
    for name in sorted(set(labels)):
        print(f"  {name:16s} {labels.count(name)}")

    # 1. Honest accuracy: 5-fold cross-validation (every note is predicted by a model that never saw it)
    model = build_model()
    cv = StratifiedKFold(n_splits=FOLDS, shuffle=True, random_state=RANDOM_STATE)
    probs = cross_val_predict(model, texts, labels, cv=cv, method="predict_proba")
    classes = sorted(set(labels))
    predicted = [classes[i] for i in probs.argmax(axis=1)]

    accuracy = accuracy_score(labels, predicted)
    report = classification_report(labels, predicted, digits=2, output_dict=True, zero_division=0)
    matrix = confusion_matrix(labels, predicted, labels=classes)

    print(f"\n=== {FOLDS}-fold cross-validated accuracy: {accuracy:.3f} ===\n")
    print(classification_report(labels, predicted, digits=2, zero_division=0))
    print("Confusion matrix (rows = heading label, columns = model prediction):")
    print("labels:", classes)
    print(matrix)

    # 2. Notes whose text disagrees with their heading-assigned category
    wrong = [i for i, (p, t) in enumerate(zip(predicted, labels)) if p != t]
    wrong.sort(key=lambda i: -probs[i].max())
    print(f"\n=== Most confident disagreements ({len(wrong)} total; showing {DISAGREEMENTS_TO_SHOW}) ===")
    for i in wrong[:DISAGREEMENTS_TO_SHOW]:
        print(f"  heading says {labels[i]:15s} model says {predicted[i]:15s} ({probs[i].max():.2f})")
        print(f"      {excerpt(texts[i])}")

    # 3. Final model: fit on every labelled note, then label the uncategorized ones
    model.fit(texts, labels)
    print(f"\n=== Uncategorized changelog notes (needs review if confidence < {REVIEW_CONFIDENCE}) ===")
    for c in unlabelled:
        p = model.predict_proba([c["text"]])[0]
        best = p.argmax()
        flag = "needs review" if p[best] < REVIEW_CONFIDENCE else "accepted"
        print(f"  {model.classes_[best]:15s} {p[best]:.2f}  {flag:12s} {excerpt(c['text'], 80)}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "training_notes": len(labelled),
                "cv_folds": FOLDS,
                "cv_accuracy": round(accuracy, 4),
                "classes": classes,
                "per_class": report,
                "confusion_matrix": matrix.tolist(),
                "disagreements": len(wrong),
            },
            f,
            indent=2,
        )
    print(f"\nSaved model   -> {MODEL_PATH.relative_to(ROOT)}")
    print(f"Saved metrics -> {METRICS_PATH.relative_to(ROOT)}")


def load_model():
    if not MODEL_PATH.exists():
        raise SystemExit("No saved model yet. Run: python3 src/classify.py train")
    return joblib.load(MODEL_PATH)


def predict_note(text: str, model=None) -> dict:
    """Predict one note. Returns category, confidence, needs_review and all class scores."""
    model = model or load_model()
    p = model.predict_proba([text])[0]
    best = int(np.argmax(p))
    return {
        "category": str(model.classes_[best]),
        "confidence": round(float(p[best]), 3),
        "needs_review": bool(p[best] < REVIEW_CONFIDENCE),
        "scores": {str(c): round(float(s), 3) for c, s in zip(model.classes_, p)},
    }


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "train":
        train()
    elif len(sys.argv) >= 3 and sys.argv[1] == "predict":
        print(json.dumps(predict_note(" ".join(sys.argv[2:])), indent=2))
    else:
        print(__doc__)
