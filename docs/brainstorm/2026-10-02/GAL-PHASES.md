# GAL-PHASES adversarial review

Target: PR #11, head `7b0f9099202aa768bbabf69c569400ed8cfe5d8a`.
This reviews the proposed plan, not completed phase implementations.

Head continuity: later PR heads `3045bb62769e85919b765b6f6c3870464d861cc1`
and `e60cc60613ddf0f606938984312cbed29edb8515` add the GAL-TESTS merge
condition and GAL-REFERENCE phase. The original findings still apply. The
`validate` check passed for `e60cc606`, but the new reference phase has no
order step or GAL-TESTS section; see the GAL-REFERENCE review.

## Findings

- **P1 — Dependency order conflicts.** `GAL-PHASES.md:14,26` lists
  GAL-HOOKS after GAL-GATES only, while `GAL-HOOKS.md:3-4` also depends on
  GAL-VENDOR for its verify gate. Add that dependency. `GAL-PHASES.md:22`
  calls GAL-GATES and GAL-VENDOR independent, but GAL-VENDOR's CI step and
  GAL-GATES both change `validate.yml`; land the gate wrapper first, then wire
  vendor verification through it.
- **P1 — Independent review is not enforced.** The plan requires exact-head
  review (`:32-35`), but the live `main` protection read-back on 2026-10-02
  has no required approving reviews. Specify and verify a required review
  control or a required exact-head review status before claiming a merge gate.
- **P2 — “Only that phase's files” is too narrow.** `:30` conflicts with
  necessary shared workflow, registry and policy-test changes. Define the
  small shared-file allowance per phase so an implementation PR can satisfy
  its gate without broadening scope ad hoc.

## Simpler sequence

Docs PR; GAL-GATES; GAL-VENDOR; GAL-CATALOG; GAL-ROUTING after an approved
receipt executor is named; GAL-HOOKS after both GATES and VENDOR. Keep each
phase PR bounded and recheck the exact PR head after every push. The pinned CI
contract is already required by `standards-binding.yaml`; GAL-GATES must close
its evidence gap before phase completion.
