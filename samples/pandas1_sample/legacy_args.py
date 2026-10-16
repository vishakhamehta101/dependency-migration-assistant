import pandas as pd


def legacy(path):
    data = pd.read_csv(path, error_bad_lines=False)
    pieces = data.groupby("kind", squeeze=True)
    data.to_excel("out.xlsx", encoding="utf-8")
    return pieces
