import Std

namespace StewardPrSignalPagination

/- The Python collector maps an explicit false hasNextPage to terminal, a true
   marker with a valid opaque cursor to next, and invalid metadata/errors to
   invalid. Natural numbers stand for cursor identities and PR numbers.
   Unusable nodes or identities without a natural number map to none;
   zero also fails validation. This model tracks page and identity coverage,
   omitting PR normalization, deduplication and hashing. It assumes the
   provider's terminal marker describes the scan. -/
inductive PageMetadata where
  | terminal
  | next (cursor : Nat)
  | invalid
  deriving DecidableEq

structure Page where
  metadata : PageMetadata
  nodes : List (Option Nat)

def validateNodes : List (Option Nat) → Option (List Nat)
  | [] => some []
  | some (number + 1) :: rest => (validateNodes rest).map ((number + 1) :: ·)
  | _ :: _ => none

def collect : Nat → List Nat → List Page → Option (List Nat)
  | 0, _, _ => none
  | _ + 1, _, [] => none
  | budget + 1, seen, page :: rest =>
    match validateNodes page.nodes, page.metadata with
    | some prs, .terminal => some prs
    | some prs, .next cursor =>
      if cursor ∈ seen then none
      else (collect budget (cursor :: seen) rest).map (prs ++ ·)
    | _, _ => none

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
      cases validation : validateNodes page.nodes with
      | none => simp [collect, validation] at h
      | some pagePrs =>
        cases metadata : page.metadata with
        | terminal => exact ⟨0, Nat.zero_lt_succ _, by simpa using metadata⟩
        | invalid => simp [collect, validation, metadata] at h
        | next cursor =>
          by_cases repeated : cursor ∈ seen
          · simp [collect, validation, metadata, repeated] at h
          · cases restResult : collect budget (cursor :: seen) rest with
            | none => simp [collect, validation, metadata, repeated, restResult] at h
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
    (nodes : List (Option Nat)) (rest : List Page) (h : cursor ∈ seen) :
    collect (remaining + 1) seen (⟨.next cursor, nodes⟩ :: rest) = none := by
  cases validation : validateNodes nodes <;> simp [collect, validation, h]

theorem validated_nodes_keep_every_positive_number {nodes : List (Option Nat)} {prs : List Nat}
    (h : validateNodes nodes = some prs) : nodes = prs.map some ∧ ∀ number ∈ prs, 0 < number := by
  induction nodes generalizing prs with
  | nil =>
    have empty : [] = prs := Option.some.inj h
    subst prs
    simp
  | cons node rest ih =>
    cases node with
    | none => simp [validateNodes] at h
    | some number =>
      cases number with
      | zero => simp [validateNodes] at h
      | succ number =>
        cases checked : validateNodes rest with
        | none => simp [validateNodes, checked] at h
        | some restPrs =>
          have all : (number + 1) :: restPrs = prs := by simpa [validateNodes, checked] using h
          subst prs
          obtain ⟨restKept, restPositive⟩ := ih checked
          constructor
          · simpa using restKept
          · intro pr member
            rcases List.mem_cons.mp member with here | later
            · subst pr
              exact Nat.zero_lt_succ _
            · exact restPositive pr later

theorem invalid_page_discards_the_prefix
    (budget cursor : Nat) (seen : List Nat) (earlier invalidNodes : List (Option Nat))
    (metadata : PageMetadata) (rest : List Page) (previous : List Nat)
    (h : validateNodes invalidNodes = none) :
    observe previous (collect (budget + 2) seen
      (⟨.next cursor, earlier⟩ :: ⟨metadata, invalidNodes⟩ :: rest)) = (previous, false) := by
  cases validation : validateNodes earlier <;> simp [collect, validation, h, observe]

def continuation (cursor : Nat) : Page := ⟨.next cursor, [some cursor]⟩

def firstFive : List Page := [1, 2, 3, 4, 5].map continuation

/-- The old collector published the capped prefix and signalled a change.
    With the same input, a bounded scan preserves the previous observation. -/
theorem capped_prefix_counterexample :
    let previous := [1, 2, 3, 4, 5, 6]
    let pages := firstFive ++ [⟨.terminal, [some 6]⟩]
    let cappedPrs := (pages.take 5).flatMap fun page => page.nodes.filterMap id
    (observe previous (some cappedPrs)).2 = true ∧
    observe previous (collect 5 [] pages) = (previous, false) := by decide

