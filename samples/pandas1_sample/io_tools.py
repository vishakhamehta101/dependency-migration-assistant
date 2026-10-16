import pandas as pd


def export(df, path):
    with pd.ExcelWriter(path) as writer:
        df.to_excel(writer)
        writer.save()
    pd.options.mode.chained_assignment = None
    idx = pd.Int64Index([1, 2])
    return isinstance(idx, pd.Int64Index)
