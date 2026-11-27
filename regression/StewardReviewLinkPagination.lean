import Std

/-
Run with Lean 4.22.0: `lean -DwarningAsError=true regression/StewardReviewLinkPagination.lean`.

Each page represents one attempted REST request after response validation.
An unavailable or unverified response stops the scan and cannot prove
completeness. Wall time exhaustion removes unattempted pages from the input.
Python fixtures test the shared deadline and header parsing directly.
-/

namespace StewardReviewLinkPagination

inductive PageStatus where
  | next | terminal | unknown
  deriving DecidableEq

structure Page where
  evidence : List Nat
  status : PageStatus

structure Result where
  evidence : List Nat
  reads : Nat
  complete : Bool

def collect : Nat → List Page → Result
  | 0, _ | _, [] => ⟨[], 0, false⟩
  | budget + 1, page :: rest =>
    match page.status with
    | .terminal => ⟨page.evidence, 1, true⟩
    | .unknown => ⟨page.evidence, 1, false⟩
    | .next =>
      let later := collect budget rest
      ⟨page.evidence ++ later.evidence, later.reads + 1, later.complete⟩

theorem bounded_requests (budget : Nat) (pages : List Page) :
    (collect budget pages).reads ≤ budget := by
  induction budget generalizing pages with
  | zero => simp [collect]
  | succ budget ih =>
    cases pages with
    | nil => simp [collect]
    | cons page rest =>
      cases status : page.status with
      | terminal => simp [collect, status]
      | unknown => simp [collect, status]
      | next => simpa [collect, status] using Nat.succ_le_succ (ih rest)

theorem completion_requires_a_terminal_page (budget : Nat) (pages : List Page)
    (complete : (collect budget pages).complete = true) :
    ∃ page ∈ pages, page.status = .terminal := by
  induction budget generalizing pages with
  | zero => simp [collect] at complete
  | succ budget ih =>
    cases pages with
    | nil => simp [collect] at complete
    | cons page rest =>
      cases status : page.status with
      | terminal => exact ⟨page, by simp, status⟩
      | unknown => simp [collect, status] at complete
      | next =>
        have later : (collect budget rest).complete = true := by
          simpa [collect, status] using complete
        obtain ⟨entry, present, terminal⟩ := ih rest later
        exact ⟨entry, List.mem_cons_of_mem _ present, terminal⟩

theorem later_terminal_page_preserves_both_pages (first second : List Nat) :
    collect 2 [⟨first, .next⟩, ⟨second, .terminal⟩] = ⟨first ++ second, 2, true⟩ := rfl

theorem unavailable_second_page_preserves_first_page_without_fallback (first : List Nat) :
    (collect 2 [⟨first, .next⟩, ⟨[], .unknown⟩]).evidence = first ∧
    (collect 2 [⟨first, .next⟩, ⟨[], .unknown⟩]).complete = false := by
  simp [collect]

#print axioms bounded_requests
#print axioms completion_requires_a_terminal_page
#print axioms later_terminal_page_preserves_both_pages
#print axioms unavailable_second_page_preserves_first_page_without_fallback

end StewardReviewLinkPagination
