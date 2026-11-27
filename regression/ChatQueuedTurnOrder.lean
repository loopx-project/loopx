import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/ChatQueuedTurnOrder.lean`.

`selected` represents the Core queue selection for a specific read.
`eligible` represents the later checks under a fixed, uncontended claim.
`time` is the parsed UTC instant in microseconds. Raw timestamp and Turn ID
ranks represent Python string order. The HTTP and store regression covers
actual parsing, persistence, and claim transitions.
-/

namespace ChatQueuedTurnOrder

structure Turn where
  selected : Bool
  eligible : Bool
  time : Option Int
  rawRank : Nat
  idRank : Nat

abbrev tieBeforeOrAt (a b : Turn) : Prop :=
  a.rawRank < b.rawRank ∨ (a.rawRank = b.rawRank ∧ a.idRank ≤ b.idRank)

def beforeOrAt (a b : Turn) : Bool :=
  match a.time, b.time with
  | some left, some right => decide (left < right ∨ (left = right ∧ tieBeforeOrAt a b))
  | none, some _ => true
  | some _, none => false
  | none, none => decide (tieBeforeOrAt a b)

def readback (rows : List Turn) : List Turn :=
  (rows.filter Turn.selected).mergeSort beforeOrAt

def candidates (rows : List Turn) : List Turn :=
  (readback rows).filter Turn.eligible

def claim (rows : List Turn) : Option Turn :=
  (candidates rows).head?

theorem readback_keeps_exactly_the_selected_turns (rows : List Turn) :
    (readback rows).Perm (rows.filter Turn.selected) :=
  List.mergeSort_perm _ _

theorem readback_is_ordered (rows : List Turn) :
    (readback rows).Pairwise (fun a b => beforeOrAt a b = true) := by
  unfold readback
  apply List.sorted_mergeSort
  · rintro ⟨_, _, a, ar, ai⟩ ⟨_, _, b, br, bi⟩ ⟨_, _, c, cr, ci⟩ hab hbc
    cases a <;> cases b <;> cases c <;> simp [beforeOrAt, tieBeforeOrAt] at * <;> omega
  · rintro ⟨_, _, a, ar, ai⟩ ⟨_, _, b, br, bi⟩
    cases a <;> cases b <;> simp [beforeOrAt, tieBeforeOrAt, Bool.or_eq_true] <;> omega

theorem candidate_membership (rows : List Turn) (turn : Turn) :
    turn ∈ candidates rows ↔ turn ∈ rows ∧ turn.selected = true ∧ turn.eligible = true := by
  rw [candidates, List.mem_filter, (readback_keeps_exactly_the_selected_turns rows).mem_iff,
    List.mem_filter]
  exact and_assoc

theorem claim_selects_an_eligible_turn (rows : List Turn) (first : Turn)
    (claimed : claim rows = some first) :
    first ∈ rows ∧ first.selected = true ∧ first.eligible = true := by
  apply (candidate_membership rows first).mp
  cases found : candidates rows with
  | nil => simp [claim, found] at claimed
  | cons head tail =>
    have same : head = first := by simpa [claim, found] using claimed
    simp [same]

theorem claim_has_no_earlier_eligible_instant (rows : List Turn) (first later : Turn)
    (claimed : claim rows = some first)
    (present : later ∈ rows) (selected : later.selected = true) (eligible : later.eligible = true)
    (firstTime laterTime : Int)
    (firstParsed : first.time = some firstTime) (laterParsed : later.time = some laterTime) :
    firstTime ≤ laterTime := by
  have member := (candidate_membership rows later).mpr ⟨present, selected, eligible⟩
  have sorted : (candidates rows).Pairwise (fun a b => beforeOrAt a b = true) :=
    (readback_is_ordered rows).filter _
  cases found : candidates rows with
  | nil => simp [claim, found] at claimed
  | cons head tail =>
    have same : head = first := by simpa [claim, found] using claimed
    rw [found, same] at member sorted
    rcases List.mem_cons.mp member with equal | inTail
    · have sameTime : firstTime = laterTime := by simpa [equal, firstParsed] using laterParsed
      omega
    · have ordered := (List.pairwise_cons.mp sorted).1 later inTail
      simp only [beforeOrAt, firstParsed, laterParsed, decide_eq_true_eq] at ordered
      omega

theorem text_sort_can_claim_the_newer_turn_first :
    "2026-02-03T10:00:00.000001Z" < "2026-02-03T10:00:00Z" ∧
    beforeOrAt ⟨true, true, some 0, 1, 0⟩ ⟨true, true, some 1, 0, 1⟩ = true := by decide

#print axioms readback_keeps_exactly_the_selected_turns
#print axioms claim_selects_an_eligible_turn
#print axioms claim_has_no_earlier_eligible_instant
#print axioms text_sort_can_claim_the_newer_turn_first

end ChatQueuedTurnOrder
