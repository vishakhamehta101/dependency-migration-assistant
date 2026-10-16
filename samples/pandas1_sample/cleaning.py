from pandas import DataFrame, read_csv as rc
import pandas.api.types as pat


def clean(frame: DataFrame, path):
    raw = rc(path)
    cols = raw.columns.tolist()
    cols.append("new")
    counts = frame.groupby("kind").agg("sum")
    ok = pat.is_scalar(3)
    return raw, counts, ok


def to_rows(df: DataFrame):
    rows = df.to_dict()
    rows.append(1)
    return rows
