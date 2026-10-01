from __future__ import annotations

import re
from typing import Any, Dict, List


UI2CODE_PROMPT = """
I will provide two webpage screenshots. The first is the reference and the
second is generated from it. Evaluate their visual similarity from 0 to 100,
where 0 means completely different and 100 means identical. Consider layout,
spacing, typography, colors, content, and UI components. Explain briefly and
put the final numeric score inside \\boxed{}.
""".strip()


WEB2CODE_PROMPT = """
Assess the two webpage screenshots and output a score from 0 to 10 for each of
the following ten criteria. The first image is the reference and the second is
the generated page. Output ONLY a comma-separated list of ten numbers.

1. Layout consistency: placement of headers, footers, and sidebars.
2. Element alignment: alignment of images, buttons, and text boxes.
3. Proportional accuracy: sizes and aspect ratios of page elements.
4. Visual harmony: balance and overall visual structure.
5. Color scheme and aesthetic match.
6. Aesthetic resemblance: overall design language.
7. Font characteristics: family, size, style, and weight.
8. Textual content match.
9. Numeric and special-character accuracy.
10. User-interface consistency: menus, buttons, and forms.

Do not give 10 unless the corresponding aspect is effectively identical.
""".strip()


def parse_ui2code_score(text: str) -> Dict[str, Any]:
    boxed = re.search(r"\\boxed\{\s*([0-9]+(?:\.[0-9]+)?)\s*\}", text)
    if boxed:
        score = float(boxed.group(1))
    else:
        candidates = [
            float(value)
            for value in re.findall(r"(?<![\w.])([0-9]+(?:\.[0-9]+)?)(?![\w.])", text)
        ]
        candidates = [value for value in candidates if 0 <= value <= 100]
        if not candidates:
            raise ValueError(f"No 0-100 score found in judge response: {text!r}")
        score = candidates[-1]
    if not 0 <= score <= 100:
        raise ValueError(f"UI2Code score out of range: {score}")
    return {"score": score, "score_normalized": score / 100.0}


def parse_web2code_scores(text: str) -> Dict[str, Any]:
    values = [
        float(value)
        for value in re.findall(r"(?<![\w.])([0-9]+(?:\.[0-9]+)?)(?![\w.])", text)
    ]
    if len(values) != 10:
        raise ValueError(f"Expected 10 Web2Code scores, got {len(values)}: {text!r}")
    if any(value < 0 or value > 10 for value in values):
        raise ValueError(f"Web2Code score out of range: {values}")
    visual_structure = sum(values[:4]) / 4
    color_aesthetic = sum(values[4:6]) / 2
    textual_content = sum(values[6:9]) / 3
    user_interface = values[9]
    overall = (
        visual_structure + color_aesthetic + textual_content + user_interface
    ) / 4
    return {
        "criteria": values,
        "visual_structure_and_alignment": visual_structure,
        "color_and_aesthetic_design": color_aesthetic,
        "textual_content_consistency": textual_content,
        "user_interface_and_interactivity": user_interface,
        "score": overall,
        "score_normalized": overall / 10.0,
    }


def profile_prompt(name: str) -> str:
    if name == "ui2code_vlm":
        return UI2CODE_PROMPT
    if name == "web2code_vlm":
        return WEB2CODE_PROMPT
    raise ValueError(f"Unknown judge profile: {name}")


def parse_profile(name: str, text: str) -> Dict[str, Any]:
    if name == "ui2code_vlm":
        return parse_ui2code_score(text)
    if name == "web2code_vlm":
        return parse_web2code_scores(text)
    raise ValueError(f"Unknown judge profile: {name}")

