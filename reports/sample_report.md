# Migration report: pandas → 2.0.0

Scanned: `samples/pandas1_sample` · 24 usage(s) of pandas found · generated 2026-10-17

## Summary

| Result | Usages | What it means |
|---|---:|---|
| **AFFECTED** | 7 | A documented change matches this exact call, and the quote was checked word for word |
| **NEEDS REVIEW** | 10 | The changelog mentions this function, but no note could be tied to this exact call: check by hand |
| **UNSURE** | 3 | Could not be confirmed (the object may not be from the library, or the AI's answer could not be verified) |
| **NO DOCUMENTED CHANGE** | 4 | Nothing in the ingested changelog mentions it |
| **Total** | 24 | |

## Confirmed breaks

| Location | Code | What changed (from the changelog) | Fix suggested by the changelog |
|---|---|---|---|
| `io_tools.py:7` | `writer.save()` | Removed deprecated methods ExcelWriter.write_cells() , ExcelWriter.save() , ExcelWriter.cur_sheet() , ExcelWriter.handles() , ExcelWriter.p… | see the note |
| `io_tools.py:9` | `idx = pd.Int64Index([1, 2])` | Removed Int64Index , UInt64Index and Float64Index . See also here for more information ( GH 42717 ) | see the note |
| `io_tools.py:10` | `return isinstance(idx, pd.Int64Index)` | Removed Int64Index , UInt64Index and Float64Index . See also here for more information ( GH 42717 ) | see the note |
| `legacy_args.py:5` | `data = pd.read_csv(path, error_bad_lines=False)` | Removed arguments prefix , squeeze , error_bad_lines and warn_bad_lines from read_csv() ( GH 40413 , GH 43427 ) | see the note |
| `legacy_args.py:6` | `pieces = data.groupby("kind", squeeze=True)` | Remove argument squeeze from DataFrame.groupby() and Series.groupby() ( GH 32380 ) | see the note |
| `legacy_args.py:7` | `data.to_excel("out.xlsx", encoding="utf-8")` | Removed arguments verbose and encoding from DataFrame.to_excel() and Series.to_excel() ( GH 47912 ) | see the note |
| `loader.py:9` | `df = df.append(extra)` | Removed deprecated Series.append() , DataFrame.append() , use concat() instead ( GH 35407 ) | see the note |

## Details by file

### cleaning.py

#### NEEDS REVIEW · line 5 · `DataFrame`

```python
def clean(frame: DataFrame, path):
```

The changelog has 5 note(s) that mention this function, but none could be matched to this exact call, so it needs a manual check.

**Matched notes** (read these to decide)

- Deprecated DataFrame.backfill() in favor of DataFrame.bfill() ( GH 33396 ) — pandas 2.0.0, *Deprecations* ([source][1])
- Enforced disallowing value_name argument in DataFrame.melt() to match an element in the DataFrame columns ( GH 35003 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Deprecated DataFrame.pad() in favor of DataFrame.ffill() ( GH 33396 ) — pandas 2.0.0, *Deprecations* ([source][1])
- Removed deprecated argument col_space in DataFrame.to_latex() ( GH 47970 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Removed errors keyword from DataFrame.where() , Series.where() , DataFrame.mask() and Series.mask() ( GH 47728 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### NEEDS REVIEW · line 6 · `read_csv`

```python
raw = rc(path)
```

The changelog has 5 note(s) that mention this function, but none could be matched to this exact call, so it needs a manual check.

**Matched notes** (read these to decide)

- Disallow passing non-keyword arguments to read_csv() except filepath_or_buffer ( GH 41485 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Remove keyword mangle_dupe_cols from read_csv() and read_table() ( GH 48137 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Added "None" to default na_values in read_csv() ( GH 50286 ) — pandas 2.0.0, *Backwards incompatible API changes > Other API changes* ([source][1])
- Deprecated argument date_parser in read_csv() , read_table() , read_fwf() , and read_excel() in favour of date_format ( GH 50601 ) — pandas 2.0.0, *Deprecations* ([source][1])
- Enforced disallowing using usecols with out of bounds indices for read_csv with engine="c" ( GH 25623 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### UNSURE · line 8 · `append`

```python
cols.append("new")
```

The changelog notes below would apply if this is a pandas object. The scanner could not confirm that it is one (it may be, for example, a plain list), so this is shown as unsure.

**Fix suggested by the changelog:** use concat() instead

**Matched notes** (read these to decide)

- Removed deprecated Series.append() , DataFrame.append() , use concat() instead ( GH 35407 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### NEEDS REVIEW · line 9 · `groupby`

```python
counts = frame.groupby("kind").agg("sum")
```

The changelog has 4 note(s) that mention this function, but none could be matched to this exact call, so it needs a manual check.

**Matched notes** (read these to decide)

- Remove argument squeeze from DataFrame.groupby() and Series.groupby() ( GH 32380 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Enforced deprecation of silently dropping nuisance columns in groupby and resample operations when numeric_only=False ( GH 41475 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Removed the level keyword in DataFrame and Series aggregations; use groupby instead ( GH 39983 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- When providing a list of columns of length one to DataFrame.groupby() , the keys that are returned by iterating over the resulting DataFrameGroupBy object will now be tuples of length one ( GH 47761 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### NEEDS REVIEW · line 14 · `DataFrame`

```python
def to_rows(df: DataFrame):
```

The changelog has 5 note(s) that mention this function, but none could be matched to this exact call, so it needs a manual check.

**Matched notes** (read these to decide)

- Deprecated DataFrame.backfill() in favor of DataFrame.bfill() ( GH 33396 ) — pandas 2.0.0, *Deprecations* ([source][1])
- Enforced disallowing value_name argument in DataFrame.melt() to match an element in the DataFrame columns ( GH 35003 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Deprecated DataFrame.pad() in favor of DataFrame.ffill() ( GH 33396 ) — pandas 2.0.0, *Deprecations* ([source][1])
- Removed deprecated argument col_space in DataFrame.to_latex() ( GH 47970 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Removed errors keyword from DataFrame.where() , Series.where() , DataFrame.mask() and Series.mask() ( GH 47728 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### NEEDS REVIEW · line 15 · `to_dict`

```python
rows = df.to_dict()
```

The changelog has 1 note(s) that mention this function, but none could be matched to this exact call, so it needs a manual check.

**Matched notes** (read these to decide)

- Disallow passing abbreviations for orient in DataFrame.to_dict() ( GH 32516 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### UNSURE · line 16 · `append`

```python
rows.append(1)
```

The changelog notes below would apply if this is a pandas object. The scanner could not confirm that it is one (it may be, for example, a plain list), so this is shown as unsure.

**Matched notes** (read these to decide)

- Removed deprecated Series.append() , DataFrame.append() , use concat() instead ( GH 35407 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

### io_tools.py

#### NEEDS REVIEW · line 5 · `ExcelWriter`

```python
with pd.ExcelWriter(path) as writer:
```

The changelog has 3 note(s) that mention this function, but none could be matched to this exact call, so it needs a manual check.

**Matched notes** (read these to decide)

- Removed deprecated methods ExcelWriter.write_cells() , ExcelWriter.save() , ExcelWriter.cur_sheet() , ExcelWriter.handles() , ExcelWriter.path() ( GH 45795 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- The ExcelWriter attribute book can no longer be set; it is still available to be accessed and mutated ( GH 48943 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Enforced disallowing the use of **kwargs in ExcelWriter ; use the keyword argument engine_kwargs instead ( GH 40430 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### NEEDS REVIEW · line 6 · `to_excel`

```python
df.to_excel(writer)
```

The changelog has 1 note(s) that mention this function, but none could be matched to this exact call, so it needs a manual check. (The scanner could not confirm this object is from pandas, so treat this as a possibility.)

**Matched notes** (read these to decide)

- Removed arguments verbose and encoding from DataFrame.to_excel() and Series.to_excel() ( GH 47912 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### AFFECTED · line 7 · `save`

```python
writer.save()
```

The save method is mentioned in Note 1 as being removed in version 2.0.0, which affects the writer.save() call in io_tools.py.

**Evidence**

> Removed deprecated methods ExcelWriter.write_cells() , ExcelWriter.save() , ExcelWriter.cur_sheet() , ExcelWriter.handles() , ExcelWriter.path() ( GH 45795 )
> — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### AFFECTED · line 9 · `Int64Index`

```python
idx = pd.Int64Index([1, 2])
```

The Int64Index was removed in version 2.0.0, which affects the code line as it uses Int64Index.

**Evidence**

> Removed Int64Index , UInt64Index and Float64Index . See also here for more information ( GH 42717 )
> — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### AFFECTED · line 10 · `Int64Index`

```python
return isinstance(idx, pd.Int64Index)
```

The code uses isinstance(idx, pd.Int64Index) which is affected by the removal of Int64Index in Note 1.

**Evidence**

> Removed Int64Index , UInt64Index and Float64Index . See also here for more information ( GH 42717 )
> — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

### legacy_args.py

#### AFFECTED · line 5 · `read_csv`

```python
data = pd.read_csv(path, error_bad_lines=False)
```

The code uses the argument 'error_bad_lines' which was removed in version 2.0.0 as stated in Note 1.

**Evidence**

> Removed arguments prefix , squeeze , error_bad_lines and warn_bad_lines from read_csv() ( GH 40413 , GH 43427 )
> — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### AFFECTED · line 6 · `groupby`

```python
pieces = data.groupby("kind", squeeze=True)
```

The code uses the 'squeeze' argument of DataFrame.groupby(), which was removed in version 2.0.0 as stated in Note 1.

**Evidence**

> Remove argument squeeze from DataFrame.groupby() and Series.groupby() ( GH 32380 )
> — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### AFFECTED · line 7 · `to_excel`

```python
data.to_excel("out.xlsx", encoding="utf-8")
```

The to_excel() function now does not accept the 'encoding' argument, as per Note 1 from version 2.0.0 changelog.

**Evidence**

> Removed arguments verbose and encoding from DataFrame.to_excel() and Series.to_excel() ( GH 47912 )
> — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

### loader.py

#### NEEDS REVIEW · line 7 · `read_csv`

```python
df = pd.read_csv(path)
```

The changelog has 5 note(s) that mention this function, but none could be matched to this exact call, so it needs a manual check.

**Matched notes** (read these to decide)

- Disallow passing non-keyword arguments to read_csv() except filepath_or_buffer ( GH 41485 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Remove keyword mangle_dupe_cols from read_csv() and read_table() ( GH 48137 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Added "None" to default na_values in read_csv() ( GH 50286 ) — pandas 2.0.0, *Backwards incompatible API changes > Other API changes* ([source][1])
- Deprecated argument date_parser in read_csv() , read_table() , read_fwf() , and read_excel() in favour of date_format ( GH 50601 ) — pandas 2.0.0, *Deprecations* ([source][1])
- Enforced disallowing using usecols with out of bounds indices for read_csv with engine="c" ( GH 25623 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### NEEDS REVIEW · line 8 · `DataFrame`

```python
extra = pd.DataFrame({"a": [1]})
```

The changelog has 5 note(s) that mention this function, but none could be matched to this exact call, so it needs a manual check.

**Matched notes** (read these to decide)

- Deprecated DataFrame.backfill() in favor of DataFrame.bfill() ( GH 33396 ) — pandas 2.0.0, *Deprecations* ([source][1])
- Enforced disallowing value_name argument in DataFrame.melt() to match an element in the DataFrame columns ( GH 35003 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Deprecated DataFrame.pad() in favor of DataFrame.ffill() ( GH 33396 ) — pandas 2.0.0, *Deprecations* ([source][1])
- Removed deprecated argument col_space in DataFrame.to_latex() ( GH 47970 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Removed errors keyword from DataFrame.where() , Series.where() , DataFrame.mask() and Series.mask() ( GH 47728 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### AFFECTED · line 9 · `append`

```python
df = df.append(extra)
```

The append method of DataFrame was removed in version 2.0.0, and the note suggests using concat() instead.

**Evidence**

> Removed deprecated Series.append() , DataFrame.append() , use concat() instead ( GH 35407 )
> — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

#### UNSURE · line 10 · `append`

```python
LOG.append("loaded")
```

The changelog notes below would apply if this is a pandas object. The scanner could not confirm that it is one (it may be, for example, a plain list), so this is shown as unsure.

**Fix suggested by the changelog:** use concat() instead

**Matched notes** (read these to decide)

- Removed deprecated Series.append() , DataFrame.append() , use concat() instead ( GH 35407 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

### plain_import.py

#### NEEDS REVIEW · line 5 · `read_csv`

```python
data = pandas.read_csv(path)
```

The changelog has 5 note(s) that mention this function, but none could be matched to this exact call, so it needs a manual check.

**Matched notes** (read these to decide)

- Disallow passing non-keyword arguments to read_csv() except filepath_or_buffer ( GH 41485 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Remove keyword mangle_dupe_cols from read_csv() and read_table() ( GH 48137 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])
- Added "None" to default na_values in read_csv() ( GH 50286 ) — pandas 2.0.0, *Backwards incompatible API changes > Other API changes* ([source][1])
- Deprecated argument date_parser in read_csv() , read_table() , read_fwf() , and read_excel() in favour of date_format ( GH 50601 ) — pandas 2.0.0, *Deprecations* ([source][1])
- Enforced disallowing using usecols with out of bounds indices for read_csv with engine="c" ( GH 25623 ) — pandas 2.0.0, *Removal of prior version deprecations/changes* ([source][1])

## No documented change

The changelog does not mention these. That is not a guarantee they are safe (see below).

- `cleaning.py:7` `cols = raw.columns.tolist()`
- `cleaning.py:9` `counts = frame.groupby("kind").agg("sum")`
- `cleaning.py:10` `ok = pat.is_scalar(3)`
- `io_tools.py:8` `pd.options.mode.chained_assignment = None`

## How to read this report

- **Affected** needs two things: the matcher found a removal or a changed argument in the changelog that matches this call, and the AI model (qwen2.5:3b) explained it with a quote that was checked word for word against that note. The model is used only for these confirmed matches.
- **Needs review** means the changelog mentions the function by name, but no note could be tied to this exact call. The model is deliberately not asked about these: a small model can neither confirm nor safely rule out a break from a name match. Read the listed notes yourself.
- **Unsure** means the object may not be from the library (for example a plain list's `.append`), or the AI's answer could not be verified.
- **No documented change does not mean safe.** Only changes written in the ingested changelog pages are checked, and only for calls the scanner found. Behaviour changes that the changelog describes without naming the function can be missed.
- Suggested fixes appear only when the changelog itself states one.

## Sources

- [1] https://pandas.pydata.org/pandas-docs/version/2.3/whatsnew/v2.0.0.html

[1]: https://pandas.pydata.org/pandas-docs/version/2.3/whatsnew/v2.0.0.html
