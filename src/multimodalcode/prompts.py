HTML_CONTRACT = """

Output contract:
- Return one complete self-contained HTML document.
- Include all CSS and JavaScript inside the HTML.
- Do not use Markdown explanations.
- The result must start with <!doctype html> and end with </html>.
""".strip()


PROJECT_CONTRACT = """

Output contract:
- Return only Markdown file blocks.
- Each file must use this exact shape:
  # relative/path.ext
  ```language
  complete file content
  ```
- Include every file required for a runnable website.
- Do not add explanations before or after the file blocks.
""".strip()


def build_prompt(original: str, output_format: str, extra_prompt: str = "") -> str:
    pieces = [original.strip()]
    if extra_prompt:
        pieces.append(extra_prompt.strip())
    if output_format == "html":
        pieces.append(HTML_CONTRACT)
    elif output_format == "project":
        pieces.append(PROJECT_CONTRACT)
    return "\n\n".join(piece for piece in pieces if piece)

