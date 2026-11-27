import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardStalledAgePrecision.lean`.

The model starts with parsed UTC timestamps and a positive rational cutoff
expressed in microseconds from the configured decimal hours. The runtime
regression covers the timestamp parser, decimal conversion, and actual
scoped observation and material buckets.
-/

namespace StewardStalledAgePrecision

def cutoffMicros (numerator denominator : Int) : Int :=
  (numerator + denominator - 1) / denominator

theorem integral_age_matches_fractional_cutoff
    (age numerator denominator : Int) (positive : 0 < denominator) :
    cutoffMicros numerator denominator ≤ age ↔ numerator ≤ age * denominator := by
  unfold cutoffMicros
  rw [Int.ediv_le_iff_le_mul positive]
  omega

def includeItem (hasCoverage inScope authorReply : Bool) (age cutoff : Int) : Bool :=
  hasCoverage && inScope && (authorReply || decide (cutoff ≤ age))

theorem early_age_without_reply_is_excluded (age cutoff : Int) (early : age < cutoff) :
    includeItem true true false age cutoff = false := by
  have below : ¬cutoff ≤ age := by omega
  simp [includeItem, below]

theorem author_reply_still_requires_coverage_and_scope
    (hasCoverage inScope : Bool) (age cutoff : Int) :
    includeItem hasCoverage inScope true age cutoff = (hasCoverage && inScope) := by
  simp [includeItem]

inductive Bucket where
  | under24 | from24 | from72 | from168
  deriving DecidableEq

def bucket (age : Int) : Bucket :=
  if age ≥ 604800000000 then .from168
  else if age ≥ 259200000000 then .from72
  else if age ≥ 86400000000 then .from24
  else .under24

theorem bucket_changes_at_each_exact_boundary :
    bucket (86400000000 - 1) = .under24 ∧ bucket 86400000000 = .from24 ∧
    bucket (259200000000 - 1) = .from24 ∧ bucket 259200000000 = .from72 ∧
    bucket (604800000000 - 1) = .from72 ∧ bucket 604800000000 = .from168 := by
  simp [bucket]

-- The retained anchor is one microsecond after a whole second.
theorem old_milliseconds_could_promote_the_bucket :
    let anchor : Int := 1
    let observed : Int := 86400000000
    bucket ((observed / 1000 - anchor / 1000) * 1000) = .from24 ∧
      bucket (observed - anchor) = .under24 := by
  simp [bucket]

#print axioms integral_age_matches_fractional_cutoff
#print axioms early_age_without_reply_is_excluded
#print axioms author_reply_still_requires_coverage_and_scope
#print axioms bucket_changes_at_each_exact_boundary
#print axioms old_milliseconds_could_promote_the_bucket

end StewardStalledAgePrecision
