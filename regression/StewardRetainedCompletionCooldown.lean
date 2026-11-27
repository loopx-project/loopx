import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardRetainedCompletionCooldown.lean`.

The model starts after parsing. Times are integer microseconds and the existing
non-time retry checks supply the eligibility flag. Python tests exercise the
actual timestamp parser, floating point boundary comparisons near the fixture's
epoch, and both callers' retained receipt checks.
-/

namespace StewardRetainedCompletionCooldown

def retryDue (eligible : Bool) (now cooldown : Int) (completed : Option Int) : Bool :=
  match completed with
  | none => false
  | some instant => eligible && decide (cooldown ≤ now - instant)

theorem equal_completion_instants_agree (eligible : Bool) (now cooldown : Int)
    (upper lower : Option Int) (same : upper = lower) :
    retryDue eligible now cooldown upper = retryDue eligible now cooldown lower := by
  rw [same]

theorem before_cutoff_withholds_retry (eligible : Bool) (now cooldown completed : Int)
    (before : now < completed + cooldown) :
    retryDue eligible now cooldown (some completed) = false := by
  have short : ¬ cooldown ≤ now - completed := by omega
  simp [retryDue, short]

theorem at_or_after_cutoff_preserves_eligibility (eligible : Bool) (now cooldown completed : Int)
    (elapsed : completed + cooldown ≤ now) :
    retryDue eligible now cooldown (some completed) = eligible := by
  have ready : cooldown ≤ now - completed := by omega
  simp [retryDue, ready]

theorem unreadable_completion_cannot_retry (eligible : Bool) (now cooldown : Int) :
    retryDue eligible now cooldown none = false := rfl

-- The old parser supplied no instant for the lowercase UTC fixture.
theorem old_lowercase_rejection_changed_the_decision :
    retryDue true 60250000 60000000 (some 250000) = true ∧
    retryDue true 60250000 60000000 none = false := by decide

#print axioms equal_completion_instants_agree
#print axioms before_cutoff_withholds_retry
#print axioms at_or_after_cutoff_preserves_eligibility
#print axioms unreadable_completion_cannot_retry
#print axioms old_lowercase_rejection_changed_the_decision

end StewardRetainedCompletionCooldown