theorem terminal_on_last_allowed_page_is_complete :
    collect 5 [] ([1, 2, 3, 4].map continuation ++ [⟨.terminal, [some 5]⟩]) =
      some [1, 2, 3, 4, 5] := by decide

theorem null_node_on_terminal_page_counterexample :
    let previous := [1, 2, 3]
    let pages := [continuation 1, ⟨.terminal, [some 2, none]⟩]
    let dropped := pages.flatMap fun page => page.nodes.filterMap id
    (observe previous (some dropped)).2 = true ∧
    observe previous (collect 5 [] pages) = (previous, false) := by decide

theorem genuine_empty_terminal_page_is_complete :
    collect 5 [] [⟨.terminal, []⟩] = some [] := rfl

/- A missing or invalid GitHub totalCount maps to none. The guard below marks
   count drift as invalid before returning a result. Deduplicating the collected
   numbers models the cardinality of the Python collector's dictionary, rather
   than its selected PR payloads or result order. Count agreement does not imply
   that the provider presents an atomic snapshot. -/
structure CountedPage where
  page : Page
  reportedTotal : Option Nat

def checkPageTotal (expected : Nat) (response : CountedPage) : Page :=
  if response.reportedTotal = some expected then response.page
  else { response.page with metadata := .invalid }

def collectCovered (budget : Nat) (pages : List CountedPage) : Option (List Nat) := do
  let first ← pages.head?
  let expected ← first.reportedTotal
  let prs ← collect budget [] (pages.map (checkPageTotal expected))
  let distinct := prs.eraseDups
  if distinct.length = expected then some distinct else none

theorem accepted_distinct_count_matches_report {budget : Nat} {first : CountedPage}
    {rest : List CountedPage} {covered : List Nat}
    (h : collectCovered budget (first :: rest) = some covered) :
    first.reportedTotal = some covered.length := by
  cases reported : first.reportedTotal with
  | none => simp [collectCovered, reported] at h
  | some expected =>
    cases scan : collect budget [] (checkPageTotal expected first :: rest.map (checkPageTotal expected)) with
    | none => simp [collectCovered, reported, scan] at h
    | some prs =>
      by_cases count : prs.eraseDups.length = expected
      · have kept : prs.eraseDups = covered := by
          simpa [collectCovered, reported, scan, count] using h
        rw [kept] at count
        exact congrArg some count.symm
      · simp [collectCovered, reported, scan, count] at h

theorem count_drift_preserves_prior_signal (budget cursor firstTotal : Nat)
    (firstNodes : List (Option Nat)) (next : CountedPage) (rest : List CountedPage)
    (previous : List Nat) (h : next.reportedTotal ≠ some firstTotal) :
    observe previous (collectCovered (budget + 2)
      (⟨⟨.next cursor, firstNodes⟩, some firstTotal⟩ :: next :: rest)) = (previous, false) := by
  cases firstCheck : validateNodes firstNodes <;>
    cases nextCheck : validateNodes next.page.nodes <;>
    simp [collectCovered, checkPageTotal, collect, observe, h, firstCheck, nextCheck]

theorem overlap_hides_missing_pr_counterexample :
    let previous := [1, 2, 3]
    let pages : List CountedPage :=
      [⟨⟨.next 1, [some 1, some 2]⟩, some 3⟩, ⟨⟨.terminal, [some 2]⟩, some 3⟩]
    (observe previous (some [1, 2])).2 = true ∧
    observe previous (collectCovered 5 pages) = (previous, false) := by decide

theorem complete_overlap_and_valid_empty_are_accepted :
    collectCovered 5
      [⟨⟨.next 1, [some 1, some 2]⟩, some 3⟩, ⟨⟨.terminal, [some 2, some 3]⟩, some 3⟩] =
      some [1, 2, 3] ∧
    collectCovered 5 [⟨⟨.terminal, []⟩, some 0⟩] = some [] := by decide

#print axioms complete_has_bounded_terminal
#print axioms incomplete_keeps_previous
#print axioms repeated_cursor_is_unavailable
#print axioms capped_prefix_counterexample
#print axioms terminal_on_last_allowed_page_is_complete
#print axioms validated_nodes_keep_every_positive_number
#print axioms invalid_page_discards_the_prefix
#print axioms null_node_on_terminal_page_counterexample
#print axioms genuine_empty_terminal_page_is_complete
#print axioms accepted_distinct_count_matches_report
#print axioms count_drift_preserves_prior_signal
#print axioms overlap_hides_missing_pr_counterexample
#print axioms complete_overlap_and_valid_empty_are_accepted

end StewardPrSignalPagination
