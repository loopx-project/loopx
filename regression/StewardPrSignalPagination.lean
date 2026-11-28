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

/- The collector requests a prefix of this plan. Its first count chooses the
   later page size, but the existing validation still decides when to stop and
   whether a snapshot can publish. The sample provider below is stable and
   obeys the requested page size. -/
def scanPageSizes (total : Nat) : List Nat :=
  if 120 < total then [20]
  else 20 :: List.replicate 4 (if 100 < total then 25 else 20)

theorem adaptive_plan_respects_page_and_row_limits (total : Nat) :
    (scanPageSizes total).length ≤ 5 ∧ (scanPageSizes total).sum ≤ 120 := by
  by_cases aboveCapacity : 120 < total
  · simp [scanPageSizes, aboveCapacity]
  · by_cases overflow : 100 < total <;> simp [scanPageSizes, aboveCapacity, overflow]

theorem ordinary_repository_keeps_standard_pages (total : Nat) (h : total ≤ 100) :
    scanPageSizes total = List.replicate 5 20 := by
  have fits : total ≤ 120 := by omega
  simp [scanPageSizes, Nat.not_lt.mpr h, Nat.not_lt.mpr fits, List.replicate_succ]

def stableProviderPages (total : Nat) : Nat → List Nat → List CountedPage
  | _, [] => []
  | start, size :: sizes =>
    let count := min size (total - start)
    let next := start + count
    let nodes := (List.range' (start + 1) count).map some
    if next < total then
      ⟨⟨.next next, nodes⟩, some total⟩ :: stableProviderPages total next sizes
    else
      [⟨⟨.terminal, nodes⟩, some total⟩]

theorem adaptive_pages_recover_111_prs :
    collectCovered 5 (stableProviderPages 111 0 (List.replicate 5 20)) = none ∧
    collectCovered 5 (stableProviderPages 111 0 (scanPageSizes 111)) =
      some (List.range' 1 111) := by decide

theorem out_of_capacity_keeps_prior_signal (previous : List Nat) :
    scanPageSizes 121 = [20] ∧
    observe previous (collectCovered 5 (stableProviderPages 121 0 (scanPageSizes 121))) =
      (previous, false) := by
  constructor <;> rfl

-- Each comment request consumes one slot even on failure.
def backfillSpent (remaining : Nat) : List Nat → Nat
  | [] => 0
  | demand :: rest =>
    let issued := min remaining (min demand 3)
    issued + backfillSpent (remaining - issued) rest

theorem comment_backfill_preserves_scan_limit (demands : List Nat) (remaining : Nat) :
    backfillSpent remaining demands ≤ remaining := by
  induction demands generalizing remaining with
  | nil => simp [backfillSpent]
  | cons demand rest ih =>
    let issued := min remaining (min demand 3)
    have issuedBound : issued ≤ remaining := Nat.min_le_left _ _
    have restBound := ih (remaining - issued)
    change issued + backfillSpent (remaining - issued) rest ≤ remaining
    omega

/- Use a common monotonic deadline for list reads and optional comment reads.
   Natural-number ticks model the order and timeout arithmetic. The model
   does not assume that a subprocess has returned merely because its timeout
   has elapsed. Eligibility checks the actual time of receipt separately. -/
def requestTimeout (perRequest deadline now : Nat) : Option Nat :=
  if now < deadline then some (min perRequest (deadline - now)) else none

def receivedInTime (deadline now : Nat) (response : α) : Option α :=
  if now ≤ deadline then some response else none

theorem request_fits_both_budgets {perRequest deadline now timeout : Nat}
    (h : requestTimeout perRequest deadline now = some timeout) :
    now < deadline ∧ timeout ≤ perRequest ∧ now + timeout ≤ deadline := by
  unfold requestTimeout at h
  split at h
  · rename_i before
    have chosen := Option.some.inj h
    subst timeout
    have remaining := Nat.min_le_right perRequest (deadline - now)
    exact ⟨before, Nat.min_le_left _ _, by omega⟩
  · simp at h

theorem exhausted_deadline_admits_no_request (perRequest deadline now : Nat)
    (h : deadline ≤ now) : requestTimeout perRequest deadline now = none := by
  simp [requestTimeout, Nat.not_lt.mpr h]

theorem late_list_reply_keeps_prior_result (deadline now : Nat) (previous rows : List Nat)
    (h : deadline < now) :
    observe previous (receivedInTime deadline now rows) = (previous, false) := by
  simp [receivedInTime, observe, Nat.not_le.mpr h]

def withCommentCoverage (rows : List Nat) (covered : Option Bool) : List Nat × Bool :=
  (rows, covered.getD false)

theorem late_comment_does_not_replace_list_or_complete_coverage (deadline now : Nat)
    (rows : List Nat) (h : deadline < now) :
    withCommentCoverage rows (receivedInTime deadline now true) = (rows, false) := by
  simp [withCommentCoverage, receivedInTime, Nat.not_le.mpr h]

theorem comment_deadline_counterexample :
    requestTimeout 20 100 95 = some 5 ∧
    receivedInTime (95 + 20) 101 true = some true ∧
    receivedInTime 100 101 true = none ∧
    receivedInTime 100 100 true = some true := by decide

/- Core supplies the matched-before-coverage predicate for each row.
   This abstracts its draft, conflict, verdict, reviewer, author and
   stalled-anchor checks. The list order models the collector's PR order. -/
structure CommentCandidate where
  number : Nat
  matchedBeforeCoverage : Bool
  incompleteComments : Bool
  deriving DecidableEq

def commentBackfillCandidates (rows : List CommentCandidate) : List CommentCandidate :=
  rows.filter fun row => row.matchedBeforeCoverage && row.incompleteComments

theorem backfill_targets_exactly_matching_incomplete_comments
    (rows : List CommentCandidate) (row : CommentCandidate) :
    row ∈ commentBackfillCandidates rows ↔
      row ∈ rows ∧ row.matchedBeforeCoverage = true ∧ row.incompleteComments = true := by
  simp [commentBackfillCandidates]

theorem unmatched_pr_cannot_consume_first_comment_request (first : CommentCandidate)
    (remaining : List CommentCandidate) (outOfScope : first.matchedBeforeCoverage = false) :
    commentBackfillCandidates (first :: remaining) = commentBackfillCandidates remaining := by
  simp [commentBackfillCandidates, outOfScope]

theorem only_remaining_request_reaches_matching_pr_counterexample :
    let rows : List CommentCandidate := [⟨500, false, true⟩, ⟨501, true, true⟩]
    (((rows.filter (·.incompleteComments)).head?).map CommentCandidate.number = some 500) ∧
    (((commentBackfillCandidates rows).head?).map CommentCandidate.number = some 501) := by decide

/- The existing stalled anchor is fixed by the observed PR and its reviews,
   before reading older comments. Ticks abstract the normalized UTC instants
   and positive age threshold. Planning uses the latest instant allowed by the
   remaining read window, while publication still uses the actual observation. -/
def needsAgeCommentCoverage (canBypass : Bool) (anchor : Option Nat)
    (minAge observedAt : Nat) : Bool :=
  canBypass || match anchor with
  | none => true
  | some basis => decide (basis + minAge ≤ observedAt)

theorem required_coverage_at_completion_is_in_the_plan
    {canBypass : Bool} {anchor : Option Nat} {minAge actual cutoff : Nat}
    (withinWindow : actual ≤ cutoff)
    (required : needsAgeCommentCoverage canBypass anchor minAge actual = true) :
    needsAgeCommentCoverage canBypass anchor minAge cutoff = true := by
  cases canBypass <;> cases anchor <;> simp [needsAgeCommentCoverage] at *
  exact Nat.le_trans required withinWindow

theorem skip_only_below_the_threshold
    (basis minAge observedAt : Nat) (tooYoung : observedAt < basis + minAge) :
    needsAgeCommentCoverage false (some basis) minAge observedAt = false := by
  simp [needsAgeCommentCoverage, Nat.not_le.mpr tooYoung]

theorem exact_threshold_keeps_coverage (basis minAge : Nat) :
    needsAgeCommentCoverage false (some basis) minAge (basis + minAge) = true := by
  simp [needsAgeCommentCoverage]

theorem unknown_anchor_or_possible_bypass_keeps_coverage (anchor : Option Nat) (minAge clock : Nat) :
    needsAgeCommentCoverage true anchor minAge clock = true ∧
    needsAgeCommentCoverage false none minAge clock = true := by
  simp [needsAgeCommentCoverage]

theorem using_plan_time_alone_loses_a_threshold_crossing :
    needsAgeCommentCoverage false (some 0) 86400 86395 = false ∧
    needsAgeCommentCoverage false (some 0) 86400 (86395 + 5) = true := by decide

/- A valid false pagination flag proves that there are no older comments.
   Missing and malformed flags both map to none. Independent coverage means
   the observed suffix reaches the stalled anchor or contains a reply whose
   role and timestamp independently establish the current state. Cursor
   identities abstract valid nonblank strings. -/
def commentPageCovered (hasPrevious : Option Bool) (independentCoverage : Bool) : Bool :=
  (hasPrevious == some false) || independentCoverage

def nextOlderCommentCursor (hasPrevious : Option Bool) (cursor : Option Nat) : Option Nat :=
  if hasPrevious == some true then cursor else none

theorem unknown_comment_flag_cannot_establish_coverage :
    commentPageCovered none false = false := rfl

theorem unknown_comment_flag_keeps_independent_coverage :
    commentPageCovered none true = true := rfl

theorem complete_comment_terminal_page_needs_no_cursor (cursor : Option Nat) :
    commentPageCovered (some false) false = true ∧
    nextOlderCommentCursor (some false) cursor = none := by
  simp [commentPageCovered, nextOlderCommentCursor]

theorem older_comment_request_needs_explicit_continuation
    (marker : Option Bool) (cursor : Option Nat) (admitted : Nat)
    (h : nextOlderCommentCursor marker cursor = some admitted) :
    marker = some true ∧ cursor = some admitted := by
  cases marker with
  | none => simp [nextOlderCommentCursor] at h
  | some marker =>
    cases marker with
    | false => simp [nextOlderCommentCursor] at h
    | true => exact ⟨rfl, by simpa [nextOlderCommentCursor] using h⟩

theorem missing_terminal_marker_counterexample :
    ((none : Option Bool) != some true) = true ∧
    commentPageCovered none false = false ∧ nextOlderCommentCursor none (some 7) = none := by decide

/- A missing comment connection or node list maps to none, while a genuine
   empty page maps to some []. A null or unreadable comment slot maps to none
   inside the list. The model accepts page metadata and coverage witnesses
   only after validating the entire batch. Position does not bound the date
   or author of an unreadable row. -/
def readCommentNodes : List (Option Nat) → Option (List Nat)
  | [] => some []
  | none :: _ => none
  | some clock :: rest => (readCommentNodes rest).map (clock :: ·)

def applyCommentPage (prior : List Nat × Bool) (batch : Option (List (Option Nat)))
    (terminal : Option Bool) (independentCoverage : Bool) : List Nat × Bool :=
  match batch.bind readCommentNodes with
  | none => prior
  | some rows => (rows ++ prior.1, commentPageCovered terminal independentCoverage)

theorem unreadable_node_blocks_coverage_from_the_whole_page (rows : List (Option Nat))
    (gap : none ∈ rows) : readCommentNodes rows = none := by
  induction rows with
  | nil => simp at gap
  | cons row rest ih =>
    cases row with
    | none => rfl
    | some clock =>
      have inRest : none ∈ rest := by simpa using gap
      simp [readCommentNodes, ih inRest]

theorem unreadable_comment_page_preserves_prior_evidence (prior : List Nat × Bool)
    (rows : List (Option Nat)) (terminal : Option Bool) (independentCoverage : Bool)
    (gap : none ∈ rows) :
    applyCommentPage prior (some rows) terminal independentCoverage = prior := by
  simp [applyCommentPage, unreadable_node_blocks_coverage_from_the_whole_page rows gap]

theorem missing_comment_connection_cannot_change_coverage (prior : List Nat × Bool)
    (terminal : Option Bool) (independentCoverage : Bool) :
    applyCommentPage prior none terminal independentCoverage = prior := rfl

theorem genuine_empty_comment_page_differs_from_unreadable_row :
    applyCommentPage ([], false) (some []) (some false) false = ([], true) ∧
    applyCommentPage ([], false) (some [none]) (some false) false = ([], false) := by decide

theorem readable_comment_reply_still_supplies_independent_coverage :
    applyCommentPage ([], false) (some [some 9]) none true = ([9], true) := rfl

/- Only post-anchor replies are considered and known bots have no role here.
   The implementation orders replies by time and then gives known authors
   precedence over maintainers. An unreadable identity could be an author,
   so it outranks a maintainer at the same time, but cannot change a verified
   author outcome at that time. -/
inductive ReplyRole where
  | maintainer
  | uncertain
  | author
  deriving DecidableEq

def replyRank : ReplyRole → Nat
  | .maintainer => 0
  | .uncertain => 1
  | .author => 2

def latestReply (left right : Nat × ReplyRole) : ReplyRole :=
  if left.1 < right.1 ∨ (left.1 = right.1 ∧ replyRank left.2 < replyRank right.2)
  then right.2
  else left.2

def replyCoverage (pageCovered : Bool) (latest : ReplyRole) : Bool :=
  pageCovered && decide (latest ≠ .uncertain)

theorem newer_unidentified_reply_keeps_the_prior_signal
    (previous current : List Nat) (verified unknown : Nat) (later : verified < unknown) :
    observe previous (if replyCoverage true (latestReply (verified, .author) (unknown, .uncertain))
      then some current else none) = (previous, false) := by
  simp [latestReply, replyCoverage, observe, later]

theorem older_unidentified_reply_cannot_change_a_verified_reply
    (unknown verified : Nat) (role : ReplyRole) (later : unknown < verified) :
    latestReply (unknown, .uncertain) (verified, role) = role := by
  simp [latestReply, later]

theorem verified_author_at_same_time_is_decisive (clock : Nat) :
    latestReply (clock, .uncertain) (clock, .author) = .author ∧
    latestReply (clock, .author) (clock, .uncertain) = .author := by
  simp [latestReply, replyRank]

theorem maintainer_at_same_time_cannot_resolve_unknown_actor (clock : Nat) :
    replyCoverage true (latestReply (clock, .maintainer) (clock, .uncertain)) = false := by
  simp [latestReply, replyRank, replyCoverage]

theorem treating_unknown_member_as_maintainer_changes_coverage :
    replyCoverage true (latestReply (9, .author) (10, .maintainer)) = true ∧
    replyCoverage true (latestReply (9, .author) (10, .uncertain)) = false := by decide

/- Identities abstract verified nonempty logins, compared without respect to
   case. The reply actor is known in this model. Its maintainer evidence
   cannot resolve whether it is also the unidentified PR creator. -/
def classifyRelativeToCreator (creator : Option Nat) (actor : Nat)
    (isBot hasMaintainerEvidence : Bool) : Option ReplyRole :=
  if isBot then none
  else match creator with
  | none => some .uncertain
  | some owner =>
    if actor = owner then some .author
    else if hasMaintainerEvidence then some .maintainer
    else none

theorem absent_creator_does_not_establish_reply_role (actor : Nat) (associated : Bool) :
    classifyRelativeToCreator none actor false associated = some .uncertain := rfl

theorem restoring_creator_identity_can_change_the_reply_role :
    classifyRelativeToCreator (some 1) 1 false true = some .author ∧
    classifyRelativeToCreator (some 2) 1 false true = some .maintainer := by decide

theorem absent_creator_keeps_the_verified_signal (previous rows : List Nat) (actor : Nat) :
    observe previous (if (classifyRelativeToCreator none actor false true).any (replyCoverage true)
      then some rows else none) = (previous, false) := rfl

theorem known_bot_is_ignored_without_creator_even_with_association (actor : Nat) :
    classifyRelativeToCreator none actor true true = none := rfl

theorem known_different_creator_preserves_irrelevant_reply (creator actor : Nat)
    (different : actor ≠ creator) :
    classifyRelativeToCreator (some creator) actor false false = none := by
  simp [classifyRelativeToCreator, different]

/- A known author contributes its membership in the configured set. A row
   already excluded by an independent filter cannot make the scope uncertain.
   Once the other gates pass, only an author-scoped row with an unreadable
   author returns no membership answer. -/
inductive AuthorScope where
  | unfiltered
  | include
  | exclude

def authorScopeMembership (mode : AuthorScope) (eligible : Bool)
    (authorInSet : Option Bool) : Option Bool :=
  if !eligible then some false
  else match mode with
  | .unfiltered => some true
  | .include => authorInSet
  | .exclude => authorInSet.map Bool.not

def observeAuthorScope (previous rows : List Nat) (membership : Option Bool) : List Nat × Bool :=
  observe previous (membership.map fun included => if included then rows else [])

theorem unknown_author_keeps_verified_scope (previous rows : List Nat) :
    observeAuthorScope previous rows (authorScopeMembership .include true none) = (previous, false) ∧
    observeAuthorScope previous rows (authorScopeMembership .exclude true none) = (previous, false) := by
  constructor <;> rfl

theorem known_author_membership_remains_dual (listed : Bool) :
    authorScopeMembership .include true (some listed) = some listed ∧
    authorScopeMembership .exclude true (some listed) = some (!listed) := by
  constructor <;> rfl

theorem independent_exclusion_needs_no_author_evidence (mode : AuthorScope) :
    authorScopeMembership mode false none = some false := rfl

theorem unfiltered_scope_needs_no_author_evidence :
    authorScopeMembership .unfiltered true none = some true := rfl

-- When an author filter cannot be resolved, backfill is still useful if it
-- could establish an independent age exclusion in a reply-bypass scope.
def planScopeCommentBackfill (knownMembership canBypassAge thresholdMet incomplete : Bool) : Bool :=
  incomplete && (knownMembership || (canBypassAge && !thresholdMet))

theorem unknown_membership_backfill_only_when_age_could_exclude
    (canBypassAge thresholdMet incomplete : Bool) :
    planScopeCommentBackfill false canBypassAge thresholdMet incomplete = true ↔
      incomplete = true ∧ canBypassAge = true ∧ thresholdMet = false := by
  cases canBypassAge <;> cases thresholdMet <;> cases incomplete <;> decide

/- The outer option is permission to use the repository component of the
   observation. Within a complete observation, none means that the monitor
   has no associated repository. Some [] means a verified empty PR list.
   This models canonical data before hashing, without assuming hash injectivity. -/
inductive RepositoryRead where
  | noRepository
  | verified (rows : List Nat)
  | invalidItems
  | unavailable

def repositoryHashInput : RepositoryRead → Option (Option (List Nat))
  | .noRepository => some none
  | .verified rows => some (some rows)
  | .invalidItems | .unavailable => none

def observeRepositoryRead (prior : Option (List Nat)) (read : RepositoryRead) :
    Option (List Nat) × Bool :=
  match repositoryHashInput read with
  | none => (prior, false)
  | some current => (current, decide (current ≠ prior))

theorem invalid_repository_items_keep_the_verified_result (prior : Option (List Nat)) :
    observeRepositoryRead prior .invalidItems = (prior, false) := rfl

theorem unavailable_repository_keeps_the_verified_result (prior : Option (List Nat)) :
    observeRepositoryRead prior .unavailable = (prior, false) := rfl

theorem genuinely_empty_repository_can_publish_a_removal (number : Nat) :
    observeRepositoryRead (some [number]) (.verified []) = (some [], true) := by
  simp [observeRepositoryRead, repositoryHashInput]

theorem no_repository_has_a_distinct_complete_input :
    repositoryHashInput .noRepository = some none ∧
    repositoryHashInput (.verified []) = some (some []) := by
  constructor <;> rfl

theorem old_empty_fallback_changes_the_result_of_invalid_items (number : Nat) :
    observeRepositoryRead (some [number]) (.verified []) ≠
      observeRepositoryRead (some [number]) .invalidItems := by
  simp [observeRepositoryRead, repositoryHashInput]

/- Decode rows at the Core boundary as optional natural numbers. Non-object
   rows and non-integer or negative numbers have no natural number encoding.
   The upper bound is JavaScript's largest safe integer. -/
def RepositoryRowsValid (seen : List Nat) : List (Option Nat) → Prop
  | [] => True
  | none :: _ => False
  | some number :: rest =>
      0 < number ∧ number ≤ 9007199254740991 ∧ number ∉ seen ∧
        RepositoryRowsValid (number :: seen) rest

def repositoryRowsValid (seen : List Nat) : List (Option Nat) → Bool
  | [] => true
  | none :: _ => false
  | some number :: rest =>
      decide (0 < number ∧ number ≤ 9007199254740991 ∧ number ∉ seen) &&
        repositoryRowsValid (number :: seen) rest

theorem repository_row_check_is_sound_and_complete (seen : List Nat) (rows : List (Option Nat)) :
    repositoryRowsValid seen rows = true ↔ RepositoryRowsValid seen rows := by
  induction rows generalizing seen with
  | nil => simp [repositoryRowsValid, RepositoryRowsValid]
  | cons row rest ih =>
    cases row <;> simp [repositoryRowsValid, RepositoryRowsValid, ih, and_assoc]

theorem valid_repository_rows_keep_every_number_in_order (seen : List Nat) (rows : List (Option Nat))
    (valid : RepositoryRowsValid seen rows) : (rows.filterMap id).map some = rows := by
  induction rows generalizing seen with
  | nil => rfl
  | cons row rest ih =>
    cases row with
    | none => simp [RepositoryRowsValid] at valid
    | some number =>
      simp [RepositoryRowsValid] at valid
      simp [ih (number :: seen) valid.2.2.2]

def observeRepositoryRows (prior : Option (List Nat)) (rows : List (Option Nat)) :
    Option (List Nat) × Bool :=
  if repositoryRowsValid [] rows then
    observeRepositoryRead prior (.verified (rows.filterMap id))
  else
    observeRepositoryRead prior .invalidItems

theorem invalid_repository_rows_preserve_the_verified_result (prior : Option (List Nat))
    (rows : List (Option Nat)) (invalid : ¬ RepositoryRowsValid [] rows) :
    observeRepositoryRows prior rows = (prior, false) := by
  have rejected : repositoryRowsValid [] rows ≠ true :=
    fun accepted => invalid ((repository_row_check_is_sound_and_complete [] rows).1 accepted)
  simp [observeRepositoryRows, rejected, invalid_repository_items_keep_the_verified_result]

theorem valid_repository_rows_publish_the_verified_input (prior : Option (List Nat))
    (rows : List (Option Nat)) (valid : RepositoryRowsValid [] rows) :
    observeRepositoryRows prior rows = observeRepositoryRead prior (.verified (rows.filterMap id)) := by
  simp [observeRepositoryRows, (repository_row_check_is_sound_and_complete [] rows).2 valid]

theorem verified_empty_rows_can_publish_a_removal (number : Nat) :
    observeRepositoryRows (some [number]) [] = (some [], true) := by
  simpa [observeRepositoryRows, repositoryRowsValid] using
    genuinely_empty_repository_can_publish_a_removal number

theorem dropping_an_unreadable_row_can_publish_a_false_removal :
    observeRepositoryRows (some [42]) [none] ≠ observeRepositoryRows (some [42]) [] := by
  decide

theorem duplicate_repository_numbers_preserve_the_verified_result (prior : Option (List Nat)) :
    observeRepositoryRows prior [some 42, some 42] = (prior, false) := by
  exact invalid_repository_rows_preserve_the_verified_result prior _ (by simp [RepositoryRowsValid])

/- Producer: timing evidence only includes exact-head Request Changes reviews.
   The outer option distinguishes unreadable timing from a confirmed absence
   of a current anchor. The existing dated-review logic provides the anchor
   when every relevant timestamp is readable. -/
def observedStalledAnchor (reviewTimes : List (Option Nat)) (verifiedAnchor : Option Nat) :
    Option (Option Nat) :=
  if none ∈ reviewTimes then none else some verifiedAnchor

theorem unordered_request_changes_time_remains_unknown
    (reviewTimes : List (Option Nat)) (verifiedAnchor : Option Nat) (missing : none ∈ reviewTimes) :
    observedStalledAnchor reviewTimes verifiedAnchor = none := by
  simp [observedStalledAnchor, missing]

theorem known_absence_and_unknown_request_changes_time_are_distinct :
    observedStalledAnchor [] none = some none ∧ observedStalledAnchor [none] none = none := by
  decide

/- Core: an absent clock means the threshold cannot be evaluated. A present
   Boolean reports the comparison with the configured minimum. An absent
   reply reports incomplete comment evidence. Independent exclusions include
   drafts, verified scope mismatch and a confirmed absent required anchor. -/
def decideStalledScope (excluded : Bool) (clock : Option Bool) (canBypass : Bool)
    (authorReply : Option Bool) : Option Bool :=
  if excluded || (clock == some false && !canBypass) then some false
  else if authorReply.isNone && (clock.isSome || canBypass) then none
  else if clock == some true || (canBypass && authorReply == some true) then some true
  else clock.map (fun _ => false)

theorem independently_excluded_rows_remain_excluded (clock : Option Bool) (canBypass : Bool)
    (authorReply : Option Bool) : decideStalledScope true clock canBypass authorReply = some false := by
  rfl

theorem unknown_age_is_included_iff_verified_author_reply_can_bypass
    (canBypass : Bool) (authorReply : Option Bool) :
    decideStalledScope false none canBypass authorReply = some true ↔
      canBypass = true ∧ authorReply = some true := by
  cases authorReply with
  | none => cases canBypass <;> decide
  | some reply => cases reply <;> cases canBypass <;> decide

theorem unknown_age_cannot_establish_a_verified_exclusion (canBypass : Bool)
    (authorReply : Option Bool) : decideStalledScope false none canBypass authorReply ≠ some false := by
  cases authorReply with
  | none => cases canBypass <;> decide
  | some reply => cases reply <;> cases canBypass <;> decide

def observeStalledScope (prior : List Nat) (number : Nat) (decision : Option Bool) :
    List Nat × Bool :=
  observe prior (decision.map (fun included => if included then [number] else []))

theorem unknown_age_preserves_prior_without_reply_bypass (prior : List Nat) (number : Nat)
    (authorReply : Option Bool) :
    observeStalledScope prior number (decideStalledScope false none false authorReply) = (prior, false) := by
  cases authorReply <;> rfl

theorem treating_unknown_age_as_recent_can_publish_a_false_removal :
    observeStalledScope [42] 42 (decideStalledScope false none false (some false)) ≠
      observeStalledScope [42] 42 (decideStalledScope false (some false) false (some false)) := by
  decide

def planStalledCommentRead (excluded : Bool) (clock : Option Bool) (canBypass authorKnown : Bool) :
    Bool :=
  !excluded && (clock != some false || canBypass) && (clock.isSome || canBypass) &&
    (authorKnown || (canBypass && clock == some false))

theorem unknown_age_comment_backfill_requires_available_reply_bypass_and_known_author
    (canBypass authorKnown : Bool) :
    planStalledCommentRead false none canBypass authorKnown = true ↔
      canBypass = true ∧ authorKnown = true := by
  cases canBypass <;> cases authorKnown <;> decide

/- After ordering the dated replies, an undated row can be irrelevant by time
   or can be the latest reply. Some true represents the author, some false
   represents a maintainer, and none could be either role. Known irrelevant
   rows are excluded before this check. The model uses the existing coverage
   tests for unread pages and uncertainty among the dated replies. -/
def decideUndatedReply (dated : Bool) : List (Option Bool) → Option Bool
  | [] => some dated
  | role :: rest => if role = some dated then decideUndatedReply dated rest else none

theorem unresolved_reply_times_agree_iff_matching_roles (dated : Bool) (undated : List (Option Bool)) :
    decideUndatedReply dated undated = some dated ↔ ∀ role ∈ undated, role = some dated := by
  induction undated with
  | nil => simp [decideUndatedReply]
  | cons role rest ih =>
    by_cases compatible : role = some dated <;> simp [decideUndatedReply, compatible, ih]

def PossibleAuthorReply (dated : Bool) (undated : List (Option Bool)) (result : Bool) : Prop :=
  result = dated ∨ some result ∈ undated ∨ none ∈ undated

theorem undated_reply_decision_is_sound_and_complete (dated : Bool) (undated : List (Option Bool)) :
    decideUndatedReply dated undated = some dated ↔
      ∀ result, PossibleAuthorReply dated undated result → result = dated := by
  rw [unresolved_reply_times_agree_iff_matching_roles]
  constructor
  · intro agreement result possible
    rcases possible with equal | present | unknown
    · exact equal
    · exact Option.some.inj (agreement (some result) present)
    · exact False.elim (by simpa using agreement none unknown)
  · intro agreement role present
    cases role with
    | none =>
      have contradiction := agreement (!dated) (Or.inr (Or.inr present))
      cases dated <;> simp at contradiction
    | some role => exact congrArg some (agreement role (Or.inr (Or.inl present)))

theorem same_role_undated_reply_preserves_the_known_decision (dated : Bool) :
    decideUndatedReply dated [some dated] = some dated := by
  simp [decideUndatedReply]

theorem undated_unknown_actor_cannot_qualify_reply (dated : Bool) :
    decideUndatedReply dated [none] = none := rfl

theorem unreadable_maintainer_time_preserves_prior (prior : List Nat) (number : Nat) :
    observeStalledScope prior number (decideUndatedReply true [some false]) = (prior, false) := rfl

theorem skipping_an_undated_maintainer_can_publish_a_false_bypass :
    observeStalledScope [] 42 (decideUndatedReply true [some false]) ≠
      observeStalledScope [] 42 (decideUndatedReply true []) := by
  decide

/- Review identifiers stand in for readable rows tied to known commits.
   None represents a row whose identity or commit is unavailable. Use the
   provider's terminal flag and reported count to qualify the whole window. -/
def reviewWindowComplete (headKnown : Bool) (hasPrevious : Option Bool) (total : Option Nat)
    (reviews : List (Option Nat)) : Bool :=
  headKnown && hasPrevious == some false && total == some reviews.length &&
    reviews.all Option.isSome && decide reviews.Nodup

theorem review_window_check_is_sound_and_complete (headKnown : Bool) (hasPrevious : Option Bool)
    (total : Option Nat) (reviews : List (Option Nat)) :
    reviewWindowComplete headKnown hasPrevious total reviews = true ↔
      headKnown = true ∧ hasPrevious = some false ∧ total = some reviews.length ∧
        (∀ review ∈ reviews, review.isSome = true) ∧ reviews.Nodup := by
  simp [reviewWindowComplete, List.all_eq_true, and_assoc]

theorem truncated_and_verified_empty_review_windows_are_distinct :
    reviewWindowComplete true (some true) (some 1) [] = false ∧
      reviewWindowComplete true (some false) (some 0) [] = true := by
  decide

theorem a_null_review_cannot_be_covered_by_a_matching_count :
    reviewWindowComplete true (some false) (some 2) [some 7, none] = false := by
  decide

theorem duplicate_reviews_cannot_supply_distinct_coverage :
    reviewWindowComplete true (some false) (some 2) [some 7, some 7] = false := by
  decide

def scopeWithReviewCoverage (independentExclusion reviewsRequired coverageComplete included : Bool) :
    Option Bool :=
  if independentExclusion then some false
  else if reviewsRequired && !coverageComplete then none
  else some included

theorem review_scope_can_publish_iff_coverage_is_sufficient
    (independentExclusion reviewsRequired coverageComplete included : Bool) :
    (scopeWithReviewCoverage independentExclusion reviewsRequired coverageComplete included).isSome =
      (independentExclusion || !reviewsRequired || coverageComplete) := by
  cases independentExclusion <;> cases reviewsRequired <;> cases coverageComplete <;> rfl

theorem incomplete_required_reviews_keep_the_prior_result (prior : List Nat) (number : Nat)
    (included : Bool) :
    observeStalledScope prior number (scopeWithReviewCoverage false true false included) =
      (prior, false) := rfl

theorem independent_exclusion_survives_unreadable_reviews (included : Bool) :
    scopeWithReviewCoverage true true false included = some false := rfl

theorem interpreting_an_unreadable_window_as_absence_can_publish_a_false_removal :
    observeStalledScope [42] 42 (scopeWithReviewCoverage false true false false) ≠
      observeStalledScope [42] 42 (scopeWithReviewCoverage false true true false) := by
  decide

/- An older response is admissible only for the requested PR and head, with
   the same total count and a timely response. Wrong PR identity and malformed
   cursor/row shape map to invalid pages. IDs stand for readable review nodes
   tied to known commits, and the first page was already validated. -/
structure OlderReviewPage where
  connection : CountedPage
  head : Option Nat
  timely : Bool

def bindOlderReviewPage (head total : Nat) (response : OlderReviewPage) : Page :=
  if response.head = some head ∧ response.timely = true then
    checkPageTotal total response.connection
  else { response.connection.page with metadata := .invalid }

def recoverOlderReviews (budget head total firstCursor : Nat) (first : List Nat)
    (pages : List OlderReviewPage) : Option (List Nat) := do
  let older ← collect budget [firstCursor] (pages.map (bindOlderReviewPage head total))
  let combined := older ++ first
  if combined.Nodup ∧ combined.length = total then some combined else none

theorem recovered_older_reviews_have_exact_distinct_coverage
    (budget head total cursor : Nat) (first : List Nat) (pages : List OlderReviewPage)
    (recovered : List Nat) (h : recoverOlderReviews budget head total cursor first pages = some recovered) :
    recovered.Nodup ∧ recovered.length = total := by
  unfold recoverOlderReviews at h
  cases collected : collect budget [cursor] (pages.map (bindOlderReviewPage head total)) with
  | none => simp [collected] at h
  | some older =>
    have verified : ((older ++ first).Nodup ∧ (older ++ first).length = total) ∧
        older ++ first = recovered := by simpa [collected] using h
    simpa [verified.2] using verified.1

theorem older_review_recovery_requires_terminal_within_budget
    (budget head total cursor : Nat) (first : List Nat) (pages : List OlderReviewPage)
    (recovered : List Nat) (h : recoverOlderReviews budget head total cursor first pages = some recovered) :
    ∃ i, i < budget ∧
      ((pages.map (bindOlderReviewPage head total))[i]?).map Page.metadata = some .terminal := by
  unfold recoverOlderReviews at h
  cases collected : collect budget [cursor] (pages.map (bindOlderReviewPage head total)) with
  | none => simp [collected] at h
  | some older => exact complete_has_bounded_terminal collected

theorem head_or_count_drift_or_late_review_preserves_prior
    (budget head total cursor : Nat) (first prior : List Nat) (page : OlderReviewPage)
    (rest : List OlderReviewPage)
    (bad : page.head ≠ some head ∨ page.timely = false ∨ page.connection.reportedTotal ≠ some total) :
    observe prior (recoverOlderReviews (budget + 1) head total cursor first (page :: rest)) =
      (prior, false) := by
  rcases bad with wrongHead | late | wrongCount <;>
    cases valid : validateNodes page.connection.page.nodes <;>
    simp_all [recoverOlderReviews, collect, bindOlderReviewPage, checkPageTotal, observe]

theorem cross_page_duplicate_review_cannot_replace_prior (prior : List Nat) :
    observe prior (recoverOlderReviews 1 9 2 1 [1] [⟨⟨⟨.terminal, [some 1]⟩, some 2⟩, some 9, true⟩]) =
      (prior, false) := by
  rfl

theorem repeated_older_review_cursor_cannot_replace_prior (prior : List Nat) :
    observe prior (recoverOlderReviews 2 9 3 1 [1]
      [⟨⟨⟨.next 1, [some 2]⟩, some 3⟩, some 9, true⟩,
       ⟨⟨⟨.terminal, [some 3]⟩, some 3⟩, some 9, true⟩]) = (prior, false) := by
  rfl

theorem one_verified_older_page_can_recover_review_twenty_one :
    let first := (List.range 20).map Nat.succ
    recoverOlderReviews 1 9 21 1 first [⟨⟨⟨.terminal, [some 21]⟩, some 21⟩, some 9, true⟩] =
      some (21 :: first) := by
  decide

theorem review_and_comment_reads_share_both_budgets
    (scan usedReviews comments : Nat) (withinPerPr : usedReviews ≤ 3) (withinScan : usedReviews ≤ scan)
    (allowed : comments ≤ min (scan - usedReviews) (3 - usedReviews)) :
    usedReviews + comments ≤ scan ∧ usedReviews + comments ≤ 3 := by
  have remainingScan := Nat.le_trans allowed (Nat.min_le_left (scan - usedReviews) (3 - usedReviews))
  have remainingPr := Nat.le_trans allowed (Nat.min_le_right (scan - usedReviews) (3 - usedReviews))
  omega

/- Before interpreting an older comment page, the shared reader decodes the
   response's PR number and head. An absent or invalid field is none. The
   reply decision below assumes the existing comment coverage checks passed. -/
def bindOlderCommentEvidence (number head : Nat) (returnedNumber returnedHead : Option Nat)
    (authorReply : Option Bool) : Option Bool :=
  if returnedNumber = some number ∧ returnedHead = some head then authorReply else none

theorem accepted_older_comments_have_exact_pr_and_head
    (number head : Nat) (returnedNumber returnedHead : Option Nat) (reply : Bool)
    (accepted : bindOlderCommentEvidence number head returnedNumber returnedHead (some reply) = some reply) :
    returnedNumber = some number ∧ returnedHead = some head := by
  by_cases bound : returnedNumber = some number ∧ returnedHead = some head
  · exact bound
  · simp [bindOlderCommentEvidence, bound] at accepted

theorem unbound_older_comments_preserve_prior
    (prior : List Nat) (number head : Nat) (returnedNumber returnedHead : Option Nat)
    (reply : Option Bool) (unbound : ¬ (returnedNumber = some number ∧ returnedHead = some head)) :
    observeStalledScope prior number (bindOlderCommentEvidence number head returnedNumber returnedHead reply) =
      (prior, false) := by
  simp [bindOlderCommentEvidence, unbound, observeStalledScope, observe]

theorem verified_matching_older_comments_preserve_their_decision
    (number head : Nat) (reply : Option Bool) :
    bindOlderCommentEvidence number head (some number) (some head) reply = reply := by
  simp [bindOlderCommentEvidence]

theorem matching_maintainer_reply_keeps_a_verified_exclusion :
    observeStalledScope [] 42 (bindOlderCommentEvidence 42 10 (some 42) (some 10) (some false)) =
      ([], false) := by
  decide

theorem ignoring_comment_head_can_publish_a_false_author_bypass :
    observeStalledScope [] 42 (some true) = ([42], true) ∧
      observeStalledScope [] 42 (bindOlderCommentEvidence 42 10 (some 42) (some 11) (some true)) =
        ([], false) := by
  decide

/- Each structurally usable page describes continuation and the reply result
   supported by the observed prefix. A dated reply or reaching the stalled
   anchor can supply coverage independently of the continuation. -/
structure OlderCommentEvidence where
  metadata : PageMetadata
  independentlyComplete : Bool
  authorReply : Bool

def collectOlderComments : Nat → List Nat → Nat → List OlderCommentEvidence → Option Bool
  | 0, _, _, _ => none
  | _ + 1, _, _, [] => none
  | budget + 1, seen, cursor, page :: rest =>
    if cursor ∈ seen then none
    else if page.independentlyComplete || page.metadata == .terminal then some page.authorReply
    else
      match page.metadata with
      | .next next => collectOlderComments budget (cursor :: seen) next rest
      | _ => none

theorem a_visited_comment_cursor_cannot_contribute_payload
    (budget cursor : Nat) (seen : List Nat) (page : OlderCommentEvidence)
    (rest : List OlderCommentEvidence) (visited : cursor ∈ seen) :
    collectOlderComments (budget + 1) seen cursor (page :: rest) = none := by
  simp [collectOlderComments, visited]

theorem looping_comment_backfill_preserves_prior (prior : List Nat) :
    observeStalledScope prior 42 (collectOlderComments 3 [] 1
      [⟨.next 2, false, false⟩, ⟨.next 1, false, false⟩, ⟨.terminal, false, true⟩]) =
      (prior, false) := rfl

theorem independent_reply_on_fresh_page_survives_a_cyclic_continuation :
    collectOlderComments 3 [] 1
      [⟨.next 2, false, false⟩, ⟨.next 1, true, true⟩, ⟨.terminal, false, false⟩] =
      some true := rfl

theorem distinct_comments_can_reach_verified_end_at_budget :
    let pages : List OlderCommentEvidence :=
      [⟨.next 2, false, false⟩, ⟨.next 3, false, false⟩, ⟨.terminal, false, false⟩]
    collectOlderComments 3 [] 1 pages = some false ∧
      collectOlderComments 2 [] 1 pages = none := by
  decide

/- These tokens stand for a readable updatedAt and a present reviewDecision.
   The optional decision models the explicit null GraphQL value. A missing
   or unreadable field makes the entire observed revision none. The list
   describes the older pages consumed by the bounded collector. -/
structure ReviewRevision where
  updatedAt : Nat
  reviewDecision : Option Nat
  deriving DecidableEq

def useReviewsAtRevision (initial : ReviewRevision) (observed : List (Option ReviewRevision))
    (coveredRows : Option (List Nat)) : Option (List Nat) :=
  if observed.all (fun revision => revision == some initial) then coveredRows else none

theorem accepted_reviews_require_observable_revision_agreement
    (initial : ReviewRevision) (observed : List (Option ReviewRevision))
    (coveredRows : Option (List Nat)) (acceptedRows : List Nat)
    (accepted : useReviewsAtRevision initial observed coveredRows = some acceptedRows) :
    ∀ revision ∈ observed, revision = some initial := by
  by_cases stable : observed.all (fun revision => revision == some initial) = true
  · simpa [List.all_eq_true] using stable
  · simp [useReviewsAtRevision, stable] at accepted

theorem any_older_review_revision_mismatch_preserves_prior
    (initial : ReviewRevision) (observed : List (Option ReviewRevision)) (revision : Option ReviewRevision)
    (coveredRows : Option (List Nat)) (prior : List Nat)
    (seen : revision ∈ observed) (drift : revision ≠ some initial) :
    observe prior (useReviewsAtRevision initial observed coveredRows) = (prior, false) := by
  cases result : useReviewsAtRevision initial observed coveredRows with
  | none => rfl
  | some rows =>
    exact False.elim (drift
      (accepted_reviews_require_observable_revision_agreement initial observed coveredRows rows result revision seen))

theorem changed_review_decision_with_equal_timestamp_cannot_publish :
    observe [] (useReviewsAtRevision ⟨10, some 1⟩ [some ⟨10, some 2⟩] (some [42])) = ([], false) := by
  decide

theorem changed_review_timestamp_with_equal_decision_cannot_remove :
    observe [42] (useReviewsAtRevision ⟨10, some 1⟩ [some ⟨11, some 1⟩] (some [])) = ([42], false) := by
  decide

theorem consistent_nullable_decisions_preserve_recovered_coverage (rows : List Nat) :
    useReviewsAtRevision ⟨10, none⟩ [some ⟨10, none⟩, some ⟨10, none⟩] (some rows) = some rows := by
  rfl

theorem missing_last_review_revision_cannot_be_treated_as_a_null_decision (prior : List Nat) :
    observe prior (useReviewsAtRevision ⟨10, none⟩ [some ⟨10, none⟩, none] (some [42])) =
      (prior, false) := by
  rfl

/- Each pair represents a review ID and the value of its selected fields.
   Readbacks carry the ordered first window, total count and previous cursor.
   The list describes the older pages consumed by the bounded collector. -/
structure FirstReviewWindow where
  rows : List (Nat × Nat)
  totalCount : Nat
  previousCursor : Nat
  deriving DecidableEq

def useReviewsAtFirstWindow (initial : FirstReviewWindow) (readbacks : List (Option FirstReviewWindow))
    (coveredRows : Option (List Nat)) : Option (List Nat) :=
  if readbacks.all (fun window => window == some initial) then coveredRows else none

theorem accepted_older_reviews_require_an_unchanged_first_window
    (initial : FirstReviewWindow) (readbacks : List (Option FirstReviewWindow))
    (coveredRows : Option (List Nat)) (acceptedRows : List Nat)
    (accepted : useReviewsAtFirstWindow initial readbacks coveredRows = some acceptedRows) :
    ∀ window ∈ readbacks, window = some initial := by
  by_cases stable : readbacks.all (fun window => window == some initial) = true
  · simpa [List.all_eq_true] using stable
  · simp [useReviewsAtFirstWindow, stable] at accepted

theorem same_review_id_with_changed_fields_preserves_prior :
    observe [42] (useReviewsAtFirstWindow ⟨[(7, 1)], 21, 10⟩
      [some ⟨[(7, 2)], 21, 10⟩] (some [])) = ([42], false) := by
  decide

theorem missing_last_first_window_readback_preserves_prior (prior : List Nat) :
    observe prior (useReviewsAtFirstWindow ⟨[(7, 1)], 21, 10⟩
      [some ⟨[(7, 1)], 21, 10⟩, none] (some [42])) = (prior, false) := rfl

theorem verified_first_windows_allow_terminal_recovery (rows : List Nat) :
    useReviewsAtFirstWindow ⟨[(7, 1)], 21, 10⟩
      [some ⟨[(7, 1)], 21, 10⟩, some ⟨[(7, 1)], 21, 10⟩] (some rows) = some rows := rfl

theorem changed_first_window_order_or_boundary_cannot_complete :
    let initial : FirstReviewWindow := ⟨[(7, 1), (8, 1)], 21, 10⟩
    useReviewsAtFirstWindow initial [some ⟨[(8, 1), (7, 1)], 21, 10⟩] (some [42]) = none ∧
      useReviewsAtFirstWindow initial [some ⟨[(7, 1), (8, 1)], 21, 11⟩] (some [42]) = none := by
  decide

/- Older readbacks are keyed by the cursor supplied to each GraphQL alias.
   The window representation includes the ordered review values and boundary.
   None represents a missing or malformed aliased page. -/
def useRevalidatedOlderReviews (consumed : List (Nat × FirstReviewWindow))
    (readbacks : List (Nat × Option FirstReviewWindow)) (coveredRows : Option (List Nat)) :
    Option (List Nat) :=
  if readbacks = consumed.map (fun entry => (entry.1, some entry.2)) then coveredRows else none

theorem accepted_recovery_requires_every_consumed_review_page_to_agree
    (consumed : List (Nat × FirstReviewWindow)) (readbacks : List (Nat × Option FirstReviewWindow))
    (coveredRows : Option (List Nat)) (acceptedRows : List Nat)
    (accepted : useRevalidatedOlderReviews consumed readbacks coveredRows = some acceptedRows) :
    readbacks = consumed.map (fun entry => (entry.1, some entry.2)) := by
  by_cases agree : readbacks = consumed.map (fun entry => (entry.1, some entry.2))
  · exact agree
  · simp [useRevalidatedOlderReviews, agree] at accepted

theorem changed_consumed_review_pages_preserve_prior
    (consumed : List (Nat × FirstReviewWindow)) (readbacks : List (Nat × Option FirstReviewWindow))
    (coveredRows : Option (List Nat)) (prior : List Nat)
    (changed : readbacks ≠ consumed.map (fun entry => (entry.1, some entry.2))) :
    observe prior (useRevalidatedOlderReviews consumed readbacks coveredRows) = (prior, false) := by
  simp [useRevalidatedOlderReviews, changed, observe]

theorem matching_consumed_review_pages_preserve_verified_coverage
    (consumed : List (Nat × FirstReviewWindow)) (coveredRows : Option (List Nat)) :
    useRevalidatedOlderReviews consumed
      (consumed.map (fun entry => (entry.1, some entry.2))) coveredRows = coveredRows := by
  simp [useRevalidatedOlderReviews]

theorem changed_first_consumed_review_is_detected_after_a_matching_second_readback :
    let consumed : List (Nat × FirstReviewWindow) :=
      [(10, ⟨[(7, 1)], 61, 11⟩), (11, ⟨[(8, 1)], 61, 12⟩)]
    let readbacks : List (Nat × Option FirstReviewWindow) :=
      [(10, some ⟨[(7, 2)], 61, 11⟩), (11, some ⟨[(8, 1)], 61, 12⟩)]
    observe [42] (useRevalidatedOlderReviews consumed readbacks (some [])) = ([42], false) := by
  decide

theorem missing_consumed_review_readback_cannot_complete :
    useRevalidatedOlderReviews [(10, ⟨[(7, 1)], 41, 11⟩)] [(10, none)] (some [42]) = none := by
  decide

theorem older_review_request_bounds_rows_including_readbacks
    (consumed firstRows olderRows readbackRows : Nat)
    (earlier : consumed ≤ 2) (firstCap : firstRows ≤ 20) (olderCap : olderRows ≤ 20)
    (readbackCap : readbackRows ≤ 20 * consumed) :
    firstRows + olderRows + readbackRows ≤ 80 := by
  omega

/- Readable check contexts are represented by an optional list of evidence
   tokens. A matching commit's explicit null rollup or empty contexts decode
   to some []. Missing evidence decodes to none. Direct adapter checks are
   independent of the nested GraphQL commit. -/
def selectHeadChecks (head : Option Nat) (direct : Option (List Nat))
    (commit : Option Nat) (nested : Option (List Nat)) : Option (List Nat) :=
  match direct with
  | some checks => some checks
  | none => if head.isSome && head == commit then nested else none

def observeHeadChecks (prior : List Nat) (independentExclusion : Bool) (checks : Option (List Nat)) :
    List Nat × Bool :=
  observe prior (if independentExclusion then some [] else checks)

theorem accepted_nested_checks_require_matching_head
    (head : Nat) (commit : Option Nat) (nested : Option (List Nat)) (checks : List Nat)
    (accepted : selectHeadChecks (some head) none commit nested = some checks) :
    commit = some head := by
  by_cases matchesHead : commit = some head
  · exact matchesHead
  · have wrongHead : some head ≠ commit := fun same => matchesHead same.symm
    simp [selectHeadChecks, wrongHead] at accepted

theorem foreign_or_unbound_checks_preserve_prior
    (prior nested : List Nat) (head : Nat) (commit : Option Nat) (wrongHead : commit ≠ some head) :
    observeHeadChecks prior false (selectHeadChecks (some head) none commit (some nested)) =
      (prior, false) := by
  have mismatch : some head ≠ commit := fun same => wrongHead same.symm
  simp [observeHeadChecks, selectHeadChecks, mismatch, observe]

theorem verified_empty_head_checks_and_missing_checks_have_distinct_results :
    observeHeadChecks [1] false (selectHeadChecks (some 7) none (some 7) (some [])) = ([], true) ∧
      observeHeadChecks [1] false (selectHeadChecks (some 7) none (some 7) none) = ([1], false) := by
  decide

theorem direct_pr_checks_remain_authoritative (head commit : Option Nat) (direct : List Nat)
    (nested : Option (List Nat)) :
    selectHeadChecks head (some direct) commit nested = some direct := rfl

theorem independent_exclusion_survives_unavailable_checks (prior : List Nat) :
    observeHeadChecks prior true none = observe prior (some []) := rfl

theorem genuine_current_head_check_failure_can_publish :
    observeHeadChecks [1] false (selectHeadChecks (some 7) none (some 7) (some [2])) =
      ([2], true) := by
  decide

/- The commit identity has already been verified. HasNext is the provider's
   terminal evidence for this forward connection. Optional naturals stand
   for readable check IDs, with none representing a malformed row. -/
def checkContextWindowComplete (hasNext : Option Bool) (total : Option Nat)
    (rows : List (Option Nat)) : Bool :=
  decide (rows.length ≤ 30) && reviewWindowComplete true hasNext total rows

def useCheckContextWindow (hasNext : Option Bool) (total : Option Nat)
    (rows : List (Option Nat)) : Option (List Nat) :=
  if checkContextWindowComplete hasNext total rows then some (rows.filterMap id) else none

theorem check_context_window_check_is_sound_and_complete (hasNext : Option Bool)
    (total : Option Nat) (rows : List (Option Nat)) :
    checkContextWindowComplete hasNext total rows = true ↔
      rows.length ≤ 30 ∧ hasNext = some false ∧ total = some rows.length ∧
        (∀ row ∈ rows, row.isSome = true) ∧ rows.Nodup := by
  simp [checkContextWindowComplete, review_window_check_is_sound_and_complete]

theorem incomplete_check_context_window_preserves_prior
    (prior : List Nat) (hasNext : Option Bool) (total : Option Nat) (rows : List (Option Nat))
    (incomplete : checkContextWindowComplete hasNext total rows = false) :
    observe prior (useCheckContextWindow hasNext total rows) = (prior, false) := by
  simp [useCheckContextWindow, incomplete, observe]

theorem terminal_check_window_at_cap_differs_from_truncated_prefix :
    let rows := (List.range 30).map some
    useCheckContextWindow (some false) (some 30) rows = some (List.range 30) ∧
      observe [42] (useCheckContextWindow (some true) (some 31) rows) = ([42], false) := by
  decide

theorem duplicate_check_ids_do_not_establish_complete_coverage :
    useCheckContextWindow (some false) (some 2) [some 7, some 7] = none := by
  decide

theorem absent_terminal_marker_and_verified_empty_checks_are_distinct :
    useCheckContextWindow none (some 0) [] = none ∧
      useCheckContextWindow (some false) (some 0) [] = some [] := by
  decide

theorem matching_reported_count_cannot_override_a_check_continuation :
    useCheckContextWindow (some true) (some 1) [some 7] = none := by
  decide

theorem over_cap_checks_cannot_be_verified_as_a_requested_window :
    useCheckContextWindow (some false) (some 31) ((List.range 31).map some) = none := by
  decide

/- The provider decodes each readable check as passed, failed or pending.
   STALE, completed checks without a terminal verdict and unsupported
   statuses decode to none. A tally requires a classification for every row. -/
inductive CheckOutcome where
  | passed
  | failed
  | pending

structure CheckTally where
  passed : Nat
  failed : Nat
  pending : Nat
  deriving DecidableEq

def addCheckOutcome (tally : CheckTally) : CheckOutcome → CheckTally
  | .passed => { tally with passed := tally.passed + 1 }
  | .failed => { tally with failed := tally.failed + 1 }
  | .pending => { tally with pending := tally.pending + 1 }

def summarizeCheckOutcomes : List (Option CheckOutcome) → Option CheckTally
  | [] => some ⟨0, 0, 0⟩
  | none :: _ => none
  | some outcome :: rest => (summarizeCheckOutcomes rest).map (fun tally => addCheckOutcome tally outcome)

theorem published_check_tally_counts_every_observed_check
    (outcomes : List (Option CheckOutcome)) (tally : CheckTally)
    (accepted : summarizeCheckOutcomes outcomes = some tally) :
    tally.passed + tally.failed + tally.pending = outcomes.length := by
  induction outcomes generalizing tally with
  | nil =>
    have same : (⟨0, 0, 0⟩ : CheckTally) = tally := Option.some.inj accepted
    subst tally
    rfl
  | cons current rest ih =>
    cases current with
    | none => simp [summarizeCheckOutcomes] at accepted
    | some outcome =>
      cases summarized : summarizeCheckOutcomes rest with
      | none => simp [summarizeCheckOutcomes, summarized] at accepted
      | some previous =>
        have priorTotal := ih previous summarized
        have same : addCheckOutcome previous outcome = tally := by
          simpa [summarizeCheckOutcomes, summarized] using accepted
        subst tally
        cases outcome <;> simp [addCheckOutcome] <;> omega

theorem an_unclassified_check_blocks_a_complete_tally
    (outcomes : List (Option CheckOutcome)) (unknown : none ∈ outcomes) :
    summarizeCheckOutcomes outcomes = none := by
  induction outcomes with
  | nil => simp at unknown
  | cons current rest ih =>
    cases current with
    | none => rfl
    | some outcome =>
      have inRest : none ∈ rest := by simpa using unknown
      simp [summarizeCheckOutcomes, ih inRest]

def observeCheckTally (prior : List Nat) (outcomes : List (Option CheckOutcome)) : List Nat × Bool :=
  observe prior ((summarizeCheckOutcomes outcomes).map (fun tally => [tally.passed, tally.failed, tally.pending]))

theorem unresolved_check_conclusion_preserves_prior_failure :
    observeCheckTally [0, 1, 0] [none] = ([0, 1, 0], false) := rfl

theorem known_failure_with_an_unclassified_check_preserves_prior :
    observeCheckTally [0, 1, 0] [some .failed, none] = ([0, 1, 0], false) := rfl

theorem known_check_outcomes_keep_their_counts :
    summarizeCheckOutcomes [some .passed, some .failed, some .pending] = some ⟨1, 1, 1⟩ := rfl

/- The native producer applies this selection to pushed and committed clocks
   separately. The pair holds a trusted timestamp and an unbound candidate.
   Naturals encode readable UTC instants in order. For direct evidence, the
   outer option records presence even if its timestamp is unreadable. -/
def selectHeadClock (head commit : Option Nat) (direct : Option (Option Nat))
    (nested : Option Nat) : Option Nat × Option Nat :=
  match direct with
  | some clock => (clock, none)
  | none =>
    if head.isSome && head == commit then (nested, none)
    else if commit.isNone then (none, nested) else (none, none)

def qualifyHeadClock (review : Nat) (pushed committed : Option Nat × Option Nat) : Option Nat :=
  let trusted := max review (max (pushed.1.getD 0) (committed.1.getD 0))
  if trusted < max (pushed.2.getD 0) (committed.2.getD 0) then none else some trusted

theorem selected_nested_clock_requires_matching_head (head commit : Option Nat) (clock : Nat) :
    (selectHeadClock head commit none (some clock)).1 = some clock ↔
      head.isSome = true ∧ head = commit := by
  cases head <;> cases commit <;> simp [selectHeadClock]
  split <;> simp_all

theorem direct_head_clock_takes_precedence (head commit nested clock : Option Nat) :
    selectHeadClock head commit (some clock) nested = (clock, none) := rfl

theorem verified_head_clock_can_reset_stall (head review pushed : Nat) (newer : review ≤ pushed) :
    qualifyHeadClock review (selectHeadClock (some head) (some head) none (some pushed)) (none, none) =
      some pushed := by
  simp [selectHeadClock, qualifyHeadClock, Nat.max_eq_right newer]

theorem foreign_head_clock_cannot_reset_stall (head commit review pushed : Nat) (wrongHead : head ≠ commit) :
    qualifyHeadClock review (selectHeadClock (some head) (some commit) none (some pushed)) (none, none) =
      some review := by
  simp [selectHeadClock, qualifyHeadClock, wrongHead]

theorem unavailable_head_clock_is_material_iff_it_can_advance_the_anchor
    (review : Nat) (pushed committed : Option Nat × Option Nat) :
    let trusted := max review (max (pushed.1.getD 0) (committed.1.getD 0))
    qualifyHeadClock review pushed committed = none ↔
      trusted < pushed.2.getD 0 ∨ trusted < committed.2.getD 0 := by
  dsimp [qualifyHeadClock]
  split <;> simp_all <;> omega

theorem a_known_stall_anchor_is_the_maximum_of_trusted_clocks
    (review clock : Nat) (pushed committed : Option Nat × Option Nat)
    (known : qualifyHeadClock review pushed committed = some clock) :
    clock = max review (max (pushed.1.getD 0) (committed.1.getD 0)) := by
  dsimp [qualifyHeadClock] at known
  split at known
  · contradiction
  · exact (Option.some.inj known).symm

/- The producer does not infer an author reply relative to an uncertain anchor.
   Core retains prior scope evidence unless an independent filter excludes it. -/
def observeHeadClockScope (prior : List Nat) (number now minimum : Nat) (excluded canBypass : Bool)
    (anchor : Option Nat) (authorReply : Option Bool) : List Nat × Bool :=
  observeStalledScope prior number <|
    decideStalledScope excluded (anchor.map (fun atTime => decide (atTime + minimum ≤ now)))
      canBypass (if anchor.isSome then authorReply else some false)

theorem unresolved_head_clock_preserves_any_prior_scope
    (prior : List Nat) (number now minimum : Nat) (canBypass : Bool) (authorReply : Option Bool) :
    observeHeadClockScope prior number now minimum false canBypass none authorReply = (prior, false) := by
  cases canBypass <;> rfl

theorem independent_exclusion_can_publish_despite_unresolved_head_clock
    (prior : List Nat) (number now minimum : Nat) (canBypass : Bool) (authorReply : Option Bool) :
    observeHeadClockScope prior number now minimum true canBypass none authorReply = ([], prior != []) := rfl

theorem accepting_or_dropping_an_unbound_clock_can_publish_unsupported_scope :
    let anchor := qualifyHeadClock 1 (selectHeadClock (some 7) none none (some 9)) (none, none)
    observeHeadClockScope [42] 42 10 5 false false anchor (some false) = ([42], false) ∧
    observeHeadClockScope [42] 42 10 5 false false (some 9) (some false) = ([], true) ∧
    observeHeadClockScope [] 42 10 5 false false anchor (some false) = ([], false) ∧
    observeHeadClockScope [] 42 10 5 false false (some 1) (some false) = ([42], true) := by
  decide

/- The shared reader has verified PR number and HEAD. Each token represents the
   raw fields relevant to a comment's time and actor. The ordered pages include
   their continuation boundaries. An absent readback represents unreadable data. -/
structure CommentWindow where
  rows : List Nat
  boundary : PageMetadata
  deriving DecidableEq

structure CommentReadback where
  revision : Option ReviewRevision
  latest : Option CommentWindow
  previous : List (Nat × Option CommentWindow)
  deriving DecidableEq

def expectedCommentReadback (revision : ReviewRevision) (latest : CommentWindow)
    (consumed : List (Nat × CommentWindow)) : CommentReadback :=
  ⟨some revision, some latest, consumed.map (fun entry => (entry.1, some entry.2))⟩

def useRevalidatedCommentPage (expected observed : CommentReadback) (decision : Option Bool) :
    Option Bool :=
  if observed = expected then decision else none

theorem older_comment_decision_checks_revision_and_all_windows
    (revision : ReviewRevision) (latest : CommentWindow) (consumed : List (Nat × CommentWindow))
    (observed : CommentReadback) (decision : Bool) :
    useRevalidatedCommentPage (expectedCommentReadback revision latest consumed) observed (some decision) =
      some decision ↔ observed.revision = some revision ∧ observed.latest = some latest ∧
        observed.previous = consumed.map (fun entry => (entry.1, some entry.2)) := by
  cases observed
  simp [useRevalidatedCommentPage, expectedCommentReadback]

theorem changed_comment_readback_keeps_the_previous_scope
    (prior : List Nat) (number : Nat) (expected observed : CommentReadback) (decision : Option Bool)
    (changed : observed ≠ expected) :
    observeStalledScope prior number (useRevalidatedCommentPage expected observed decision) =
      (prior, false) := by
  simp [useRevalidatedCommentPage, changed, observeStalledScope, observe]

theorem matching_comment_readbacks_allow_independent_or_terminal_coverage
    (expected : CommentReadback) (budget cursor : Nat) (pages : List OlderCommentEvidence) :
    useRevalidatedCommentPage expected expected (collectOlderComments budget [] cursor pages) =
      collectOlderComments budget [] cursor pages := by
  simp [useRevalidatedCommentPage]

theorem equal_comment_revision_can_hide_a_changed_latest_window :
    let expected := expectedCommentReadback ⟨10, some 1⟩ ⟨[1], .next 5⟩ []
    let current := expectedCommentReadback ⟨10, some 1⟩ ⟨[2], .next 5⟩ []
    observeStalledScope [42] 42 (useRevalidatedCommentPage expected current (some false)) =
      ([42], false) ∧ observeStalledScope [42] 42 (some false) = ([], true) := by
  decide

theorem missing_first_consumed_comment_page_is_detected_with_a_matching_second :
    let first : CommentWindow := ⟨[2], .next 6⟩
    let second : CommentWindow := ⟨[3], .next 7⟩
    let expected := expectedCommentReadback ⟨10, some 1⟩ ⟨[1], .next 5⟩ [(5, first), (6, second)]
    let current := {expected with previous := [(5, none), (6, some second)]}
    useRevalidatedCommentPage expected current (some true) = none := by
  decide

theorem older_comment_request_bounds_rows_including_readbacks
    (consumed firstRows olderRows readbackRows : Nat)
    (earlier : consumed ≤ 2) (firstCap : firstRows ≤ 20) (olderCap : olderRows ≤ 50)
    (readbackCap : readbackRows ≤ 50 * consumed) :
    firstRows + olderRows + readbackRows ≤ 170 := by
  omega

/- One row token carries its PR number, HEAD, update time and review decision.
   A window also carries the total and its verified continuation. Cursor keys
   represent the original query positions. This models observable agreement
   with the final response, conditional on consistent aliases in that response. -/
structure OpenPrWindowEvidence where
  rows : List (Nat × Nat × ReviewRevision)
  total : Nat
  continuation : Nat
  deriving DecidableEq

def useOpenPrReadbacks (earlier : List (Option Nat × OpenPrWindowEvidence))
    (readbacks : List (Option Nat × Option OpenPrWindowEvidence)) (covered : Option (List Nat)) :
    Option (List Nat) :=
  if readbacks = earlier.map (fun entry => (entry.1, some entry.2)) then covered else none

theorem published_open_pr_list_requires_all_consumed_windows
    (earlier : List (Option Nat × OpenPrWindowEvidence))
    (readbacks : List (Option Nat × Option OpenPrWindowEvidence)) (rows : List Nat) :
    useOpenPrReadbacks earlier readbacks (some rows) = some rows ↔
      readbacks = earlier.map (fun entry => (entry.1, some entry.2)) := by
  by_cases agreement : readbacks = earlier.map (fun entry => (entry.1, some entry.2)) <;>
    simp [useOpenPrReadbacks, agreement]

theorem changed_open_pr_window_keeps_the_previous_observation
    (earlier : List (Option Nat × OpenPrWindowEvidence))
    (readbacks : List (Option Nat × Option OpenPrWindowEvidence)) (prior : List Nat)
    (covered : Option (List Nat))
    (changed : readbacks ≠ earlier.map (fun entry => (entry.1, some entry.2))) :
    observe prior (useOpenPrReadbacks earlier readbacks covered) = (prior, false) := by
  simp [useOpenPrReadbacks, changed, observe]

theorem one_page_open_pr_list_needs_no_readbacks (rows : List Nat) :
    useOpenPrReadbacks [] [] (some rows) = some rows := by
  simp [useOpenPrReadbacks]

theorem complete_distinct_count_can_hide_an_open_pr_generation_change :
    let earlier : OpenPrWindowEvidence := ⟨[(1, 7, ⟨11, none⟩)], 2, 5⟩
    let current : OpenPrWindowEvidence := ⟨[(3, 8, ⟨12, none⟩)], 2, 5⟩
    observe [1, 2] (some [1, 4]) = ([1, 4], true) ∧
    observe [1, 2] (useOpenPrReadbacks [(none, earlier)] [(none, some current)] (some [1, 4])) =
      ([1, 2], false) := by
  decide

theorem matching_latest_readback_cannot_hide_a_changed_first_open_pr_window :
    let first : OpenPrWindowEvidence := ⟨[(1, 7, ⟨11, none⟩)], 3, 5⟩
    let middle : OpenPrWindowEvidence := ⟨[(2, 9, ⟨10, none⟩)], 3, 6⟩
    let changed := {first with rows := [(1, 8, ⟨11, none⟩)]}
    useOpenPrReadbacks [(none, first), (some 5, middle)] [(none, some changed), (some 5, some middle)]
      (some [1, 2, 3]) = none := by
  decide

theorem open_pr_request_bounds_rows_including_readbacks
    (priorOverflowPages firstRows previousRows currentRows : Nat)
    (earlier : priorOverflowPages ≤ 3) (firstCap : firstRows ≤ 20)
    (previousCap : previousRows ≤ 25 * priorOverflowPages) (currentCap : currentRows ≤ 25) :
    firstRows + previousRows ≤ 95 ∧ firstRows + previousRows + currentRows ≤ 120 := by
  omega

/- A carried nested tally already passed HEAD, coverage and classification checks.
   The terminal readback must establish the same evidence. Fresh terminal rows
   and trusted direct adapter results take their usual path without comparison. -/
def verifyCarriedCheckTally (original current : Option CheckTally) : Option CheckTally :=
  if original = current then original else none

theorem carried_check_tally_is_published_iff_both_reads_agree (original current : Option CheckTally)
    (counts : CheckTally) : verifyCarriedCheckTally original current = some counts ↔
      original = some counts ∧ current = some counts := by
  by_cases agrees : original = current
  · subst current
    simp [verifyCarriedCheckTally]
  · simp [verifyCarriedCheckTally, agrees]
    intro prior now
    exact agrees (prior.trans now.symm)

def observeCarriedCheckTally (prior : List Nat) (excluded : Bool)
    (original current : Option CheckTally) : List Nat × Bool :=
  observeHeadChecks prior excluded
    ((verifyCarriedCheckTally original current).map (fun tally => [tally.passed, tally.failed, tally.pending]))

theorem changed_or_unreadable_carried_checks_keep_prior
    (prior : List Nat) (original current : Option CheckTally) (differs : original ≠ current) :
    observeCarriedCheckTally prior false original current = (prior, false) := by
  simp [observeCarriedCheckTally, observeHeadChecks, verifyCarriedCheckTally, differs, observe]

theorem independently_excluded_carried_checks_allow_an_observation
    (prior : List Nat) (original current : Option CheckTally) :
    observeCarriedCheckTally prior true original current = ([], prior != []) := rfl

theorem same_check_total_can_hide_a_changed_carried_result :
    let prior : CheckTally := ⟨0, 1, 0⟩
    let current : CheckTally := ⟨1, 0, 0⟩
    prior.passed + prior.failed + prior.pending = current.passed + current.failed + current.pending ∧
    observeCarriedCheckTally [0, 1, 0] false (some prior) (some current) = ([0, 1, 0], false) := by
  decide

theorem a_page_without_check_readbacks_cannot_complete_when_below_capacity
    (seen pageSize fetched expected : Nat) (short : seen + pageSize < expected)
    (bounded : fetched ≤ pageSize) : seen + fetched < expected := by
  omega

theorem terminal_request_bounds_check_context_rows
    (previousPrs currentPrs previousChecks currentChecks : Nat) (previousCap : previousPrs ≤ 95)
    (currentCap : currentPrs ≤ 25) (previousWindow : previousChecks ≤ 30 * previousPrs)
    (currentWindow : currentChecks ≤ 30 * currentPrs) :
    previousChecks ≤ 2850 ∧ previousChecks + currentChecks ≤ 3600 := by
  omega

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
#print axioms adaptive_plan_respects_page_and_row_limits
#print axioms ordinary_repository_keeps_standard_pages
#print axioms adaptive_pages_recover_111_prs
#print axioms out_of_capacity_keeps_prior_signal
#print axioms comment_backfill_preserves_scan_limit
#print axioms request_fits_both_budgets
#print axioms exhausted_deadline_admits_no_request
#print axioms late_list_reply_keeps_prior_result
#print axioms late_comment_does_not_replace_list_or_complete_coverage
#print axioms comment_deadline_counterexample
#print axioms backfill_targets_exactly_matching_incomplete_comments
#print axioms unmatched_pr_cannot_consume_first_comment_request
#print axioms only_remaining_request_reaches_matching_pr_counterexample
#print axioms required_coverage_at_completion_is_in_the_plan
#print axioms skip_only_below_the_threshold
#print axioms exact_threshold_keeps_coverage
#print axioms unknown_anchor_or_possible_bypass_keeps_coverage
#print axioms using_plan_time_alone_loses_a_threshold_crossing
#print axioms unknown_comment_flag_cannot_establish_coverage
#print axioms unknown_comment_flag_keeps_independent_coverage
#print axioms complete_comment_terminal_page_needs_no_cursor
#print axioms older_comment_request_needs_explicit_continuation
#print axioms missing_terminal_marker_counterexample
#print axioms unreadable_node_blocks_coverage_from_the_whole_page
#print axioms unreadable_comment_page_preserves_prior_evidence
#print axioms missing_comment_connection_cannot_change_coverage
#print axioms genuine_empty_comment_page_differs_from_unreadable_row
#print axioms readable_comment_reply_still_supplies_independent_coverage
#print axioms newer_unidentified_reply_keeps_the_prior_signal
#print axioms older_unidentified_reply_cannot_change_a_verified_reply
#print axioms verified_author_at_same_time_is_decisive
#print axioms maintainer_at_same_time_cannot_resolve_unknown_actor
#print axioms treating_unknown_member_as_maintainer_changes_coverage
#print axioms absent_creator_does_not_establish_reply_role
#print axioms restoring_creator_identity_can_change_the_reply_role
#print axioms absent_creator_keeps_the_verified_signal
#print axioms known_bot_is_ignored_without_creator_even_with_association
#print axioms known_different_creator_preserves_irrelevant_reply
#print axioms unknown_author_keeps_verified_scope
#print axioms known_author_membership_remains_dual
#print axioms independent_exclusion_needs_no_author_evidence
#print axioms unfiltered_scope_needs_no_author_evidence
#print axioms unknown_membership_backfill_only_when_age_could_exclude
#print axioms invalid_repository_items_keep_the_verified_result
#print axioms unavailable_repository_keeps_the_verified_result
#print axioms genuinely_empty_repository_can_publish_a_removal
#print axioms no_repository_has_a_distinct_complete_input
#print axioms old_empty_fallback_changes_the_result_of_invalid_items
#print axioms repository_row_check_is_sound_and_complete
#print axioms valid_repository_rows_keep_every_number_in_order
#print axioms invalid_repository_rows_preserve_the_verified_result
#print axioms valid_repository_rows_publish_the_verified_input
#print axioms verified_empty_rows_can_publish_a_removal
#print axioms dropping_an_unreadable_row_can_publish_a_false_removal
#print axioms duplicate_repository_numbers_preserve_the_verified_result
#print axioms unordered_request_changes_time_remains_unknown
#print axioms known_absence_and_unknown_request_changes_time_are_distinct
#print axioms independently_excluded_rows_remain_excluded
#print axioms unknown_age_is_included_iff_verified_author_reply_can_bypass
#print axioms unknown_age_cannot_establish_a_verified_exclusion
#print axioms unknown_age_preserves_prior_without_reply_bypass
#print axioms treating_unknown_age_as_recent_can_publish_a_false_removal
#print axioms unknown_age_comment_backfill_requires_available_reply_bypass_and_known_author
#print axioms unresolved_reply_times_agree_iff_matching_roles
#print axioms undated_reply_decision_is_sound_and_complete
#print axioms same_role_undated_reply_preserves_the_known_decision
#print axioms undated_unknown_actor_cannot_qualify_reply
#print axioms unreadable_maintainer_time_preserves_prior
#print axioms skipping_an_undated_maintainer_can_publish_a_false_bypass
#print axioms review_window_check_is_sound_and_complete
#print axioms truncated_and_verified_empty_review_windows_are_distinct
#print axioms a_null_review_cannot_be_covered_by_a_matching_count
#print axioms duplicate_reviews_cannot_supply_distinct_coverage
#print axioms review_scope_can_publish_iff_coverage_is_sufficient
#print axioms incomplete_required_reviews_keep_the_prior_result
#print axioms independent_exclusion_survives_unreadable_reviews
#print axioms interpreting_an_unreadable_window_as_absence_can_publish_a_false_removal
#print axioms recovered_older_reviews_have_exact_distinct_coverage
#print axioms older_review_recovery_requires_terminal_within_budget
#print axioms head_or_count_drift_or_late_review_preserves_prior
#print axioms cross_page_duplicate_review_cannot_replace_prior
#print axioms repeated_older_review_cursor_cannot_replace_prior
#print axioms one_verified_older_page_can_recover_review_twenty_one
#print axioms review_and_comment_reads_share_both_budgets
#print axioms accepted_older_comments_have_exact_pr_and_head
#print axioms unbound_older_comments_preserve_prior
#print axioms verified_matching_older_comments_preserve_their_decision
#print axioms matching_maintainer_reply_keeps_a_verified_exclusion
#print axioms ignoring_comment_head_can_publish_a_false_author_bypass
#print axioms a_visited_comment_cursor_cannot_contribute_payload
#print axioms looping_comment_backfill_preserves_prior
#print axioms independent_reply_on_fresh_page_survives_a_cyclic_continuation
#print axioms distinct_comments_can_reach_verified_end_at_budget
#print axioms accepted_reviews_require_observable_revision_agreement
#print axioms any_older_review_revision_mismatch_preserves_prior
#print axioms changed_review_decision_with_equal_timestamp_cannot_publish
#print axioms changed_review_timestamp_with_equal_decision_cannot_remove
#print axioms consistent_nullable_decisions_preserve_recovered_coverage
#print axioms missing_last_review_revision_cannot_be_treated_as_a_null_decision
#print axioms accepted_older_reviews_require_an_unchanged_first_window
#print axioms same_review_id_with_changed_fields_preserves_prior
#print axioms missing_last_first_window_readback_preserves_prior
#print axioms verified_first_windows_allow_terminal_recovery
#print axioms changed_first_window_order_or_boundary_cannot_complete
#print axioms accepted_recovery_requires_every_consumed_review_page_to_agree
#print axioms changed_consumed_review_pages_preserve_prior
#print axioms matching_consumed_review_pages_preserve_verified_coverage
#print axioms changed_first_consumed_review_is_detected_after_a_matching_second_readback
#print axioms missing_consumed_review_readback_cannot_complete
#print axioms older_review_request_bounds_rows_including_readbacks
#print axioms accepted_nested_checks_require_matching_head
#print axioms foreign_or_unbound_checks_preserve_prior
#print axioms verified_empty_head_checks_and_missing_checks_have_distinct_results
#print axioms direct_pr_checks_remain_authoritative
#print axioms independent_exclusion_survives_unavailable_checks
#print axioms genuine_current_head_check_failure_can_publish
#print axioms check_context_window_check_is_sound_and_complete
#print axioms incomplete_check_context_window_preserves_prior
#print axioms terminal_check_window_at_cap_differs_from_truncated_prefix
#print axioms duplicate_check_ids_do_not_establish_complete_coverage
#print axioms absent_terminal_marker_and_verified_empty_checks_are_distinct
#print axioms matching_reported_count_cannot_override_a_check_continuation
#print axioms over_cap_checks_cannot_be_verified_as_a_requested_window
#print axioms published_check_tally_counts_every_observed_check
#print axioms an_unclassified_check_blocks_a_complete_tally
#print axioms unresolved_check_conclusion_preserves_prior_failure
#print axioms known_failure_with_an_unclassified_check_preserves_prior
#print axioms known_check_outcomes_keep_their_counts
#print axioms selected_nested_clock_requires_matching_head
#print axioms direct_head_clock_takes_precedence
#print axioms verified_head_clock_can_reset_stall
#print axioms foreign_head_clock_cannot_reset_stall
#print axioms unavailable_head_clock_is_material_iff_it_can_advance_the_anchor
#print axioms a_known_stall_anchor_is_the_maximum_of_trusted_clocks
#print axioms unresolved_head_clock_preserves_any_prior_scope
#print axioms independent_exclusion_can_publish_despite_unresolved_head_clock
#print axioms accepting_or_dropping_an_unbound_clock_can_publish_unsupported_scope
#print axioms older_comment_decision_checks_revision_and_all_windows
#print axioms changed_comment_readback_keeps_the_previous_scope
#print axioms matching_comment_readbacks_allow_independent_or_terminal_coverage
#print axioms equal_comment_revision_can_hide_a_changed_latest_window
#print axioms missing_first_consumed_comment_page_is_detected_with_a_matching_second
#print axioms older_comment_request_bounds_rows_including_readbacks
#print axioms published_open_pr_list_requires_all_consumed_windows
#print axioms changed_open_pr_window_keeps_the_previous_observation
#print axioms one_page_open_pr_list_needs_no_readbacks
#print axioms complete_distinct_count_can_hide_an_open_pr_generation_change
#print axioms matching_latest_readback_cannot_hide_a_changed_first_open_pr_window
#print axioms open_pr_request_bounds_rows_including_readbacks
#print axioms carried_check_tally_is_published_iff_both_reads_agree
#print axioms changed_or_unreadable_carried_checks_keep_prior
#print axioms independently_excluded_carried_checks_allow_an_observation
#print axioms same_check_total_can_hide_a_changed_carried_result
#print axioms a_page_without_check_readbacks_cannot_complete_when_below_capacity
#print axioms terminal_request_bounds_check_context_rows

end StewardPrSignalPagination
