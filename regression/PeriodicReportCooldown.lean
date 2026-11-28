import Std

/-
Run with Lean 4.22.0 using
`lean -DwarningAsError=true regression/PeriodicReportCooldown.lean`.

The model uses integer UTC microseconds for parsed instants and durations.
It covers an otherwise eligible trigger without a cooldown bypass. Python
compares a datetime difference with the configured duration, then uses
their sum to advertise the cutoff when suppression applies. The CLI
regression demonstrates actual float rounding and calendar limits after
JSON parsing. The model assumes that the correct cutoff is representable.
-/

namespace PeriodicReportCooldown

def cutoff (delivered interval : Int) : Int := delivered + interval

def cooling (delivered evaluated interval : Int) : Prop :=
  evaluated - delivered < interval

theorem cooling_iff_before_cutoff (delivered evaluated interval : Int) :
    cooling delivered evaluated interval ↔ evaluated < cutoff delivered interval := by
  unfold cooling cutoff
  omega

theorem cutoff_is_first_eligible_instant (delivered interval : Int) :
    cooling delivered (cutoff delivered interval - 1) interval ∧
      ¬ cooling delivered (cutoff delivered interval) interval := by
  unfold cooling cutoff
  omega

theorem any_nonzero_drift_misstates_cutoff (delivered interval drift : Int)
    (nonzero : drift ≠ 0) :
    let exact := cutoff delivered interval
    let advertised := exact + drift
    (advertised < exact ∧ cooling delivered advertised interval) ∨
      (exact < advertised ∧ ¬ cooling delivered exact interval) := by
  dsimp [cooling, cutoff]
  omega

#print axioms cooling_iff_before_cutoff
#print axioms cutoff_is_first_eligible_instant
#print axioms any_nonzero_drift_misstates_cutoff

end PeriodicReportCooldown
