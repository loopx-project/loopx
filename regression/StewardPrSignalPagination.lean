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

end StewardPrSignalPagination
