import Std

/-
Run with Lean 4.22.0 using
`lean -DwarningAsError=true regression/PeriodicReportCadenceArchive.lean`.

The model starts with a valid, unprepared cadence window and serializes each
switch under the journal lock. `archivePredecessor` models the first durable
write. `replace` models the two writes in order. The full admission record in
Python also includes the Goal, retirement, and task-completion fields.

`identify` abstracts the digest of the complete predecessor record. Retry
and preservation do not assume collision freedom. The liveness theorem
requires distinct retained records to have distinct keys. SHA-256 makes that
a practical assumption and the journal rejects a conflicting stored record.
The model assumes atomic file replacement and does not model the filesystem.
-/

namespace PeriodicReportCadenceArchive

structure Admission (Key : Type) where
  window : Nat
  admittedAt : Nat
  predecessor : Option Key
  publication : Option Nat
  deriving DecidableEq

abbrev Archive (Key : Type) := Key → Option (Admission Key)

variable {Key : Type} [DecidableEq Key]

def archivePredecessor (identify : Admission Key → Key) (history : Archive Key)
    (active : Admission Key) : Option (Archive Key) :=
  match history (identify active) with
  | none => some (fun key => if key = identify active then some active else history key)
  | some saved => if saved = active then some history else none

def replace (identify : Admission Key → Key) (history : Archive Key)
    (active : Admission Key) (window now : Nat) : Option (Archive Key × Admission Key) := do
  let archived ← archivePredecessor identify history active
  pure (archived, ⟨window, now, some (identify active), none⟩)

theorem archive_readback (identify : Admission Key → Key) (before after : Archive Key)
    (active : Admission Key) (saved : archivePredecessor identify before active = some after) :
    after (identify active) = some active := by
  cases lookup : before (identify active) with
  | none =>
    simp [archivePredecessor, lookup] at saved
    subst after
    simp
  | some entry =>
    by_cases same : entry = active
    · simp [archivePredecessor, lookup, same] at saved
      subst after
      simpa [same] using lookup
    · simp [archivePredecessor, lookup, same] at saved

theorem archive_preserves (identify : Admission Key → Key) (before after : Archive Key)
    (active old : Admission Key) (key : Key)
    (retained : before key = some old)
    (saved : archivePredecessor identify before active = some after) :
    after key = some old := by
  cases lookup : before (identify active) with
  | none =>
    simp [archivePredecessor, lookup] at saved
    subst after
    have different : key ≠ identify active := by
      intro equal
      rw [equal, lookup] at retained
      contradiction
    simp [different, retained]
  | some entry =>
    by_cases same : entry = active
    · simp [archivePredecessor, lookup, same] at saved
      subst after
      exact retained
    · simp [archivePredecessor, lookup, same] at saved

theorem archive_conflict_refused (identify : Admission Key → Key) (history : Archive Key)
    (active saved : Admission Key) (lookup : history (identify active) = some saved)
    (different : saved ≠ active) : archivePredecessor identify history active = none := by
  simp [archivePredecessor, lookup, different]

theorem archive_total_when_keys_distinguish (identify : Admission Key → Key)
    (history : Archive Key) (active : Admission Key)
    (distinguish : ∀ saved, history (identify active) = some saved → saved = active) :
    ∃ after, archivePredecessor identify history active = some after := by
  cases lookup : history (identify active) with
  | none => simp [archivePredecessor, lookup]
  | some entry => simp [archivePredecessor, lookup, distinguish entry lookup]

theorem restart_after_archive (identify : Admission Key → Key) (before after : Archive Key)
    (active : Admission Key) (window now : Nat)
    (saved : archivePredecessor identify before active = some after) :
    replace identify after active window now =
      some (after, ⟨window, now, some (identify active), none⟩) := by
  simp [replace, archivePredecessor, archive_readback identify before after active saved]

theorem replacement_retains_predecessor (identify : Admission Key → Key)
    (before after : Archive Key) (active next : Admission Key) (window now : Nat)
    (saved : replace identify before active window now = some (after, next)) :
    after (identify active) = some active ∧ next.predecessor = some (identify active) ∧
      next.publication = none := by
  cases replay : archivePredecessor identify before active with
  | none => simp [replace, replay] at saved
  | some archived =>
    simp [replace, replay] at saved
    obtain ⟨rfl, rfl⟩ := saved
    exact ⟨archive_readback identify before archived active replay, rfl, rfl⟩

private def switchBackAndForth (identify : Admission Nat → Nat) := do
  let first : Admission Nat := ⟨0, 0, none, none⟩
  let (history, other) ← replace identify (fun _ => none) first 1 0
  let (history, sameWindow) ← replace identify history other 0 0
  replace identify history sameWindow 1 0

theorem old_window_key_conflicts : switchBackAndForth Admission.window = none := by rfl

-- For this two-window example the record key also encodes the predecessor.
private def exampleKey (entry : Admission Nat) : Nat :=
  entry.window + 2 * entry.predecessor.getD 0 + 1

theorem four_admissions_keep_three_distinct_predecessors :
    (switchBackAndForth exampleKey).map (fun (history, latest) =>
      (latest, history 1, history 4, history 9)) =
      some (⟨1, 0, some 9, none⟩, some ⟨0, 0, none, none⟩,
        some ⟨1, 0, some 1, none⟩, some ⟨0, 0, some 4, none⟩) := by rfl

#print axioms archive_preserves
#print axioms archive_conflict_refused
#print axioms archive_total_when_keys_distinguish
#print axioms restart_after_archive
#print axioms replacement_retains_predecessor
#print axioms old_window_key_conflicts
#print axioms four_admissions_keep_three_distinct_predecessors

end PeriodicReportCadenceArchive
