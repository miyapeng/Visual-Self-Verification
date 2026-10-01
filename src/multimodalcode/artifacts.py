from __future__ import annotations

import html
import re
from pathlib import Path, PurePosixPath
from typing import List, Optional, Tuple


FENCE_RE = re.compile(r"```([A-Za-z0-9_+.-]*)\s*\n(.*?)```", re.DOTALL)
PROJECT_RE = re.compile(
    r"^#\s+([^\n]+)\n(?:\s*\n)*```([^\n`]*)\n(.*?)\n```\s*(?:\n|$)",
    re.DOTALL | re.MULTILINE,
)


def extract_html(text: str) -> str:
    blocks = FENCE_RE.findall(text)
    for language, content in blocks:
        if language.lower() in {"html", "htm"}:
            return content.strip()
    for _, content in blocks:
        if re.search(r"<(?:!doctype\s+html|html|body)\b", content, re.IGNORECASE):
            return content.strip()
    match = re.search(
        r"(?is)(<!doctype\s+html.*?</html>|<html\b.*?</html>)", text
    )
    if match:
        return match.group(1).strip()
    if "<" in text and ">" in text:
        return text.strip()
    return (
        "<!doctype html><html><body><pre>"
        + html.escape(text)
        + "</pre></body></html>"
    )


def _safe_relative_path(value: str) -> Optional[PurePosixPath]:
    cleaned = value.strip().strip("`").replace("\\", "/")
    candidate = PurePosixPath(cleaned)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        return None
    return candidate


def save_project_markdown(text: str, output_dir: str | Path) -> List[str]:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for filename, _language, content in PROJECT_RE.findall(text.strip("\n")):
        relative = _safe_relative_path(filename)
        if relative is None:
            continue
        target = root.joinpath(*relative.parts).resolve()
        if root not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(str(target))
    return written


def parse_flame_output(text: str) -> Tuple[str, str, str]:
    cleaned = text.strip()
    blocks = FENCE_RE.findall(cleaned)
    if len(blocks) >= 2:
        css = next((body for lang, body in blocks if lang.lower() in {"css", "scss", "less"}), "")
        code = next(
            (
                body
                for lang, body in blocks
                if lang.lower() in {"js", "jsx", "ts", "tsx", "javascript", "typescript"}
            ),
            "",
        )
        if code:
            language = next(
                lang
                for lang, body in blocks
                if body == code
            ).lower()
            return css.strip(), language or "jsx", code.strip()
    splitter = re.search(
        r"//\s*(JavaScript|TypeScript)(?:\s+XML)?\s*\((JS|TS|JSX|TSX)\)",
        cleaned,
        re.IGNORECASE,
    )
    if splitter:
        css = cleaned[: splitter.start()]
        css = re.sub(r"^\s*//\s*CSS\s*", "", css, flags=re.IGNORECASE)
        return css.strip(), splitter.group(2).lower(), cleaned[splitter.end():].strip()
    import_at = cleaned.find("import ")
    if import_at >= 0:
        css = re.sub(r"^\s*//\s*CSS\s*", "", cleaned[:import_at], flags=re.IGNORECASE)
        return css.strip(), "jsx", cleaned[import_at:].strip()
    return "", "jsx", cleaned


def flame_browser_html(text: str) -> str:
    css, language, component = parse_flame_output(text)
    export_name = None
    export_match = re.search(
        r"export\s+default\s+([A-Za-z_$][\w$]*)\s*;?", component
    )
    if export_match:
        export_name = export_match.group(1)
        component = component[: export_match.start()] + component[export_match.end():]
    default_function = re.search(
        r"export\s+default\s+(?:function|class)\s+([A-Za-z_$][\w$]*)",
        component,
    )
    if default_function:
        export_name = default_function.group(1)
        component = component.replace("export default ", "", 1)
    component = re.sub(
        r"^\s*import\s+.*?(?:;\s*$|$\n?)",
        "",
        component,
        flags=re.MULTILINE,
    )
    component = re.sub(
        r"^\s*export\s+(?=(?:const|let|var|function|class)\b)",
        "",
        component,
        flags=re.MULTILINE,
    )
    if not export_name:
        names = re.findall(
            r"(?:const|let|var|function|class)\s+([A-Z][A-Za-z0-9_$]*)",
            component,
        )
        export_name = names[-1] if names else "App"
    component = component.replace("</script>", "<\\/script>")
    css = css.replace("</style>", "<\\/style>")
    presets = "react,typescript" if language in {"ts", "tsx"} else "react"
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{css}</style>
  <script crossorigin src="https://unpkg.com/react@18.3.1/umd/react.development.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js"></script>
  <script src="https://unpkg.com/@babel/standalone@7.26.10/babel.min.js"></script>
</head>
<body>
  <div id="root"></div>
  <script type="text/babel" data-presets="{presets}" data-filename="component.{language}">
    const {{ useState, useEffect, useReducer, useContext, useCallback, useMemo, useRef }} = React;
    {component}
    ReactDOM.createRoot(document.getElementById("root")).render(React.createElement({export_name}));
  </script>
</body>
</html>
"""
