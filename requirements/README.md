# Benchmark-specific environments

These files are intentionally separate from the `mmcode` environment:

- `native/frontalk.txt` installs the released FronTalk dependencies plus its
  matching Chrome driver.
- `native/interactweb_bench.txt` is retained only to reproduce archived
  InteractWeb-Bench artifacts. InteractWeb-Bench is outside the active paper.

Do not install either file into the shared `mmcode` environment. The project
root `requirements.txt` remains the active rendering and Design2Code evaluator
dependency set; `requirements-agents.txt` and `requirements-chartmimic.txt`
serve their named isolated workflows.
