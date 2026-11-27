import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardCoreMonitorCooldown.lean`.

The model starts with a parsed UTC completion in microseconds and the exact
rational value of the finite wire clock sample in epoch seconds. Its positive
denominator corresponds to the binary clock exponent. TypeScript tests cover
decoding, timestamp parsing, and the surrounding journal and identity checks.
-/

namespace StewardCoreMonitorCooldown

def cutoff (completedUs : Int) : Int := completedUs + 300000000

def elapsed (completedUs numerator denominator : Int) : Prop :=
  cutoff completedUs ≤ (numerator * 1000000) / denominator

def canRecover (evidence : Bool) (completedUs numerator denominator : Int) : Prop :=
  evidence = true ∧ elapsed completedUs numerator denominator

theorem floor_preserves_the_exact_cutoff
    (completedUs numerator denominator : Int) (positive : 0 < denominator) :
    elapsed completedUs numerator denominator ↔
      cutoff completedUs * denominator ≤ numerator * 1000000 :=
  Int.le_ediv_iff_mul_le positive

theorem last_microsecond_still_waits (completedUs : Int) :
    ¬elapsed completedUs (cutoff completedUs - 1) 1000000 := by
  rw [floor_preserves_the_exact_cutoff _ _ _ (by decide)]
  omega

theorem exact_cutoff_can_recover (completedUs : Int) :
    canRecover true completedUs (cutoff completedUs) 1000000 := by
  unfold canRecover
  rw [floor_preserves_the_exact_cutoff _ _ _ (by decide)]
  simp

theorem elapsed_time_cannot_replace_evidence (completedUs numerator denominator : Int) :
    ¬canRecover false completedUs numerator denominator := by
  simp [canRecover]

-- Millisecond truncation accepted this synthetic one-microsecond-early case.
theorem old_truncation_can_release_early :
    (2000300000000 : Int) / 1000 - (2000000000001 : Int) / 1000 ≥ 300000 ∧
      ¬elapsed 2000000000001 2000300 1 := by
  dsimp [elapsed, cutoff]
  decide

#print axioms floor_preserves_the_exact_cutoff
#print axioms last_microsecond_still_waits
#print axioms exact_cutoff_can_recover
#print axioms elapsed_time_cannot_replace_evidence
#print axioms old_truncation_can_release_early

end StewardCoreMonitorCooldown
