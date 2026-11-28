import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardRetryCooldownPrecision.lean`.

The model assumes a parsed completion instant in UTC microseconds. It checks
the calendar-independent comparison used by both Python retry callers. The
native clock yields integer nanoseconds. A supplied finite clock value has
the exact numerator and positive denominator returned by the runtime.
-/

namespace StewardRetryCooldownPrecision

def dueUs (completedUs cooldownSeconds : Int) : Int :=
  completedUs + cooldownSeconds * 1000000

def nativeElapsed (completedUs cooldownSeconds clockNs : Int) : Prop :=
  dueUs completedUs cooldownSeconds ≤ clockNs / 1000

def overrideElapsed (completedUs cooldownSeconds numerator denominator : Int) : Prop :=
  dueUs completedUs cooldownSeconds ≤ (numerator * 1000000) / denominator

theorem native_elapsed_iff_exact_deadline (completedUs cooldownSeconds clockNs : Int) :
    nativeElapsed completedUs cooldownSeconds clockNs ↔
      dueUs completedUs cooldownSeconds * 1000 ≤ clockNs :=
  Int.le_ediv_iff_mul_le (by decide)

theorem override_elapsed_iff_exact_deadline
    (completedUs cooldownSeconds numerator denominator : Int) (positive : 0 < denominator) :
    overrideElapsed completedUs cooldownSeconds numerator denominator ↔
      dueUs completedUs cooldownSeconds * denominator ≤ numerator * 1000000 :=
  Int.le_ediv_iff_mul_le positive

theorem last_nanosecond_still_waits (completedUs cooldownSeconds : Int) :
    ¬nativeElapsed completedUs cooldownSeconds (dueUs completedUs cooldownSeconds * 1000 - 1) := by
  unfold nativeElapsed
  omega

theorem exact_cutoff_is_inclusive (completedUs cooldownSeconds : Int) :
    nativeElapsed completedUs cooldownSeconds (dueUs completedUs cooldownSeconds * 1000) := by
  rw [native_elapsed_iff_exact_deadline]
  omega

-- The retained completion is one microsecond after this whole second.
theorem rounding_can_open_the_far_future_gate_early :
    let wholeUs := 253402300200 * 1000000
    nativeElapsed wholeUs 60 ((wholeUs + 60000000) * 1000) ∧
      ¬nativeElapsed (wholeUs + 1) 60 ((wholeUs + 60000000) * 1000) := by
  dsimp only [nativeElapsed, dueUs]
  decide

#print axioms native_elapsed_iff_exact_deadline
#print axioms override_elapsed_iff_exact_deadline
#print axioms last_nanosecond_still_waits
#print axioms exact_cutoff_is_inclusive
#print axioms rounding_can_open_the_far_future_gate_early

end StewardRetryCooldownPrecision
