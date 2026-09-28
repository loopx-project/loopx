# RFC execution ledger

One file per entry. A ledger entry records what a change measured, what it
changed, and what it deliberately did not establish — the same content that used
to be appended to an RFC's Appendix A.

## Why entries are files

Appendix A was a single append cluster. Every branch adding an entry inserted at
the same position, so concurrent work conflicted there by construction: eight
times in one afternoon during the #4447 repair round, each resolved by hand as
"both sides are disjoint, keep both". That is mechanical work with a real
failure mode, because one careless resolution silently drops an entry.

A file per entry removes the shared line. Two branches adding entries on the
same day touch two different files and merge cleanly with no resolution at all.

## Convention

- `<rfc-slug>/YYYY-MM-DD-slug.md` — one directory per RFC, named exactly like
  the RFC file it belongs to. Several RFCs carry an execution-ledger appendix,
  so an entry has to say which one it extends. The appendix inside the RFC is
  titled `Appendix <letter>: Execution ledger` and takes whichever letter the
  RFC has free.
- The name carries the date the work was measured, not the merge date.
- Each entry carries a Chinese mirror at `<rfc-slug>/YYYY-MM-DD-slug.zh-CN.md`,
  the same rule the RFCs themselves follow.
- A directory is the index for its RFC. Nothing enumerates entries across
  RFCs, because an index line is the append cluster this directory exists to
  remove.
- Entries are append-only history. Correct a wrong entry with a later entry that
  says what was wrong; do not rewrite a record someone may have cited.
- Dated checkpoints are ledger entries, not RFC sections. An RFC body must not
  contain a heading with `checkpoint` in it; the body keeps a one-line pointer
  (`Checkpoint moved to the execution ledger: <link to ledger/<rfc-slug>/<file>>`)
  where the section used to be. Sections moved from an RFC after the fact open
  with a provenance line naming the source RFC, the former section title, the
  move date and the RFC baseline commit, so a reader can diff the move.

`examples/docs-governance-smoke.py` checks the naming and the mirror pairing.
