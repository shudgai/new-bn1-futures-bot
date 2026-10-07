# Hold local turns in an intact advancing trend — 2026-10-07

The user confirms implementing the CAP early-close finding. At 14:01:05 Taipei,
CAP long closed via `EXIT_EARLY_SWING_REVERSAL`; the saved snapshot still had
rising closed KC middle, rising quote-derived MA5, and intact long defense at
0.08581. This is an exit-rule issue, not a claim based on the later rebound.

Both new confirmed-pivot and legacy early-reversal exits now hold when the
synchronized latest two closed KC middle values advance in the held direction,
quote-derived MA5 advances against its previous closed value, and the matching
defensive structure is explicitly intact without a closed break. Only a relative
price 1e-12 tolerance is used; missing evidence does not invent an intact trend.

Hard risk, waterfall, and genuine structural breaks are independent. A new pivot
may still exit before middle crossing once this intact-trend conjunction fails.
No profit locks, entry changes or netting changes are introduced.

Pre-guard soft pending tickets are reevaluated when verified intact-trend evidence
is available. New valid soft triggers carry `pivot_guard_version=1` and retain
failed-close retry authority after subsequent rebound or restart. Audit includes
the guard decision and trigger version.

267 focused tests passed, including the actual CAP 14:01 snapshot holding,
both-side normal pullbacks, real weakening, old soft-ticket reevaluation, new
verified retries and hard/waterfall priority. The prior lobster 14:03 replay
remains an exit when live MA5 is 0.064358 against closed 0.06433: short MA5 has
reversed despite the still declining KC middle. No alternate historical fill or
exact historical quote claim is made. Compilation and diff checks passed.
