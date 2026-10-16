import pandas


def count(path):
    data = pandas.read_csv(path)
    return len(data.index)
