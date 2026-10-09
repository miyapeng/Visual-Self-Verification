# SmartRecruiters BDA and unresolved VC audit — 2026-10-05

This is an evidence audit, not a revised evaluation. Scores, model responses, and metric definitions remain unchanged. No new model calls were made. The audit inspects all 19 recorded BDA failures and relevant original responses, task requirements, reference images, and model inputs. It also identifies omissions and inconsistent positive labels; it is not an independently calibrated relabeling of every positive target.

## Inputs

- Scores: `runs/vsv_eval/complete_smartrecruiters_20261005/final/scores.json`
- Per-stage inputs and raw responses: `runs/vsv_eval/complete_smartrecruiters_20261005/checks_complete/`
- Frozen catalogue: `runs/vsv_eval/plan_grounded_gpt54_low_smartrecruiters_20261004/catalogue.json`
- Original timeline: `data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify/trajectory/run.json`
- Original task: the same fixture's `agent_visible/prompt.txt`.
- References: `data/vision2web/extracted/frontend/smartrecruiters/prototypes/{homepage,winston_ai}.jpg`.

## Arithmetic and interpretation

BDA = 100 × (58/75 + 8/10) / 2 = 78.6667%. There are 66 correct labels, 19 incorrect labels, and 7 unknowns. The 19 failures comprise 18 absent diagnoses and one recorded positive diagnosis of a supposedly failing state. This is balanced diagnosis accuracy under the current missing-diagnosis penalty, not the proportion of explicitly stated conclusions that are correct.

The protocol explicitly penalizes missing conclusions when sufficient evidence exists within the actual check scope. It also requires repairing missed diagnosis annotations before scoring. Neither rule justifies treating every visible item as an intended check or treating extraction omissions as agent failures.

## Confirmed problems and bounded findings

1. **Missing direct response (229–231).** Console feedback at #230 reports zero errors and warnings. #231 explicitly says “Zero console errors now.” The BDA packet for `verification-0229-0230-0707acc77c` has an empty `eligible_diagnosis_event_ids` list and labels the console target absent. #231 was assigned to the following visual round. This is an extraction/association error, not an incorrect console diagnosis. A response can evaluate the preceding check and introduce the next one; preserve its original ID and associate the relevant statement with both contexts without counting a second check.

2. **Example converted into a mandatory value (283–286).** `winston_metrics` is marked failing because the page shows “56% reduction in new hire turnover” rather than “33% reduction in time-to-fill.” The original task says metrics “like” the latter, the frozen criterion says “such as,” and the supplied Winston prototype itself displays the former. The recorded #284 image also displays the former. The cited numerical mismatch does not justify this failure under the plan's instruction that examples are not mandatory exact values. Reassess the target against the actual criterion rather than mechanically flipping the score; the scope of #286's overall-match statement also needs consistent treatment.

3. **Unsupported interaction state (231–237).** The sticky-navigation target explicitly includes remaining fixed during scrolling; the carousel target includes scrollability. Their CV labels both say method/evidence are false, yet BDA labels the actual states pass from static imagery. Static appearance alone cannot establish these interaction properties. The appropriate repair is to retain the actual attempted static condition or mark unsupported behavior unknown, not count an absent behavior diagnosis against the agent. The same scope ambiguity appears in the final homepage sticky-navigation target.

4. **Potential delayed-summary association omission (347–354).** The final route/file check has seven absent-diagnosis penalties. #354 explicitly summarizes route serving and deliverables, but lies outside the round ending at #348. Intervening actions clean Playwright artifacts and update a task status; they do not add new route-test evidence. This merits explicit semantic linkage to the earlier checks. It does not automatically prove all claims in the broad final summary or justify importing later observations into earlier judgments.

5. **Observed defect omitted from the initial target set (237).** #237 explicitly identifies the Gartner logo as a solid white block. The final initial-homepage target set includes the blank metrics and Winston section, but no Gartner target. The customer-logo-carousel target is a different object and cannot substitute for it. This omission means the denominator also needs review, not just the existing negative labels.

