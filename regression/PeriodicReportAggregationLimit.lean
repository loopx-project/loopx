import Std

/-
Run with Lean 4.22.0 using
`lean -DwarningAsError=true regression/PeriodicReportAggregationLimit.lean`.

The model uses UTC instants in integer microseconds after parsing. Python
compares the corresponding `timedelta` values in `runtime_producer.py`.
Other request and event validations are outside this model. The old rule
models whole-second truncation on a positive duration. The CLI regression
exercises the actual float conversion and both boundary decisions.
-/

namespace PeriodicReportAggregationLimit

def allowsSegment (start stop seconds : Int) : Prop :=
  start < stop ∧ stop - start ≤ seconds * 1000000

def oldAllowsSegment (start stop seconds : Int) : Prop :=
  start < stop ∧ (stop - start) / 1000000 ≤ seconds

theorem accepts_exactly_nonempty_segments_within_limit (start stop seconds : Int) :
    allowsSegment start stop seconds ↔ start < stop ∧ stop ≤ start + seconds * 1000000 := by
  unfold allowsSegment
  omega

theorem exact_limit_is_allowed (start seconds : Int) (positive : 0 < seconds) :
    allowsSegment start (start + seconds * 1000000) seconds := by
  unfold allowsSegment
  omega

theorem old_rule_admits_subsecond_overage (start seconds excess : Int)
    (nonnegative : 0 ≤ seconds) (positive : 0 < excess) (subsecond : excess < 1000000) :
    let stop := start + seconds * 1000000 + excess
    oldAllowsSegment start stop seconds ∧ ¬ allowsSegment start stop seconds := by
  dsimp [oldAllowsSegment, allowsSegment]
  omega

#print axioms accepts_exactly_nonempty_segments_within_limit
#print axioms exact_limit_is_allowed
#print axioms old_rule_admits_subsecond_overage

end PeriodicReportAggregationLimit
