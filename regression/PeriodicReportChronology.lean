import Std

/-
Run with Lean 4.22.0: lean -DwarningAsError=true regression/PeriodicReportChronology.lean

The model starts after rollout-event validation. `instant` is the number of
microseconds from the earliest representable UTC instant. `idRank` represents
the lexical event-ID order within the input. Python compares the corresponding
UTC `datetime` values and IDs in `runtime_producer.py`. Keeping the first
occurrence of each stage identity after sorting preserves the first stage.
The cutoff takes Todo completions after the producer's existing deduplication.
The CLI regression checks the timestamp parsing and the actual Python path.
-/

namespace PeriodicReportChronology

structure Event where
  instant : Nat
  idRank : Nat
  isStage : Bool

def beforeOrAt (a b : Event) : Bool :=
  decide (a.instant < b.instant ∨ (a.instant = b.instant ∧ a.idRank ≤ b.idRank))

def stageEvents (events : List Event) : List Event :=
  (events.mergeSort beforeOrAt).filter Event.isStage

def firstStage (events : List Event) : Option Event :=
  (stageEvents events).head?

def completionsAt (completions : List Event) (stage : Event) : List Event :=
  completions.filter (fun event => decide (event.instant ≤ stage.instant))

private theorem stageEvents_sorted (events : List Event) :
    (stageEvents events).Pairwise (fun a b => beforeOrAt a b = true) := by
  apply List.Pairwise.filter
  apply List.sorted_mergeSort
  · intro a b c hab hbc
    simp only [beforeOrAt, decide_eq_true_eq] at *
    omega
  · intro a b
    simp only [Bool.or_eq_true, beforeOrAt, decide_eq_true_eq]
    omega

private theorem mem_stageEvents {events : List Event} {event : Event} :
    event ∈ stageEvents events ↔ event ∈ events ∧ event.isStage = true := by
  simp [stageEvents]

theorem firstStage_none_iff (events : List Event) :
    firstStage events = none ↔ ∀ event ∈ events, event.isStage ≠ true := by
  simp only [firstStage, List.head?_eq_none_iff, List.eq_nil_iff_forall_not_mem,
    mem_stageEvents, not_and]

private theorem firstStage_minimal {events : List Event} {stage : Event}
    (selected : firstStage events = some stage) :
    stage ∈ events ∧ stage.isStage = true ∧
      ∀ other ∈ events, other.isStage = true → beforeOrAt stage other = true := by
  obtain ⟨rest, hrest⟩ := List.head?_eq_some_iff.mp selected
  have hmem : stage ∈ stageEvents events := by simp [hrest]
  have hsorted := stageEvents_sorted events
  rw [hrest, List.pairwise_cons] at hsorted
  refine ⟨(mem_stageEvents.mp hmem).1, (mem_stageEvents.mp hmem).2, ?_⟩
  intro other hother hstage
  have h : other ∈ stageEvents events := mem_stageEvents.mpr ⟨hother, hstage⟩
  rw [hrest, List.mem_cons] at h
  rcases h with rfl | h
  · simp [beforeOrAt]
  · exact hsorted.1 other h

theorem firstStage_some_iff (events : List Event) (stage : Event) :
    firstStage events = some stage ↔ stage ∈ events ∧ stage.isStage = true ∧
      ∀ other ∈ events, other.isStage = true → beforeOrAt stage other = true := by
  constructor
  · exact firstStage_minimal
  · rintro ⟨hmem, hstage, hleast⟩
    cases hselected : firstStage events with
    | none => exact False.elim (firstStage_none_iff events |>.mp hselected stage hmem hstage)
    | some selected =>
      obtain ⟨hm, hs, hmin⟩ := firstStage_minimal hselected
      have hs₁ := hleast selected hm hs
      have hs₂ := hmin stage hmem hstage
      simp only [beforeOrAt, decide_eq_true_eq] at hs₁ hs₂
      have htime : selected.instant = stage.instant := by omega
      have hid : selected.idRank = stage.idRank := by omega
      cases selected
      cases stage
      simp_all

theorem completion_included_iff (completions : List Event) (stage event : Event) :
    event ∈ completionsAt completions stage ↔
      event ∈ completions ∧ event.instant ≤ stage.instant := by
  simp [completionsAt]

-- ISO output omits the fraction at a whole second. The old order is reversed.
theorem old_order_counterexample :
    "2026-08-21T09:00:00.000001Z" < "2026-08-21T09:00:00Z" ∧
      beforeOrAt ⟨1, 0, true⟩ ⟨0, 1, true⟩ = false := by decide

#print axioms firstStage_none_iff
#print axioms firstStage_some_iff
#print axioms completion_included_iff
#print axioms old_order_counterexample

end PeriodicReportChronology
