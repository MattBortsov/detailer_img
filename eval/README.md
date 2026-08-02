# CarWrap Bot feasibility gate

This directory contains the tracked evaluation contract. Authorized source
images, generated results, run artifacts, score workfiles, and reports are
operator-owned evidence and are intentionally ignored by Git.

## 1. Prepare and lock the corpus

Copy `corpus.example.yaml` to `corpus.yaml`, place only authorized vehicle
images beneath `eval/fixtures/`, and replace every example checksum with the
lowercase SHA-256 of the referenced file. Keep the case IDs and coverage
metadata stable once scoring begins. The locked corpus must cover cars and
motorcycles, front/rear/side/three-quarter viewpoints, light and dark source
colors, reflections, complex backgrounds, and partial occlusions.

Validate checksums, path containment, symlink policy, D-03 coverage, and a full
in-memory Pillow decode without credentials or network access. Preflight
accepts only single-frame PNG, JPEG, and WebP fixtures within the configured
dimension and pixel limits:

```bash
env -u OPENROUTER_API_KEY .venv/bin/python -m car_wrap.eval validate \
  --manifest eval/corpus.yaml \
  --fixture-root eval/fixtures
```

Exercise the complete preflight without reading a credential, creating an HTTP
client, writing images, or creating a run artifact:

```bash
env -u OPENROUTER_API_KEY .venv/bin/python -m car_wrap.eval generate \
  --manifest eval/corpus.yaml \
  --fixture-root eval/fixtures \
  --dry-run
```

## 2. Run the paid candidate deliberately

Set `OPENROUTER_API_KEY` in the invoking shell and optionally set
`OPENROUTER_IMAGE_MODEL` (default: `x-ai/grok-imagine-image-quality`). Generated
media may be written only to a real, non-symlink descendant of the project
`eval/output/` directory. The versioned run artifact contains metadata only.

```bash
.venv/bin/python -m car_wrap.eval generate \
  --manifest eval/corpus.yaml \
  --fixture-root eval/fixtures \
  --output-dir eval/output/phase-01 \
  --run eval/runs/phase-01.json
```

Cases run in stable case-ID order. Each explicit invocation makes at most one
provider attempt for each case without a recorded success. There is no generic
retry. A failed attempt is recorded with an allowlisted error code; after
investigation, deliberately rerun the same command to append the next numbered
attempt. On resume, every recorded success must still exist in the same output
directory with its exact byte count and SHA-256 digest. Never hand-edit
successful attempts or copy results between cases.

The run artifact records the source checksum, model, prompt revision, attempt
number, start/finish times, latency, output byte count, allowlisted usage/cost,
optional peak RSS, and safe outcome. It never stores image bytes, base64, data
URLs, provider bodies, headers, signed URLs, credentials, or raw errors.
Successful attempts also record the SHA-256 of the exact generated output.

## 3. Score all eight dimensions

Review every authorized source/result pair side by side. Create
`eval/scores.yaml` as a YAML list with one item per case:

```yaml
- case_id: car-front-light
  source_sha256: "<the exact locked manifest checksum>"
  output_sha256: "<the exact SHA-256 from the selected successful attempt>"
  scores:
    vehicle_identity: 1
    geometry_viewpoint: 1
    target_coverage: 1
    non_target_preservation: 1
    lighting_material: 1
    color_intent: 1
    artifact_control: 1
    telegram_usability: 1
```

Replace each value with an independent integer score from 1 through 5 after
review. Do not collapse dimensions into an overall impression. Record
measurements from the run artifact rather than estimates.

### Custom wrap-reference fidelity gate

Before a profiled custom reference is released, run the deterministic offline
extraction fixtures:

```bash
.venv/bin/python -m pytest tests/custom_colors/test_analysis.py tests/eval -x
```

The focused fixtures must demonstrate all of the following:

- a solid sample remains in the intended hue family after unrelated
  background, printed text, highlights, and shadows are rejected;
- a multicolor or chameleon sample retains two to five supported palette
  colors instead of collapsing to one average;
- uncertain or mismatched samples fail closed and cannot be auto-approved;
- matte/satin and solid/multicolor values survive profile validation as
  server-owned metadata.

Offline extraction success does not prove generated-output quality. Add
authorized vehicle cases for each custom-reference family to the existing
locked corpus, score `color_intent` for hue-family and transition fidelity, and
score `lighting_material` for matte/satin plausibility. Do not claim exact
physical, SKU, camera-independent, or display-independent matching.

Each paid corpus case still receives at most one provider attempt per explicit
run. A weak result fails the release gate; it never triggers automatic
per-job regeneration.

## 4. Bind evidence and decide the release gate

Use the same manifest and the exact run artifact produced by generation:

```bash
.venv/bin/python -m car_wrap.eval gate \
  --manifest eval/corpus.yaml \
  --run eval/runs/phase-01.json \
  --scores eval/scores.yaml \
  --thresholds eval/thresholds.yaml \
  --report eval/reports/phase-01.json
```

The command rejects missing, extra, duplicate, source- or output-checksum
mismatched cases; multiple selected successes; mixed models; and mismatched
prompt revisions before evaluation or report writing. A release is allowed only
when the report has `"verdict":"pass"` and no failed critical rules.

Exit codes are stable:

- `0` — validation or gate passed
- `1` — evidence was valid, but the quality gate failed
- `2` — command input, manifest, fixture, run, score, threshold, binding, or
  output destination was invalid
- `3` — live provider, credential, or network failure

An unavailable, incomplete, mismatched, unscored, or failing paid benchmark
keeps Phase 1 blocked. Offline test success proves the evaluation machinery,
not model feasibility.
