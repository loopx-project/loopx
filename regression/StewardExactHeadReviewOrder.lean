import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardExactHeadReviewOrder.lean`.

The model starts with exact-head records. Its optional UTC microsecond field
comes from the parser and the actionable flag comes from existing review
classification. Ranks represent the unchanged raw text and ID ordering.
Python tests cover actual parsing, verdict reduction, and provider refusals.
-/

namespace StewardExactHeadReviewOrder

structure Review where
  instant : Option Int
  rawRank : Nat
  idRank : Nat
  actionable : Bool

def legacyBeforeOrAt (left right : Review) : Bool :=
  decide (left.rawRank < right.rawRank ∨
    (left.rawRank = right.rawRank ∧ left.idRank ≤ right.idRank))

def chronologicalBeforeOrAt (left right : Review) : Bool :=
  match left.instant, right.instant with
  | some first, some second =>
    decide (first < second ∨ (first = second ∧ legacyBeforeOrAt left right = true))
  | none, some _ => true
  | some _, none => false
  | none, none => legacyBeforeOrAt left right

def canUseChronology (reviews : List Review) : Bool :=
  reviews.all (fun review => review.instant.isSome || !review.actionable)

def order (reviews : List Review) : List Review :=
  if canUseChronology reviews then reviews.mergeSort chronologicalBeforeOrAt
  else reviews.mergeSort legacyBeforeOrAt

theorem order_retains_every_record (reviews : List Review) : (order reviews).Perm reviews := by
  unfold order
  split <;> exact List.mergeSort_perm _ _

theorem unreadable_actionable_record_retains_legacy_order
    (reviews : List Review) (review : Review) (present : review ∈ reviews)
    (missing : review.instant = none) (actionable : review.actionable = true) :
    order reviews = reviews.mergeSort legacyBeforeOrAt := by
  have unknown : canUseChronology reviews = false := by
    cases result : canUseChronology reviews with
    | false => rfl
    | true =>
      have accepted := List.all_eq_true.mp result review present
      simp [missing, actionable] at accepted
  simp [order, unknown]

theorem utc_time_takes_precedence_over_raw_text
    (first second : Int) (leftRaw rightRaw leftId rightId : Nat) (earlier : first < second) :
    chronologicalBeforeOrAt ⟨some first, leftRaw, leftId, true⟩ ⟨some second, rightRaw, rightId, true⟩ = true ∧
    chronologicalBeforeOrAt ⟨some second, rightRaw, rightId, true⟩ ⟨some first, leftRaw, leftId, true⟩ = false := by
  have notEarlier : ¬second < first := by omega
  have different : second ≠ first := by omega
  simp [chronologicalBeforeOrAt, earlier, notEarlier, different]

theorem equal_instants_keep_the_legacy_tie_break
    (instant : Int) (leftRaw rightRaw leftId rightId : Nat) :
    let left : Review := ⟨some instant, leftRaw, leftId, true⟩
    let right : Review := ⟨some instant, rightRaw, rightId, true⟩
    chronologicalBeforeOrAt left right = legacyBeforeOrAt left right := by
  simp [chronologicalBeforeOrAt]

theorem raw_offset_text_can_reverse_two_reviews :
    legacyBeforeOrAt ⟨some 2, 0, 0, true⟩ ⟨some 1, 1, 1, true⟩ = true ∧
    chronologicalBeforeOrAt ⟨some 2, 0, 0, true⟩ ⟨some 1, 1, 1, true⟩ = false := by
  simp [legacyBeforeOrAt, chronologicalBeforeOrAt]

#print axioms order_retains_every_record
#print axioms unreadable_actionable_record_retains_legacy_order
#print axioms utc_time_takes_precedence_over_raw_text
#print axioms equal_instants_keep_the_legacy_tie_break
#print axioms raw_offset_text_can_reverse_two_reviews

end StewardExactHeadReviewOrder
