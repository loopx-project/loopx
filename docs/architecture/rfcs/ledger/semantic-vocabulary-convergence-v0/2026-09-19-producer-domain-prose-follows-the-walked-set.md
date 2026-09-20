# The producer domain's prose follows the walked set

Normative for what F1 and F2 claim; no check changes its verdict on the current
tree. What changes is that the sentence a reader audits and the set the checker
walks can no longer disagree.

- **Two answers were both green.** Giving `settlement_binding_kind` an executed
  witness made it the first `cross_runtime` vocabulary to declare producers, so
  `check_producers` walks it and F1/F2 report `7/26`. The same registry still
  said `Kernel(V)` was the only tier declaring producers, and F2 still said in
  words that the `cross_runtime` tier declares none. A machine reader and a
  human reader got different answers about what F1 and F2 prove, and 122
  focused tests plus 25 CI checks passed either way.
- **The gap was structural, not an oversight.** `check_invariant_domain` pins
  `quantifies_over`, `verified` and `registered` against counts derived from the
  registry, so the machine domain could not rot. `statement` and `evidence` were
  free text that nothing read. Widening the walked set was therefore a one-diff
  failure on the numbers and a silent one on the claim.
- **The domain is named for the predicate, not a tier.** `Producers(V)` is the
  subset that declares producers: every `tier: kernel` vocabulary plus any
  vocabulary of another tier carrying executed production evidence. That is the
  set `check_producers` visits, so the name no longer has to be restated when a
  vocabulary outside the kernel tier earns its way in.
- **The claim states its own boundary.** F1 and F2 name what stays outside —
  the 19 `cross_runtime` vocabularies still declaring no producer — rather than
  only the seven verified. Stating the verified half alone lets the unverified
  remainder shrink out of the text without any diff saying so.
- **The agreement is checked.** `check_domain_prose` in
  `examples/semantic-vocabulary-drift-smoke.py` derives the walked set and
  refuses prose that contradicts it: no producer-domain statement or evidence
  line may bound the claim by `Kernel(V)` once a non-kernel vocabulary is
  walked, none may say a tier declares no producers while one of its members
  does, and each statement must name the count left outside. Verified by
  mutation: restoring the `Kernel(V)` universe, re-adding the cross-runtime
  denial to F2, and replacing F1's `19 cross_runtime` with a vague phrase each
  turn the smoke red, and two regressions in
  `tests/architecture/test_semantic_vocabulary_drift.py` pin all three.
- **The RFC's own statement was carrying the same contradiction.** The formal
  model section in both language editions stated F1 and F2 over `Kernel(V)` and
  repeated the cross-runtime denial. Both are restated over `Producers(V)`. The
  2026-09-17 appendix entry that first bounded the invariants to `Kernel(V)`
  stays as written: it is append-only history and was accurate when recorded.
