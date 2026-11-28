import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardFollowupOrder.lean`.

This models `runnable_advancement_todos` after eligibility and timestamp
parsing. Smaller priority numbers rank first. Timestamps are optional UTC
microseconds, with ranks for the original timestamp and Todo ID strings.
The Python regression covers the actual filters, parser, and sorted output.
-/

namespace StewardFollowupOrder

structure Todo where
  eligible : Bool
  priority : Nat
  time : Option Int
  rawRank : Nat
  idRank : Nat

abbrev tieBeforeOrAt (a b : Todo) : Prop :=
  a.rawRank < b.rawRank ∨ (a.rawRank = b.rawRank ∧ a.idRank ≤ b.idRank)

def timeBeforeOrAt (a b : Todo) : Bool :=
  match a.time, b.time with
  | some left, some right => decide (left < right ∨ (left = right ∧ tieBeforeOrAt a b))
  | none, some _ => true
  | some _, none => false
  | none, none => decide (tieBeforeOrAt a b)

def beforeOrAt (a b : Todo) : Bool :=
  decide (a.priority < b.priority ∨
    (a.priority = b.priority ∧ timeBeforeOrAt a b = true))

def runnable (todos : List Todo) : List Todo :=
  (todos.filter Todo.eligible).mergeSort beforeOrAt

theorem runnable_keeps_exactly_the_eligible_todos (todos : List Todo) :
    (runnable todos).Perm (todos.filter Todo.eligible) :=
  List.mergeSort_perm _ _

theorem runnable_is_ordered (todos : List Todo) :
    (runnable todos).Pairwise (fun a b => beforeOrAt a b = true) := by
  unfold runnable
  apply List.sorted_mergeSort
  · rintro ⟨_, ap, atime, ar, ai⟩ ⟨_, bp, btime, br, bi⟩ ⟨_, cp, ctime, cr, ci⟩ hab hbc
    cases atime <;> cases btime <;> cases ctime <;>
      simp [beforeOrAt, timeBeforeOrAt, tieBeforeOrAt] at * <;> omega
  · rintro ⟨_, ap, atime, ar, ai⟩ ⟨_, bp, btime, br, bi⟩
    cases atime <;> cases btime <;>
      simp [beforeOrAt, timeBeforeOrAt, tieBeforeOrAt, Bool.or_eq_true] <;> omega

theorem priority_precedes_time (todos : List Todo) :
    (runnable todos).Pairwise (fun a b => a.priority ≤ b.priority) := by
  apply (runnable_is_ordered todos).imp
  intro a b ordered
  simp only [beforeOrAt, decide_eq_true_eq] at ordered
  omega

theorem equal_priority_uses_chronological_order (todos : List Todo) :
    (runnable todos).Pairwise (fun a b => a.priority = b.priority →
      ∀ first second, a.time = some first → b.time = some second → first ≤ second) := by
  apply (runnable_is_ordered todos).imp
  intro a b ordered samePriority first second firstTime secondTime
  simp [beforeOrAt, timeBeforeOrAt, firstTime, secondTime, samePriority] at ordered
  omega

theorem text_sort_can_put_a_newer_equal_priority_todo_first :
    "2026-02-03T10:00:00.000001Z" < "2026-02-03T10:00:00Z" ∧
    beforeOrAt ⟨true, 1, some 0, 1, 0⟩ ⟨true, 1, some 1, 0, 1⟩ = true := by decide

#print axioms runnable_keeps_exactly_the_eligible_todos
#print axioms priority_precedes_time
#print axioms equal_priority_uses_chronological_order
#print axioms text_sort_can_put_a_newer_equal_priority_todo_first

end StewardFollowupOrder
