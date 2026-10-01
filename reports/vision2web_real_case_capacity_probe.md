# Vision2Web real-case self-verification capacity probe

## Conclusion

The framework can execute the intended mechanics on a real frozen Vision2Web
Level-2 output:

`deploy -> observe -> reset -> fill -> press Enter -> visual/runtime evidence -> edit -> reset -> replay`

This is an **interface-capacity result**, not an agent-autonomy result. No Qwen
or other coding model was called. The workflow selected one private atomic
check, GPT/manual analysis repaired the real implementation defect, and the
browser only executed and recorded actions.

## Case and information boundary

- Case: `frontend/academy_govloop` (Level 2).
- Frozen generated program: a historical official-mode Claude Code output.
- Private workflow objective: submit `leadership` in the top search bar and
  observe a search-results page with matching results.
- `workflow.json` was read only by the probe controller and was never inserted
  into a model context.
- The browser recorded expectation text but made no pass/fail decision.
- The post-hoc oracle read only the saved URL and DOM after execution.

## Real defect and repair

The first rendered version exposed the search input and accepted typing, but
pressing Enter caused no transition. Inspection found two concrete code faults:

1. a syntax error in `app.js` prevented the application script from executing;
2. after removing that blocker, the existing search helper and visible controls
   still had no event binding.

The repair fixed the malformed object literal, bound Enter/click to the existing
search control, rendered the matching videos, and updated the SPA route.

## Replayed evidence

| Evidence | Before repair | After repair |
|---|---:|---:|
| Semantic target grounded | `textbox / Search...` | `textbox / Search...` |
| Actions mechanically completed | 2/2 | 2/2 |
| Final URL | `/` | `/search/leadership` |
| Required heading in saved DOM | no | yes |
| DOM changed after Enter | no | yes |
| Runtime/console errors in captured delta | none | none |
| Screenshots returned inline by tool | 3 | 3 |
| Private post-hoc oracle | fail | pass |

Program hashes changed from
`c55c20e13362efcc4c9e822cad34f0978ccf81169d2286f391d51dd980e681af`
to
`31e15f7a8bdbc6ee99b5f1cbe6446bb1fc69c9c9d937bf6a05f870135aaab9f4`.

## Reproduction

The final successful job was CPU-only (`0 GPU`):

```bash
clusterx run --job-name mmc-v2w-real-academy-v4 \
  --num-nodes 1 --gpus-per-task 0 --cpus-per-task 8 \
  --memory-per-task 32 --shm-size-gib 8 --no-env \
  --image registry.pjlab.org.cn/ccr-t-llm-frontier/vision2web:official-577f939 \
  bash /data/miyapeng/mmcode/MultimodalCode/scripts/vision2web/run_real_case_capacity_probe.sh \
  academy_real_case_capacity_v4 capacity_probe_academy.json
```

Canonical result:
`runs/vision2web_self_verify/real_case_capacity/academy_real_case_capacity_v4/result.json`.

The interactive visual report is
`reports/vision2web_real_case_capacity_probe.html`.

## What this proves and does not prove

It proves that a coding policy can be given all required affordances: launch the
real app, receive a real screenshot, submit one coherent semantic interaction
episode, receive an image after every action, edit the workspace, and replay
the same check against the new version.

It does not prove that a particular model will voluntarily choose this sequence
or infer the same repair. Those are separate behavioral questions for the
`official`, `tools`, and `self_verify` conditions.
