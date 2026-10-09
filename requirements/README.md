# Dependencies

`base.txt` contains the rendering and Design2Code dependencies and is included by
the root `requirements.txt`. `agents.txt` and `chartmimic.txt` serve their named
isolated workflows.

The native dependency sets remain separate from the `mmcode` environment:

- `native/frontalk.txt` installs the released FronTalk dependencies plus its
  matching Chrome driver.
- `native/interactweb_bench.txt` is retained only to reproduce archived
  InteractWeb-Bench artifacts. InteractWeb-Bench is outside the active paper.

Do not install either file into the shared `mmcode` environment. The project
root `requirements.txt` remains the active rendering and Design2Code evaluator
dependency set; `requirements/agents.txt` and `requirements/chartmimic.txt`
serve their named isolated workflows.

Conda specifications for recorded-trajectory evaluation are in
`native/3dcodebench.yml` and `native/swemm.yml`. Run environment creation from the
repository root. External renderer/browser setup is documented in
[the runtime guide](../docs/VSV_RUNTIME_SETUP.md).
