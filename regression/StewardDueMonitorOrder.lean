import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardDueMonitorOrder.lean`.

Eligible rows have deadlines parsed to UTC microseconds. The raw deadline
and Todo ID ranks represent the two Python string tie breakers. An independent
predicate represents the pump skipping a row before its first attempt. The
Python regression covers the actual filters, parser, and dispatch arguments.
-/

namespace StewardDueMonitorOrder

structure Monitor where
  eligible : Bool
  deadline : Int
  rawRank : Nat
  idRank : Nat

def beforeOrAt (a b : Monitor) : Bool :=
  decide (a.deadline < b.deadline ∨ (a.deadline = b.deadline ∧
    (a.rawRank < b.rawRank ∨ (a.rawRank = b.rawRank ∧ a.idRank ≤ b.idRank))))

def due (rows : List Monitor) : List Monitor :=
  (rows.filter Monitor.eligible).mergeSort beforeOrAt

def attempts (rows : List Monitor) (ready : Monitor → Bool) : List Monitor :=
  (due rows).filter ready

theorem due_keeps_exactly_the_eligible_rows (rows : List Monitor) :
    (due rows).Perm (rows.filter Monitor.eligible) :=
  List.mergeSort_perm _ _

theorem due_is_ordered (rows : List Monitor) :
    (due rows).Pairwise (fun a b => beforeOrAt a b = true) := by
  unfold due
  apply List.sorted_mergeSort
  · intro a b c ab bc
    simp only [beforeOrAt, decide_eq_true_eq] at *
    omega
  · intro a b
    simp only [beforeOrAt, Bool.or_eq_true, decide_eq_true_eq]
    omega

theorem first_attempt_has_earliest_ready_deadline
    (rows : List Monitor) (ready : Monitor → Bool) (first other : Monitor)
    (picked : (attempts rows ready).head? = some first)
    (present : other ∈ rows) (eligible : other.eligible = true) (canRun : ready other = true) :
    first.deadline ≤ other.deadline := by
  have dueMember := (due_keeps_exactly_the_eligible_rows rows).mem_iff.mpr
    (List.mem_filter.mpr ⟨present, eligible⟩)
  have member : other ∈ attempts rows ready := List.mem_filter.mpr ⟨dueMember, canRun⟩
  have sorted : (attempts rows ready).Pairwise (fun a b => beforeOrAt a b = true) :=
    (due_is_ordered rows).filter _
  cases found : attempts rows ready with
  | nil => simp [found] at picked
  | cons head tail =>
    have same : head = first := by simpa [found] using picked
    rw [found, same] at member sorted
    rcases List.mem_cons.mp member with equal | inTail
    · simp [equal]
    · have ordered := (List.pairwise_cons.mp sorted).1 other inTail
      simp only [beforeOrAt, decide_eq_true_eq] at ordered
      omega

theorem text_sort_can_attempt_a_later_deadline_first :
    "2000-02-03T10:00:00.000001Z" < "2000-02-03T10:00:00Z" ∧
    beforeOrAt ⟨true, 0, 1, 0⟩ ⟨true, 1, 0, 1⟩ = true := by decide

#print axioms due_keeps_exactly_the_eligible_rows
#print axioms first_attempt_has_earliest_ready_deadline
#print axioms text_sort_can_attempt_a_later_deadline_first

end StewardDueMonitorOrder
