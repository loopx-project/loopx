import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/ChatIngressReceiptOrder.lean`.

The model of `loopx_ingress` starts after timestamp parsing. Parsed instants
are UTC microseconds. Natural ranks represent the Python string order of raw
timestamps and ingress IDs. The file-backed Chat regression exercises the
actual timestamp parser and the queue drain with a local provider substitute.
-/

namespace ChatIngressReceiptOrder

inductive Mode where
  | queue | inbox | other

structure Receipt where
  mode : Mode
  time : Option Int
  rawRank : Nat
  idRank : Nat

def retained (row : Receipt) : Bool :=
  match row.mode with
  | .queue | .inbox => true
  | .other => false

abbrev tieBeforeOrAt (a b : Receipt) : Prop :=
  a.rawRank < b.rawRank ∨ (a.rawRank = b.rawRank ∧ a.idRank ≤ b.idRank)

def beforeOrAt (a b : Receipt) : Bool :=
  match a.time, b.time with
  | some left, some right =>
    decide (left < right ∨ (left = right ∧ tieBeforeOrAt a b))
  | none, some _ => true
  | some _, none => false
  | none, none => decide (tieBeforeOrAt a b)

def readback (rows : List Receipt) : List Receipt :=
  (rows.filter retained).mergeSort beforeOrAt

theorem keeps_exactly_the_retained_receipts (rows : List Receipt) :
    (readback rows).Perm (rows.filter retained) :=
  List.mergeSort_perm _ _

theorem readback_is_ordered (rows : List Receipt) :
    (readback rows).Pairwise (fun a b => beforeOrAt a b = true) := by
  unfold readback
  apply List.sorted_mergeSort
  · rintro ⟨_, a, ar, ai⟩ ⟨_, b, br, bi⟩ ⟨_, c, cr, ci⟩ hab hbc
    cases a <;> cases b <;> cases c <;> simp [beforeOrAt, tieBeforeOrAt] at * <;> omega
  · rintro ⟨_, a, ar, ai⟩ ⟨_, b, br, bi⟩
    cases a <;> cases b <;> simp [beforeOrAt, tieBeforeOrAt, Bool.or_eq_true] <;> omega

theorem parsed_receipts_are_chronological (rows : List Receipt) :
    (readback rows).Pairwise (fun a b =>
      ∀ first second, a.time = some first → b.time = some second → first ≤ second) := by
  apply (readback_is_ordered rows).imp
  intro a b ordered first second aTime bTime
  simp only [beforeOrAt, aTime, bTime, decide_eq_true_eq] at ordered
  omega

theorem equal_instants_keep_the_previous_tie_rule (instant : Int)
    (a b : Receipt) (atTime : a.time = some instant) (btTime : b.time = some instant) :
    beforeOrAt a b = true ↔ tieBeforeOrAt a b := by
  simp [beforeOrAt, tieBeforeOrAt, atTime, btTime]

theorem text_sort_can_reverse_chronology :
    "2026-02-03T10:00:00.000001Z" < "2026-02-03T10:00:00Z" ∧
      beforeOrAt ⟨.queue, some 0, 1, 0⟩ ⟨.queue, some 1, 0, 1⟩ = true := by decide

#print axioms keeps_exactly_the_retained_receipts
#print axioms readback_is_ordered
#print axioms parsed_receipts_are_chronological
#print axioms equal_instants_keep_the_previous_tie_rule
#print axioms text_sort_can_reverse_chronology

end ChatIngressReceiptOrder