6. **Some absences remain plausible.** In the second homepage round, #254 and #259 discuss Gartner rather than explicitly judging every other visible section. The asset diagnostic at #264–268 has a pre-test intent but no explicit post-result conclusion before the edit. These must not be marked correct by inventing implicit diagnoses. Check-scope relevance should be checked first.

## Why VC contains one unresolved requirement

`homepage_hero_content` is explicitly frozen in `unresolved_check_ids`. The task specifies hero buttons “Get started” and “See how it works”; the prototype shows “Explore the Benefits” and “See how it works.” A separate “Get started” button appears in the navigation, not in the hero. The visible discrepancy supports the unresolved criterion, although the saved plan response does not provide an explicit explanation field.

Current prompts, validation, and aggregation force every matched unresolved goal to unknown. Consequently, this is not an unevaluated model request or a missing screenshot. The existing aggregate contains 20 full, 5 partial, 1 none, and 1 unknown out of 27: lower bound 20/27 = 74.0741%; upper bound 21/27 = 77.7778%. Partial remains uncredited under the existing user-selected policy.

There is a separate design issue: ambiguity about the correct button text need not make the fact of checking the hero unknown. The current whole-goal flag propagates ambiguity into VC, CV, and BDA, including an HTTP route check incorrectly mapped to the hero-content goal. A future correction should use the actual attempted condition for CV/BDA and distinguish observable check coverage from unresolved content correctness. Do not silently declare full hero coverage or change the denominator without re-evaluation.

## Minimal corrective direction

- Preserve semantic response links across adjacent rounds, and link delayed summaries only to supported earlier checks, retaining evidence timing.
- Keep target scope faithful to the operation and observed feedback; do not infer interaction outcomes from static views or discard explicit observed defects outside the fixed catalogue.
- Apply the plan's example-versus-requirement distinction consistently in BDA.
- Limit requirement ambiguity to the affected condition rather than automatically making unrelated route checks unknown.
- Re-run affected model stages and aggregation after these corrections. Do not manually patch the final score JSON. No corrected BDA point estimate is claimed in this audit.

## All recorded BDA failures

| Round | Target ID | Actual state | Agent state |
| --- | --- | --- | --- |
| `verification-0229-0230-0707acc77c` | `f0b500fadd3231ca` | pass | absent |
| `verification-0231-0237-3e18a64d00` | `homepage_sticky_nav` | pass | absent |
| `verification-0231-0237-3e18a64d00` | `homepage_logos_carousel` | pass | absent |
| `verification-0231-0237-3e18a64d00` | `homepage_case_studies` | pass | absent |
| `verification-0247-0259-fb3b042120` | `homepage_sticky_nav` | pass | absent |
| `verification-0247-0259-fb3b042120` | `homepage_logos_carousel` | pass | absent |
| `verification-0247-0259-fb3b042120` | `homepage_metrics` | pass | absent |
| `verification-0247-0259-fb3b042120` | `homepage_case_studies` | pass | absent |
| `verification-0247-0259-fb3b042120` | `9076a0e2f862c1c7` | pass | absent |
| `verification-0264-0268-40e02a7bc1` | `a08f326f1ba4aeae` | fail | absent |
| `verification-0283-0286-2f3e250cc0` | `nav_winston_ai` | pass | absent |
| `verification-0283-0286-2f3e250cc0` | `winston_metrics` | fail | pass |
| `verification-0347-0348-8e6b9b5391` | `nav_pricing` | pass | absent |
| `verification-0347-0348-8e6b9b5391` | `nav_about_us` | pass | absent |
| `verification-0347-0348-8e6b9b5391` | `nav_winston_ai` | pass | absent |
| `verification-0347-0348-8e6b9b5391` | `nav_customers` | pass | absent |
| `verification-0347-0348-8e6b9b5391` | `nav_careers` | pass | absent |
| `verification-0347-0348-8e6b9b5391` | `259fd9252ad50702` | pass | absent |
| `verification-0347-0348-8e6b9b5391` | `480175f811f2dff9` | pass | absent |
