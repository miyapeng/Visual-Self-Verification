# Vision2Web repeated functional-check smoke test

Run time: 2026-08-25 14:14 UTC

This is a deterministic infrastructure test. It proves that the deployment,
browser, evidence, workspace-version, and replay interfaces compose correctly;
it is not evidence that a coding model chooses the loop autonomously.

## Executed episode

1. Launch a temporary application through `bash /workspace/start.sh` semantics.
2. Confirm `http://127.0.0.1:3000/` returns HTTP 200.
3. Call `browser_get_state` and return its screenshot inline.
4. Commit the expectation: "After clicking Run check, the visible result reads Fixed."
5. Execute one semantic click episode. The action succeeds, while the rendered
   result is `Broken`.
6. Simulate the coding policy changing `index.html` from `Broken` to `Fixed`.
7. Replay the same committed expectation and semantic action. The rendered
   result is now `Fixed`.

The first `executor_status=ok` means only that reset, grounding, and click
execution succeeded. The browser did not convert the visual mismatch into a
verdict and did not propose the edit.

## Returned evidence

- Initial state: two text content items plus one inline image.
- Each `browser_execute_plan` call: three text content items plus two inline
  images (reset state and post-action state).
- Each action observation records URL, viewport, compact interactive state,
  accessibility state, DOM change, console/runtime deltas, and paths to the
  full screenshot, DOM, accessibility, browser-state, and runtime artifacts.
- The same expectation is retained across replay.
- `P_first` and `P_final` have different program hashes, confirming the edit was
  captured between the two browser checks.

Canonical result:
`runs/vision2web_self_verify/repeated_function_check_v1/result.json`

Before-edit screenshot:
`runs/vision2web_self_verify/repeated_function_check_v1/trace/evidence/1787667263974668578-run-check-00-click-ok/screenshot.png`

After-edit screenshot:
`runs/vision2web_self_verify/repeated_function_check_v1/trace/evidence/1787667265092327660-run-check-00-click-ok/screenshot.png`

## Claude Code pilot snapshot

At 2026-08-25 14:17 UTC, the primary 20-case controller reported seven
completed, two running, one queuing, and ten not yet submitted. All seven
completed generation cases have successful code-generation results. A separate
three-case infrastructure-rerun controller reported one completed, one running,
and one pending.

Four completed same-context continuations exited successfully but made zero
`playwright-cli` calls and placed no browser screenshot in model context. This
is a policy-behavior result, not a browser-infrastructure failure. Those
continuations began with the earlier prompt revision; subsequent continuations
will use the prompt that explicitly states that curl/raw HTML inspection does
not count as a visual check.
