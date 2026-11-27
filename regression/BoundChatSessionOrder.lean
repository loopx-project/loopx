import Std

/-
Run with Lean 4.22.0 using
`lean -DwarningAsError=true regression/BoundChatSessionOrder.lean`.

`approved` represents Core's validated Session list. Rejection has no list.
The model begins after timestamp parsing. It uses UTC microseconds and a
rank for the original timestamp text, which is the final sort tie breaker.
Python applies the sort to the validated rows in `local_session_snapshot`.
The HTTP regressions exercise actual binding checks and timestamp parsing.
-/

namespace BoundChatSessionOrder

structure Row where
  session : Nat
  time : Option Int
  rawRank : Nat

def beforeOrAt (a b : Row) : Bool :=
  match a.time, b.time with
  | some left, some right =>
    decide (right < left ∨ (left = right ∧ b.rawRank ≤ a.rawRank))
  | some _, none => true
  | none, some _ => false
  | none, none => decide (b.rawRank ≤ a.rawRank)

def display (approved : Option (List Row)) : Option (List Row) :=
  approved.map (fun rows => rows.mergeSort beforeOrAt)

theorem rejection_remains_unavailable : display none = none := rfl

theorem display_keeps_every_approved_row (approved displayed : List Row)
    (result : display (some approved) = some displayed) : displayed.Perm approved := by
  have ordered : approved.mergeSort beforeOrAt = displayed := by
    simpa [display] using result
  rw [← ordered]
  exact List.mergeSort_perm approved beforeOrAt

theorem displayed_rows_have_recency_order (approved displayed : List Row)
    (result : display (some approved) = some displayed) :
    displayed.Pairwise (fun a b => beforeOrAt a b = true) := by
  have ordered : approved.mergeSort beforeOrAt = displayed := by
    simpa [display] using result
  rw [← ordered]
  apply List.sorted_mergeSort
  · rintro ⟨s, a, ar⟩ ⟨t, b, br⟩ ⟨u, c, cr⟩ hab hbc
    cases a <;> cases b <;> cases c <;> simp [beforeOrAt] at * <;> omega
  · rintro ⟨s, a, ar⟩ ⟨t, b, br⟩
    cases a <;> cases b <;> simp [beforeOrAt, Bool.or_eq_true] <;> omega

theorem equal_instants_keep_raw_tie_rank (instant : Int) (a b ar br : Nat) :
    beforeOrAt ⟨a, some instant, ar⟩ ⟨b, some instant, br⟩ = decide (br ≤ ar) := by
  simp [beforeOrAt]

theorem old_text_comparison_can_put_an_older_session_first :
    "2026-02-03T10:00:00Z" > "2026-02-03T10:00:00.000001Z" ∧
      beforeOrAt ⟨1, some 1, 0⟩ ⟨0, some 0, 1⟩ = true := by decide

#print axioms rejection_remains_unavailable
#print axioms display_keeps_every_approved_row
#print axioms displayed_rows_have_recency_order
#print axioms equal_instants_keep_raw_tie_rank
#print axioms old_text_comparison_can_put_an_older_session_first

end BoundChatSessionOrder
