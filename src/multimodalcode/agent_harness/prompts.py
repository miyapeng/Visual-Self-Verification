"""Controlled mini-swe-agent prompt templates.

The benchmark task text and images are inserted into ``{{ task }}``.  These
templates keep the action interface identical across the three mini runs.
"""

from __future__ import annotations

from .cases import AgentCase
from .registry import PROJECT_ROOT


SYSTEM_TEMPLATE = """You are a coding agent with a Linux shell. Work iteratively: inspect the current state, make a small change, execute or render it, inspect the evidence, and continue until the task is solved. Every response must contain exactly one shell action in this form:

```mswea_bash_command
command
```

Do not merely describe commands. Actually execute them. Avoid interactive terminal programs.
"""


_COMMON_OBSERVATION = """{% if '<MSWEA_MULTIMODAL_CONTENT>' in output.output -%}
<returncode>{{ output.returncode }}</returncode>
<output>{{ output.output }}</output>
{%- elif output.output | length < 12000 -%}
<returncode>{{ output.returncode }}</returncode>
<output>{{ output.output }}</output>
{%- else -%}
<returncode>{{ output.returncode }}</returncode>
<warning>Output truncated; request a narrower view.</warning>
<output_head>{{ output.output[:6000] }}</output_head>
<output_tail>{{ output.output[-6000:] }}</output_tail>
{%- endif %}
{% if output.exception_info %}<exception>{{ output.exception_info }}</exception>{% endif %}"""


def observation_template() -> str:
    return _COMMON_OBSERVATION


def instance_template(case: AgentCase) -> str:
    tool = PROJECT_ROOT / "scripts" / "agents" / "visual_tool.py"
    if case.benchmark == "swe-mm":
        return """<task>
{{ task }}
</task>

Solve this issue in the repository in the current working directory. Inspect the repository, edit only the necessary source files, and run relevant tests. The supplied issue images are part of the task evidence. Do not edit tests or verifier files and do not use the network to search for the original patch.

When finished, create a patch containing only the intended source changes, inspect it, then submit it using a separate final command:
`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.diff`
"""
    if case.benchmark == "design2code":
        return f"""<task>
{{{{ task }}}}
</task>

Build the requested page in the current empty workspace. The target screenshot in the task is the authoritative visual specification. Follow Design2Code's official direct-prompting output contract: return one self-contained `index.html`, include all CSS in that file, use the provided `rick.jpg` for image regions, do not add external dependencies, and do not add JavaScript for dynamic interactions. Do not seek or reconstruct hidden reference code.

Iterate using this fixed renderer, which returns the rendered screenshot to you as a multimodal observation:
`python {tool} render-html index.html candidate.png --width 1440 --height 900`

Check browser errors and visual fidelity after changes. When the best current page is ready, submit with the separate command `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.
"""
    width = case.metadata["width"]
    height = case.metadata["height"]
    return f"""<task>
{{{{ task }}}}
</task>

Create an executable `candidate.py` in the current empty workspace that reproduces the supplied chart. The target figure size is {width} by {height} inches. The program must be deterministic and save both `candidate.pdf` and `candidate.png` in the current directory. Do not inspect or copy hidden ground-truth Python code.

Iterate by executing `python candidate.py`, then inspect the result with:
`python {tool} view-image candidate.png`

Match layout, chart types, data/trends, text, color, styling, and clarity. When ready, submit with the separate command `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.
"""
