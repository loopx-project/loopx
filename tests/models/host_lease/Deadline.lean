import Init

namespace HostLeaseDeadline

-- Elapsed time comes from the supervisor's process-local monotonic clock.
def effectiveTime (priorWall wall elapsed : Int) : Int :=
  max wall (priorWall + elapsed)

-- The authority proof has already passed the identity and activity checks.
def proofDeadline (currentVersion newVersion : Nat) (currentExpiry newExpiry : Int) : Int :=
  if currentVersion < newVersion then newExpiry else min currentExpiry newExpiry

def acceptProof (observed currentExpiry : Int) (currentVersion newVersion : Nat)
    (newExpiry : Int) : Option Int :=
  if currentExpiry ≤ observed then none
  else some (proofDeadline currentVersion newVersion currentExpiry newExpiry)

theorem rollback_preserves_monotonic_elapsed_time (priorWall wall elapsed : Int)
    (rollback : wall ≤ priorWall + elapsed) :
    effectiveTime priorWall wall elapsed = priorWall + elapsed := by
  unfold effectiveTime
  exact Int.max_eq_right rollback

theorem effective_time_includes_elapsed (priorWall wall elapsed : Int) :
    priorWall + elapsed ≤ effectiveTime priorWall wall elapsed := by
  unfold effectiveTime
  exact Int.le_max_right _ _

theorem unchanged_version_cannot_extend (version : Nat) (currentExpiry newExpiry : Int) :
    proofDeadline version version currentExpiry newExpiry ≤ currentExpiry := by
  simp only [proofDeadline, Nat.lt_irrefl, if_false]
  exact Int.min_le_left _ _

theorem new_proof_cannot_revive_elapsed_deadline (priorWall wall elapsed currentExpiry newExpiry : Int)
    (currentVersion newVersion : Nat) (expired : currentExpiry ≤ priorWall + elapsed) :
    acceptProof (effectiveTime priorWall wall elapsed) currentExpiry currentVersion
      newVersion newExpiry = none := by
  have effectiveExpired := Int.le_trans expired (effective_time_includes_elapsed priorWall wall elapsed)
  simp [acceptProof, effectiveExpired]

theorem validated_new_version_before_expiry_can_extend (observed currentExpiry newExpiry : Int)
    (currentVersion newVersion : Nat) (before : observed < currentExpiry)
    (advanced : currentVersion < newVersion) :
    acceptProof observed currentExpiry currentVersion newVersion newExpiry = some newExpiry := by
  have notExpired : ¬currentExpiry ≤ observed := by omega
  simp [acceptProof, notExpired, proofDeadline, advanced]

theorem raw_wall_can_show_time_left_after_real_expiry :
    let priorWall : Int := 100000
    let expiry : Int := 102400
    let wall : Int := 70000
    let elapsed : Int := 2400
    wall < expiry ∧ expiry ≤ effectiveTime priorWall wall elapsed := by decide

#print axioms rollback_preserves_monotonic_elapsed_time
#print axioms effective_time_includes_elapsed
#print axioms unchanged_version_cannot_extend
#print axioms new_proof_cannot_revive_elapsed_deadline
#print axioms validated_new_version_before_expiry_can_extend
#print axioms raw_wall_can_show_time_left_after_real_expiry

end HostLeaseDeadline
