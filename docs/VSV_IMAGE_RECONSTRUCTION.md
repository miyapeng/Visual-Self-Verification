# Reconstruct archived application images

`scripts/vision2web/reconstruct_leaderboard_images.py` replays recorded file edits
and shell/browser commands in a dedicated Docker container. It does not run an
agent or call a model. Original trajectory files and task materials remain intact.

From the repository root, scan first:

```bash
python scripts/vision2web/reconstruct_leaderboard_images.py \
  --model-root /mnt/shared-storage-user/miyapeng/datasets/vision2web-leaderboard/ClaudeCode+Claude-Opus-4.8 \
  --work-root runs/vsv_eval/image_reconstruction
```

Run a bounded selection:

```bash
python scripts/vision2web/reconstruct_leaderboard_images.py \
  --model-root /mnt/shared-storage-user/miyapeng/datasets/vision2web-leaderboard/ClaudeCode+Claude-Opus-4.8 \
  --work-root runs/vsv_eval/image_reconstruction \
  --tasks frontend/1daycloud frontend/blackbeltacandelectric webpage/alberta_alis \
  --execute --limit 3
```

Use `--execute --limit N` without `--tasks` to process the first N eligible cases.
Existing outputs are checked against trajectory and image hashes and skipped.
Partial outputs are also preserved; the script does not silently replace them.
An existing output with conflicting hashes requires review.

Requirements: Docker, the `vision2web-sandbox:latest` image (Node, npm,
Playwright CLI, browser, Python and Pillow), and original task materials under
`data/vision2web/extracted/<level>/<task>`. Override these with `--image` and
`--task-root`. Network access may be needed for dependencies and external assets.
The dedicated npm cache is configurable with `--npm-cache`. Command and task
budgets default to 300 and 900 seconds. Downloads use bounded retries and the
cache; the application manifest and recorded commands are unchanged.

## What is reconstructed

1. Import the native Claude conversation with existing event IDs and tool-result
   pairing. Find image receipts and their recorded application capture sources,
   including helper scripts written in the trajectory.
2. Plan Write, Edit and Bash actions through the last relevant image Read.
   Original failed file edits are not applied. Unsupported tools, ambiguous
   mutations and detected destructive commands block automatic execution.
3. Restore original task materials inside the container and execute actions in
   order. Keep shell working-directory changes and explicit working-directory
   resets recorded by Claude Code. Do not repair application bugs.
4. At each original image Read, copy the file produced by its recorded source.
   Reused filenames get distinct read-event filenames. A failed capture cannot
   silently reuse an older image. Historical screenshots retain their capture
   state even when the code has since changed.
5. Publish images and their provenance index. A source hash check protects against
   associating evidence with a changed export.

Task directories contain only:

```text
reconstructed/
  images/<read-event-id>-<filename>
  reconstructed_observations.json
```

The index links each image to `read_event_id`, `image_receipt_event_ids`,
`capture_event_id`, original path, dimensions, content hash and reconstructed
workspace hash. It also records the source trajectory hash, environment,
unresolved references and failures. Detailed command output, workspace manifests,
dependency locks and the normalized trajectory remain under `--work-root`.
The workspace hash is unavailable when files changed inside the capture's Bash
action; its after-action hash is retained separately. This prevents claiming an
exact intermediate code state that the replay did not observe.

## Interpretation and limits

`completed` means every planned application image receipt was reconstructed.
It does not mean the website passed its tests or that every image in the entire
conversation was restored. Reference images and asset inspection are outside
this replay target; unresolved image sources are listed explicitly. Only images
with a recorded input receipt are exported, rather than every incidental capture.

These are **reconstructed images**, not original pixels. Browser versions,
dependency resolution, fonts, external services, timing and animations may
differ. Original Read-tool image conversion is not reproduced. Runtime and
dependency locks document the new environment; pixel equivalence remains
`unverified`. Installed Chrome's version is inventory metadata; Python Playwright
or an explicit Chromium selection may use a different bundled browser.
The strict evaluator does not automatically consume these sidecars.
Use them as reconstructed evidence with this qualification.

## Explicit reconstructed-evidence evaluation

Pass `--reconstructed-images TASK/reconstructed/reconstructed_observations.json`
to `score_vsv.py` together with the existing `--rounds-json`, `--catalogue`,
`--task-root`, configuration and a separate output directory. The evaluator
validates the raw source hash, capture/read/receipt IDs and image hashes before
attaching pixels. Original timeline events and segmentation remain unchanged.
The image attachment and every judge packet identify reconstruction explicitly;
future evidence remains excluded by the original observation cutoff.

Results carry `evidence_mode=reconstructed_observations` and status
`reconstructed_pilot`. They must be reported separately from original-image and
text-proxy results. `--reuse-checks` is disabled for this mode; unchanged calls
can resume from the same output cache. Reconstructed labels cannot be imported
as strict original-evidence judgments. No scoring formula changes.

This supplies image evidence for VC, CV and BDA. It does not create the executable
before/after acceptance records required by RS and CP. A workspace hash is not
a runnable snapshot, and a recorded recheck is not independent regression
acceptance. Unassessed repair groups and resulting VCS uncertainty remain explicit.

The shell preflight is a conservative review gate, not a general security
sandbox. Run trusted archives only. The container mounts only the dedicated npm
cache, not source trajectories or task directories. Containers are stopped and
retained for diagnosis; the script does not delete old containers, outputs or
user data. Historical task paths inside a container can still be unavailable;
those failures are recorded rather than patched with task-specific rules.

## Repair checkpoints

Image reconstruction can also export code checkpoints required by RS/CP. Prepare
an `acceptance_binding` with `prepare_vsv_acceptance.py`, then pass both
`--rounds-json` and `--acceptance-binding` to the existing reconstruction command
for one selected task. The CLI preserves the extraction's source run and event IDs.
It replays through the latest required image read or repair boundary, whichever
comes last; repairs after the last image read are not omitted.

The worker exports original code bytes before/after the linked repairs and records
actual shell-driven code mutations. The host verifies every file hash before
publishing the existing version-manifest format. An incomplete run cannot publish
a complete manifest. Nested application assets are included; dependency trees are
not copied. Existing recovered images are not overwritten by checkpoint preparation.
The exported versions remain reconstructed artifacts, not certified historical
runtime replicas: dependency and browser equivalence still require experimental checks.
