import Std

/-
Run with Lean 4.22.0 using
`lean -DwarningAsError=true regression/PeriodicReportWindow.lean`.

Each integer denotes a UTC microsecond. The model starts after timestamp
parsing. `pending_intent.py` compares the corresponding aware datetimes
when selecting completed Todo facts for a newly prepared cadence report.
The CLI regression exercises parsing and the persisted editorial request.
-/

namespace PeriodicReportWindow

def inWindow (start stop instant : Int) : Prop :=
  start ≤ instant ∧ instant < stop

def oldInWindow (start stop instant : Int) : Prop :=
  start < instant ∧ instant ≤ stop

theorem old_rule_assigns_cut_to_previous (start cut stop : Int) (left : start < cut) :
    oldInWindow start cut cut ∧ ¬oldInWindow cut stop cut := by
  simp [oldInWindow, left]

theorem cut_belongs_to_next (start cut stop : Int) (right : cut < stop) :
    ¬inWindow start cut cut ∧ inWindow cut stop cut := by
  simp [inWindow, right]

theorem adjacent_windows_partition (start cut stop instant : Int)
    (left : start ≤ cut) (right : cut ≤ stop) :
    inWindow start stop instant ↔ inWindow start cut instant ∨ inWindow cut stop instant := by
  unfold inWindow
  omega

theorem adjacent_windows_are_disjoint (start cut stop instant : Int) :
    ¬(inWindow start cut instant ∧ inWindow cut stop instant) := by
  unfold inWindow
  omega

#print axioms old_rule_assigns_cut_to_previous
#print axioms cut_belongs_to_next
#print axioms adjacent_windows_partition
#print axioms adjacent_windows_are_disjoint

end PeriodicReportWindow
