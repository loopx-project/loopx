import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardReviewLinkCorrelation.lean`.

This models the choice of a review URL after parsing and indexing. A direct
match carries its distinct nonempty URLs. Fallback requires a complete REST
result. Candidates remain distinct when they lack a URL. The compatibility
flag records that at least one side lacks a node ID when no direct match
exists. Python tests cover the timestamps, identities and response shapes.
-/

namespace StewardReviewLinkCorrelation

def chooseLink (existing : Option Nat) (direct : Option (List Nat))
    (complete : Bool) (viewCount : Nat) (compatible : Bool) (fallback : List (Option Nat)) : Option Nat :=
  match existing with
  | some url => some url
  | none =>
    match direct with
    | some [url] => some url
    | some _ => none
    | none =>
      if complete = true ∧ viewCount = 1 ∧ compatible = true then
        match fallback with
        | [link] => link
        | _ => none
      else none

theorem existing_link_is_preserved
    (url count : Nat) (direct : Option (List Nat)) (complete compatible : Bool)
    (fallback : List (Option Nat)) :
    chooseLink (some url) direct complete count compatible fallback = some url := rfl

theorem unique_direct_id_takes_precedence (url count : Nat) (complete compatible : Bool)
    (fallback : List (Option Nat)) :
    chooseLink none (some [url]) complete count compatible fallback = some url := rfl

theorem conflicting_direct_links_do_not_fall_back (a b count : Nat) (remaining : List Nat)
    (complete compatible : Bool) (fallback : List (Option Nat)) :
    chooseLink none (some (a :: b :: remaining)) complete count compatible fallback = none := rfl

theorem both_sides_must_be_unique
    (a b : Option Nat) (remaining : List (Option Nat)) (viewCount : Nat)
    (ambiguous : viewCount ≠ 1) :
    chooseLink none none true 1 true (a :: b :: remaining) = none ∧
    chooseLink none none true viewCount true [a] = none := by
  simp [chooseLink, ambiguous]

theorem incomplete_results_cannot_supply_fallback (url : Nat) :
    chooseLink none none false 1 true [some url] = none := rfl

theorem conflicting_node_ids_disallow_fallback (url : Nat) :
    chooseLink none none true 1 false [some url] = none := rfl

theorem unique_compatible_candidate_supplies_its_link (link : Option Nat) :
    chooseLink none none true 1 true [link] = link := rfl

#print axioms existing_link_is_preserved
#print axioms unique_direct_id_takes_precedence
#print axioms conflicting_direct_links_do_not_fall_back
#print axioms both_sides_must_be_unique
#print axioms incomplete_results_cannot_supply_fallback
#print axioms conflicting_node_ids_disallow_fallback
#print axioms unique_compatible_candidate_supplies_its_link

end StewardReviewLinkCorrelation
