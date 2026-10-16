# Production Gap Log

Shortcuts taken for the student/prototype version of Module 1, what production would do instead, and how to get there. Updated: 2026-10-16.

## Open gaps

| # | System | Shortcut today | Production version | Migration path |
|---|---|---|---|---|
| 1 | Ingest | GitHub issues are not ingested (dropped as a source) | Ingest breaking-change issue threads as a third source (`source_type: github_issue`) | Add a GitHub REST API fetcher (token from an environment variable, never in code), add `github_repo` and `github_issue_filter` to the settings file, add a chunk branch for issue threads |
| 2 | Chunk | `guess_source_url()` rebuilds each file's URL from its filename | Ingest writes `data/manifest.json` (filename to url and source_type); chunk reads it | Add manifest writing to `ingest`, replace `guess_source_url` with a manifest lookup |
| 3 | Chunk | `source_type` is inferred from filename text (`"guide"` or `"write"` in the name) | Taken from the manifest, never inferred | Same change as gap 2 |
| 4 | Chunk | Guide chunks have `version: None` | Settings holds the target version range, applied to guide chunks | Add `target_versions` to the settings file, read it in `chunk_file` |
| 5 | Chunk | Category keyword map is hardcoded in `chunk.py` | Per-library ordered list in the settings file | Move `CATEGORY_KEYWORDS` into settings (keep order, since the first match wins) |
| 6 | Chunk | **Resolved 2026-10-16.** Subsections were folded into their parent section | Each subsection is now its own unit with a `subsection` field | Done in the chunker rewrite |
| 7 | Chunk | `MAX_CHUNK_WORDS = 150` and `SHORT_ITEM_WORDS = 3` are hardcoded | Configurable per library | Move to settings with a default |
| 8 | All | Paths are relative to the working directory (`Path("data")`, `configs/...`) | Resolve from `Path(__file__).parent` so scripts run from any folder | Add a `PROJECT_ROOT` constant and build paths from it |
| 9 | Ingest | `target_version` is hardcoded to 2.0.x and its dots are unescaped (`v2.0.\d+.html`) | Version range (from, to) in settings; dots escaped as `\.` | Add `from_version` and `to_version`, build the pattern in code |
| 10 | Ingest | Pages are re-downloaded on every run | Cache with conditional requests, plus a scheduled refresh when a new release appears | See the auto-refresh item in the Future Features Backlog |
| 11 | All | No automated tests; verification is manual smoke tests | pytest unit tests for ingest, chunk, and the scanner, run in CI | Add `tests/`, then a GitHub Actions workflow |
| 12 | Chunk | The chunker assumes Sphinx-style `<section>` nesting (page wrapper, then h2, then h3) and the changelog intro is skipped by a hardcoded `source_type` rule | Fall back to heading-based splitting when `<section>` tags are absent; make the intro rule a setting | Add a heading-walk fallback and a `skip_intro_for` setting |
| 13 | Chunk | Code blocks longer than 150 words are split at a line break, which can cut an example in half | Split code on blank lines or function boundaries and repeat the introducing sentence on each piece | Make the splitter code-aware |
| 14 | Retrieve | The index is deleted and rebuilt from scratch on every run | Incremental upsert keyed on chunk id plus a content hash, so only changed chunks are re-embedded | Store a hash in each payload and compare before embedding |
| 15 | Retrieve | Qdrant host/port and the embedding model name are constants in `retrieve.py` | Read from environment variables or settings, with these as defaults | Add env-var overrides |

## Dropped decisions

- **GitHub issues as a source** (Decision 2 originally included them). Dropped on 2026-10-16. The changelog pages do contain the breaking changes. A first check wrongly suggested the chunks covered them, but the chunker was dropping paragraphs, code examples and subsection titles in sections that also had lists. Fixed the same day in the chunker rewrite (832 chunks). Revisit if explanations lack the maintainers' reasoning (gap 1).
