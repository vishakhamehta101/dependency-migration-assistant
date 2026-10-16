import pandas as pd

LOG = []


def load(path):
    df = pd.read_csv(path)
    extra = pd.DataFrame({"a": [1]})
    df = df.append(extra)
    LOG.append("loaded")
    return df
