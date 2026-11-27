import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardReviewEvidenceTimestamp.lean`.

This model starts with a parsed UTC instant in microseconds and the role already
usable by the GitHub evidence owner. It checks the contribution of comments
to reply ordering and paginated coverage. The Python fixture covers parsing,
role admission, and preservation of the original anchor string.
-/

namespace StewardReviewEvidenceTimestamp

inductive Role where
  | author | maintainer | other
  deriving DecidableEq

structure Comment where
  time : Option Int
  role : Role

def rolePriority : Role → Nat
  | .author => 1
  | _ => 0

def postAnchorReply (anchor : Int) (comment : Comment) : Option (Int × Role) :=
  match comment.time with
  | none => none
  | some instant =>
    if anchor < instant ∧ comment.role ≠ .other then some (instant, comment.role) else none

def reachedAnchor (anchor : Int) (comment : Comment) : Bool :=
  match comment.time with
  | none => false
  | some instant => decide (instant ≤ anchor)

def replyBeforeOrAt (left right : Int × Role) : Bool :=
  decide (left.1 < right.1 ∨
    (left.1 = right.1 ∧ rolePriority left.2 ≤ rolePriority right.2))

def authorReplied (anchor : Int) (comments : List Comment) : Bool :=
  match ((comments.filterMap (postAnchorReply anchor)).mergeSort replyBeforeOrAt).getLast? with
  | some (_, .author) => true
  | _ => false

def coverageComplete (earlierPage : Bool) (anchor : Int) (comments : List Comment) : Bool :=
  !earlierPage || comments.any (reachedAnchor anchor) ||
    comments.any (fun comment => (postAnchorReply anchor comment).isSome)

theorem unreadable_comment_adds_no_evidence
    (comments : List Comment) (anchor : Int) (earlierPage : Bool) (role : Role) :
    authorReplied anchor (comments ++ [⟨none, role⟩]) = authorReplied anchor comments ∧
    coverageComplete earlierPage anchor (comments ++ [⟨none, role⟩]) =
      coverageComplete earlierPage anchor comments := by
  simp [authorReplied, coverageComplete, postAnchorReply, reachedAnchor, List.filterMap_append,
    List.any_append]

theorem equal_anchor_reaches_coverage_without_a_reply :
    authorReplied 0 [⟨some 0, .author⟩] = false ∧
    coverageComplete true 0 [⟨some 0, .author⟩] = true := by
  simp [authorReplied, coverageComplete, postAnchorReply, reachedAnchor]

theorem later_maintainer_reply_supersedes_author :
    authorReplied 0 [⟨some 1, .author⟩, ⟨some 2, .maintainer⟩] = false ∧
    coverageComplete true 0 [⟨some 1, .author⟩, ⟨some 2, .maintainer⟩] = true := by
  simp [authorReplied, coverageComplete, postAnchorReply, reachedAnchor, replyBeforeOrAt,
    rolePriority, List.mergeSort]

-- Rejecting the lowercase UTC comment previously erased this parsed evidence.
theorem missing_valid_utc_instant_changes_both_results :
    authorReplied 0 [⟨some 1, .author⟩] = true ∧
    coverageComplete true 0 [⟨some 1, .author⟩] = true ∧
    authorReplied 0 [⟨none, .author⟩] = false ∧
    coverageComplete true 0 [⟨none, .author⟩] = false := by
  simp [authorReplied, coverageComplete, postAnchorReply, reachedAnchor]

#print axioms unreadable_comment_adds_no_evidence
#print axioms equal_anchor_reaches_coverage_without_a_reply
#print axioms later_maintainer_reply_supersedes_author
#print axioms missing_valid_utc_instant_changes_both_results

end StewardReviewEvidenceTimestamp
