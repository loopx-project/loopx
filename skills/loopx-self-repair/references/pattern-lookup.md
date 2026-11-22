# Find a repair pattern

The catalog is a reference database. Search
using the exact error code or two distinctive symptom terms, then expand the
relevant result. Run from this skill directory, or use the script's absolute
path from any working directory:

```bash
python3 scripts/find_pattern.py --query 'turn recovery'
python3 scripts/find_pattern.py --id acceptance_scope_capture
```

Search returns at most five ids with their complete symptoms, plus
`total_matches` and `next_offset`. BM25 ranks lexical matches; a complete pattern
id, then an exact backtick-delimited code token, take precedence. Results label
that precedence in `exact_match`. Identifiers split at underscores and punctuation, ignoring
case. Refine the query or pass `--offset` to see the next page. If an exact
error code has no match, try its domain and symptom words. `--list` browses ids;
`--id` returns the complete evidence, root and repair guidance without truncation.
Each result exposes `matched_terms`, and `unmatched_terms` names query words
absent from the corpus. Scores are ordering signals, not confidence or a
diagnosis. A generic shared word such as `turn` can rank unrelated incidents:
expand the symptom with the failing action and exact error before choosing a
repair. There is no synonym expansion, stemming, translation or semantic model;
for the English catalog, use English terms or literal protocol identifiers.
A match is a hypothesis: verify it against the current typed contract and facts.

The small manually labeled regression set in
`tests/fixtures/self_repair_queries.json` includes an intentionally under-specified
query to retain this limitation. Its hit rates are a local sanity check, not
measured production search accuracy. BM25 uses fixed `k1=1.2`, `b=0.75` and
positive IDF; these parameters are not fitted to that set. See the
[Lucene BM25 formula and defaults](https://lucene.apache.org/core/9_12_1/core/org/apache/lucene/search/similarities/BM25Similarity.html).

The script uses only Python's standard library, resolves resources relative to
itself, and does not invoke LoopX, read a registry, or create a Turn. It is also
delivered with installed workflow skills. If Python is unavailable, search
the source with `rg -n -F '<distinctive term>' references/repair-patterns.md`
and read only the matching rows or section. Do not recover truncated output by
paging through the entire catalog.

Maintainers add or amend the single canonical
[pattern catalog](repair-patterns.md), preserving its five-column table and
unique ids. Prose appendices are searchable as `note_*` entries. Existing
patterns are diagnosis aids, not additional authority or mandatory checklists.
