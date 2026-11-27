import Std

/-
Run with Lean 4.22.0 using
`lean -DwarningAsError=true regression/ChatSessionRecency.lean`.

The model combines the existing Goal, Agent, channel and resumability
filters with the optional exact Goal-instance filter. Parsed update times
are UTC microseconds. The tie rank abstracts source order for the Python
store and Session ID order for the TypeScript selector. Unreadable times
keep their relative lexical order after dated Sessions. The regressions
exercise the actual timestamp parsers and both selection paths.
-/

namespace ChatSessionRecency

structure Scope where
  goal : Nat
  agent : Nat
  channel : Nat
  deriving DecidableEq

structure Session where
  scope : Scope
  instanceId : Nat
  resumable : Bool
  time : Option Int
  rawRank : Nat
  tieRank : Nat

def eligible (scope : Scope) (instanceId : Option Nat) (session : Session) : Bool :=
  decide (session.scope = scope ∧ session.resumable = true ∧
    (instanceId = none ∨ instanceId = some session.instanceId))

def newerOrTied (a b : Session) : Bool :=
  match a.time, b.time with
  | some left, some right => decide (right < left ∨ (left = right ∧ b.tieRank ≤ a.tieRank))
  | some _, none => true
  | none, some _ => false
  | none, none =>
    decide (b.rawRank < a.rawRank ∨ (a.rawRank = b.rawRank ∧ b.tieRank ≤ a.tieRank))

def latest (scope : Scope) (instanceId : Option Nat) (sessions : List Session) : Option Session :=
  ((sessions.filter (eligible scope instanceId)).mergeSort newerOrTied).head?

private theorem recency_sorted (sessions : List Session) :
    (sessions.mergeSort newerOrTied).Pairwise (fun a b => newerOrTied a b = true) := by
  apply List.sorted_mergeSort
  · rintro ⟨s, i, x, a, ar, atie⟩ ⟨t, j, y, b, br, btie⟩ ⟨u, k, z, c, cr, ctie⟩ hab hbc
    cases a <;> cases b <;> cases c <;> simp [newerOrTied] at * <;> omega
  · rintro ⟨s, i, x, a, ar, atie⟩ ⟨t, j, y, b, br, btie⟩
    cases a <;> cases b <;> simp [newerOrTied, Bool.or_eq_true] <;> omega

theorem latest_is_resumable_and_within_scope (scope : Scope) (instanceId : Option Nat)
    (sessions : List Session) (selected : Session) (found : latest scope instanceId sessions = some selected) :
    selected ∈ sessions ∧ selected.scope = scope ∧ selected.resumable = true ∧
      (instanceId = none ∨ instanceId = some selected.instanceId) := by
  obtain ⟨tail, head⟩ := List.head?_eq_some_iff.mp found
  have member : selected ∈ (sessions.filter (eligible scope instanceId)).mergeSort newerOrTied := by
    simp [head]
  obtain ⟨stored, accepted⟩ := List.mem_filter.mp (List.mem_mergeSort.mp member)
  exact ⟨stored, by simpa [eligible] using accepted⟩

theorem latest_precedes_all_eligible (scope : Scope) (instanceId : Option Nat)
    (sessions : List Session) (selected : Session) (found : latest scope instanceId sessions = some selected)
    (other : Session) (stored : other ∈ sessions) (accepted : eligible scope instanceId other = true) :
    newerOrTied selected other = true := by
  obtain ⟨tail, head⟩ := List.head?_eq_some_iff.mp found
  have ordered := recency_sorted (sessions.filter (eligible scope instanceId))
  rw [head, List.pairwise_cons] at ordered
  have member : other ∈ (sessions.filter (eligible scope instanceId)).mergeSort newerOrTied :=
    List.mem_mergeSort.mpr (List.mem_filter.mpr ⟨stored, accepted⟩)
  rw [head, List.mem_cons] at member
  rcases member with rfl | tailMember
  · cases time : other.time <;> simp [newerOrTied, time]
  · exact ordered.1 other tailMember

theorem old_text_order_can_choose_the_earlier_session :
    "2026-02-03T10:00:00Z" > "2026-02-03T10:00:00.000001Z" ∧
      newerOrTied ⟨⟨0, 0, 0⟩, 0, true, some 1, 0, 0⟩
        ⟨⟨0, 0, 0⟩, 0, true, some 0, 1, 1⟩ = true := by decide

#print axioms latest_is_resumable_and_within_scope
#print axioms latest_precedes_all_eligible
#print axioms old_text_order_can_choose_the_earlier_session

end ChatSessionRecency
