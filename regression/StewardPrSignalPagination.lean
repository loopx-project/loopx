import Std

namespace StewardPrSignalPagination

/- The Python collector maps an explicit false hasNextPage to terminal, a true
   marker with a valid opaque cursor to next, and invalid metadata/errors to
   invalid. Natural numbers stand for cursor identities and PR numbers. This
   model tracks page coverage, omitting PR normalization, deduplication and
   hashing. It assumes the provider's terminal marker describes the scan. -/
inductive PageMetadata where
  | terminal
  | next (cursor : Nat)
  | invalid
  deriving DecidableEq

structure Page where
  metadata : PageMetadata
  prs : List Nat

def collect : Nat → List Nat → List Page → Option (List Nat)
  | 0, _, _ => none
  | _ + 1, _, [] => none
  | budget + 1, seen, page :: rest =>
    match page.metadata with
    | .terminal => some page.prs
    | .invalid => none
    | .next cursor =>
      if cursor ∈ seen then none
      else (collect budget (cursor :: seen) rest).map (page.prs ++ ·)

/-- A published observation requires a terminal page inside the request budget. -/
theorem complete_has_bounded_terminal {budget : Nat} {seen : List Nat}
    {pages : List Page} {prs : List Nat} (h : collect budget seen pages = some prs) :
    ∃ i, i < budget ∧ (pages[i]?).map Page.metadata = some .terminal := by
  induction budget generalizing seen pages prs with
  | zero => simp [collect] at h
  | succ budget ih =>
    cases pages with
    | nil => simp [collect] at h
    | cons page rest =>
      cases metadata : page.metadata with
      | terminal => exact ⟨0, Nat.zero_lt_succ _, by simpa using metadata⟩
      | invalid => simp [collect, metadata] at h
      | next cursor =>
        by_cases repeated : cursor ∈ seen
        · simp [collect, metadata, repeated] at h
        · cases restResult : collect budget (cursor :: seen) rest with
          | none => simp [collect, metadata, repeated, restResult] at h
          | some restPrs =>
            obtain ⟨i, hi, terminal⟩ := ih restResult
            exact ⟨i + 1, Nat.succ_lt_succ hi, by simpa using terminal⟩

-- Core keeps the prior observation and emits no material change on unavailable.
def observe (previous : List Nat) : Option (List Nat) → List Nat × Bool
  | none => (previous, false)
  | some current => (current, previous != current)

theorem incomplete_keeps_previous (previous : List Nat) :
    observe previous none = (previous, false) := rfl

theorem repeated_cursor_is_unavailable (remaining cursor : Nat) (seen : List Nat)
    (prs : List Nat) (rest : List Page) (h : cursor ∈ seen) :
    collect (remaining + 1) seen (⟨.next cursor, prs⟩ :: rest) = none := by
  simp [collect, h]

def continuation (cursor : Nat) : Page := ⟨.next cursor, [cursor]⟩

def firstFive : List Page := [1, 2, 3, 4, 5].map continuation

/-- The old collector published the capped prefix and signalled a change.
    With the same input, a bounded scan preserves the previous observation. -/
theorem capped_prefix_counterexample :
    let previous := [1, 2, 3, 4, 5, 6]
    let pages := firstFive ++ [⟨.terminal, [6]⟩]
    (observe previous (some ((pages.take 5).flatMap Page.prs))).2 = true ∧
    observe previous (collect 5 [] pages) = (previous, false) := by decide

theorem terminal_on_last_allowed_page_is_complete :
    collect 5 [] ([1, 2, 3, 4].map continuation ++ [⟨.terminal, [5]⟩]) =
      some [1, 2, 3, 4, 5] := by decide

#print axioms complete_has_bounded_terminal
#print axioms incomplete_keeps_previous
#print axioms repeated_cursor_is_unavailable
#print axioms capped_prefix_counterexample
#print axioms terminal_on_last_allowed_page_is_complete

end StewardPrSignalPagination
