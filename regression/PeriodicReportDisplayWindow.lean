import Std

/-
Run with Lean 4.22.0 using
`lean -DwarningAsError=true regression/PeriodicReportDisplayWindow.lean`.

UTC bounds and timezone offsets are integer microseconds. The model uses
separate endpoint offsets because a timezone can change offset in a window.
In `pending_intent.py`, `ZoneInfo` provides these offsets and `astimezone`
checks the calendar range. Missing timezone data is the `none` case here.
The CLI regression exercises the real formatter. The model concerns valid
UTC bounds and does not model timezone lookup, parsing, or display strings.
-/

namespace PeriodicReportDisplayWindow

structure Window where
  start : Int
  stop : Int

structure Offsets where
  start : Int
  stop : Int

def fromUtc (utc : Window) (offsets : Offsets) : Window :=
  ⟨utc.start + offsets.start, utc.stop + offsets.stop⟩

def toUtc (display : Window) (offsets : Offsets) : Window :=
  ⟨display.start - offsets.start, display.stop - offsets.stop⟩

def representable (minimum maximum : Int) (window : Window) : Prop :=
  minimum ≤ window.start ∧ window.start ≤ maximum ∧
    minimum ≤ window.stop ∧ window.stop ≤ maximum

instance (minimum maximum : Int) (window : Window) :
    Decidable (representable minimum maximum window) := by
  unfold representable
  infer_instance

def displayOffsets (minimum maximum : Int) (utc : Window) (preferred : Option Offsets) :
    Offsets :=
  match preferred with
  | none => ⟨0, 0⟩
  | some offsets =>
    if representable minimum maximum (fromUtc utc offsets) then offsets else ⟨0, 0⟩

theorem missing_timezone_uses_utc (minimum maximum : Int) (utc : Window) :
    displayOffsets minimum maximum utc none = ⟨0, 0⟩ := rfl

theorem unrepresentable_endpoint_uses_utc_for_both (minimum maximum : Int)
    (utc : Window) (preferred : Offsets)
    (outside : ¬ representable minimum maximum (fromUtc utc preferred)) :
    displayOffsets minimum maximum utc (some preferred) = ⟨0, 0⟩ := by
  simp [displayOffsets, outside]

theorem both_display_endpoints_remain_representable (minimum maximum : Int)
    (utc : Window) (preferred : Option Offsets) (valid : representable minimum maximum utc) :
    representable minimum maximum (fromUtc utc (displayOffsets minimum maximum utc preferred)) := by
  cases preferred with
  | none => simpa [displayOffsets, representable, fromUtc] using valid
  | some offsets =>
    by_cases fits : representable minimum maximum (fromUtc utc offsets)
    · rw [displayOffsets, if_pos fits]
      exact fits
    · rw [displayOffsets, if_neg fits]
      simpa [representable, fromUtc] using valid

theorem display_keeps_both_utc_instants (minimum maximum : Int) (utc : Window)
    (preferred : Option Offsets) :
    let selected := displayOffsets minimum maximum utc preferred
    toUtc (fromUtc utc selected) selected = utc := by
  cases utc
  simp [toUtc, fromUtc]

#print axioms missing_timezone_uses_utc
#print axioms unrepresentable_endpoint_uses_utc_for_both
#print axioms both_display_endpoints_remain_representable
#print axioms display_keeps_both_utc_instants

end PeriodicReportDisplayWindow
