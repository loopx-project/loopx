import Std

/-
Run with Lean 4.22.0 using
`lean -DwarningAsError=true regression/ChatCompletionChronology.lean`.

`instant` is the parsed UTC microsecond. The model uses the relative lexical
rank of unreadable timestamps. It mirrors the two Chat history sort keys in
`completed_todos.py`. The Python HTTP tests exercise parsing, live accepted
result verification and snapshot cursors on File and SQLite.
-/

namespace ChatCompletionChronology

structure Row where
  instant : Option Int
  rawRank : Nat
  position : Nat

def beforeOrAt (left right : Row) : Bool :=
  match left.instant, right.instant with
  | some a, some b => decide (b < a ∨ (a = b ∧ right.position ≤ left.position))
  | some _, none => true
  | none, some _ => false
  | none, none =>
    decide (right.rawRank < left.rawRank ∨
      (left.rawRank = right.rawRank ∧ right.position ≤ left.position))

def historyOrder (rows : List Row) : List Row :=
  rows.mergeSort beforeOrAt

theorem history_is_ordered (rows : List Row) :
    (historyOrder rows).Pairwise (fun a b => beforeOrAt a b = true) := by
  apply List.sorted_mergeSort
  · rintro ⟨a, ar, ap⟩ ⟨b, br, bp⟩ ⟨c, cr, cp⟩ hab hbc
    cases a <;> cases b <;> cases c <;> simp [beforeOrAt] at * <;> omega
  · rintro ⟨a, ar, ap⟩ ⟨b, br, bp⟩
    cases a <;> cases b <;> simp [beforeOrAt, Bool.or_eq_true] <;> omega

theorem all_records_are_retained (rows : List Row) : (historyOrder rows).Perm rows :=
  List.mergeSort_perm rows beforeOrAt

theorem equal_instants_use_source_position (instant : Int)
    (leftRank rightRank leftPosition rightPosition : Nat) :
    beforeOrAt ⟨some instant, leftRank, leftPosition⟩ ⟨some instant, rightRank, rightPosition⟩ =
      decide (rightPosition ≤ leftPosition) := by
  simp [beforeOrAt]

theorem snapshot_pages_preserve_the_order (rows : List Row) (size : Nat) :
    (historyOrder rows).take size ++ (historyOrder rows).drop size = historyOrder rows :=
  List.take_append_drop size (historyOrder rows)

theorem old_lexical_order_can_put_a_whole_second_first :
    "2026-02-03T10:00:00Z" > "2026-02-03T10:00:00.000001Z" ∧
      beforeOrAt ⟨some 1, 0, 0⟩ ⟨some 0, 1, 1⟩ = true := by decide

#print axioms history_is_ordered
#print axioms all_records_are_retained
#print axioms equal_instants_use_source_position
#print axioms snapshot_pages_preserve_the_order
#print axioms old_lexical_order_can_put_a_whole_second_first

end ChatCompletionChronology
