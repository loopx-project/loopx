import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardIdleMonitorBackoff.lean`.

Times are integer microseconds. The model begins with the scheduler parser's
accepted clock and offset fields. An omitted offset means UTC. The portfolio
pump takes the minimum parsed deadline of an already filtered nonempty list.
The managed pump has one deadline. The Python regression exercises parsing,
the idle routes and the monotonic hints without dispatch.
-/

namespace StewardIdleMonitorBackoff

structure Deadline where
  localTime : Int
  offset : Option Int

def Deadline.utc (stamp : Deadline) : Int :=
  stamp.localTime - stamp.offset.getD 0

def earliest (first : Deadline) (rest : List Deadline) : Int :=
  (rest.map Deadline.utc).foldl min first.utc

def managedWait (now due : Int) : Int :=
  min 60000000 (max 0 (due - now))

def portfolioWait (existing now due : Int) : Int :=
  if now < due then min 60000000 (due - now) else existing

theorem earliest_le_every_candidate (first : Deadline) (rest : List Deadline)
    (stamp : Deadline) (present : stamp ∈ first :: rest) :
    earliest first rest ≤ stamp.utc := by
  have foldBound (dates : List Int) (initial : Int) (target : Int)
      (bound : initial ≤ target ∨ target ∈ dates) : dates.foldl min initial ≤ target := by
    induction dates generalizing initial with
    | nil => simp at bound ⊢; omega
    | cons next tail ih =>
      simp only [List.foldl_cons]
      rcases bound with le | inDates
      · exact ih _ (Or.inl (by omega))
      · rcases List.mem_cons.mp inDates with rfl | inTail
        · exact ih _ (Or.inl (by omega))
        · exact ih _ (Or.inr inTail)
  apply foldBound
  rcases List.mem_cons.mp present with rfl | inRest
  · exact Or.inl (by omega)
  · exact Or.inr (List.mem_map.mpr ⟨stamp, inRest, rfl⟩)

theorem waits_agree_for_future_deadlines (existing now due : Int) (future : now < due) :
    portfolioWait existing now due = managedWait now due := by
  simp [portfolioWait, future, managedWait]
  omega

theorem managed_wait_is_bounded (now due : Int) :
    0 ≤ managedWait now due ∧ managedWait now due ≤ 60000000 := by
  unfold managedWait
  omega

theorem overdue_portfolio_keeps_its_hint (existing now due : Int) (overdue : due ≤ now) :
    portfolioWait existing now due = existing := by
  simp [portfolioWait, show ¬now < due by omega]

def oldComparable (a b : Deadline) : Bool :=
  a.offset.isSome == b.offset.isSome

theorem mixed_formats_have_a_bounded_wait :
    oldComparable ⟨250000, none⟩ ⟨-17989750000, some (-18000000000)⟩ = false ∧
    managedWait 0 (earliest ⟨250000, none⟩ [⟨-17989750000, some (-18000000000)⟩]) = 250000 := by
  decide

#print axioms earliest_le_every_candidate
#print axioms waits_agree_for_future_deadlines
#print axioms managed_wait_is_bounded
#print axioms overdue_portfolio_keeps_its_hint
#print axioms mixed_formats_have_a_bounded_wait

end StewardIdleMonitorBackoff
