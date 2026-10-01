import json
import os
import re
import ast
import argparse


def save_jsonl_html_blocks(jsonl_path, output_dir, code_key="predict"):
    """
    Extracts ```html``` code blocks that are located *outside* of <think></think> tags
    from a JSONL file and saves them as individual HTML files.
    """
    os.makedirs(output_dir, exist_ok=True)

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"[Line {i}] ❌ Failed to parse JSON, skipping.")
                continue

            if code_key not in obj:
                print(f"[Line {i}] ⚠ Missing key '{code_key}', skipping.")
                continue

            raw_text = obj[code_key]

            # 1️⃣ Remove all content inside <think>...</think>
            cleaned_text = re.sub(r"<think>[\s\S]*?</think>", "", raw_text, flags=re.IGNORECASE)

            # 2️⃣ Search for ```html``` code blocks in the remaining text
            match = re.search(r"```html\s*([\s\S]*?)\s*```", cleaned_text, re.IGNORECASE)
            if not match:
                print(f"[Line {i}] ⚠ No ```html``` block found outside <think> tags.")
                continue

            # 3️⃣ Extract HTML code and handle escape sequences
            html_code = match.group(1)
            try:
                html_code = ast.literal_eval(f"'''{html_code}'''")  # handle \n, \t, etc.
            except Exception:
                pass

            # 4️⃣ Save HTML to a file
            image_name = os.path.splitext(os.path.basename(obj.get("image_path", f"line_{i}")))[0]
            out_file = os.path.join(output_dir, f"{image_name}.html")

            with open(out_file, "w", encoding="utf-8") as out_f:
                out_f.write(html_code)

            print(f"✅ Line {i}: HTML saved to {out_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract ```html``` code blocks outside of <think></think> from a JSONL file and save as HTML files."
    )
    parser.add_argument("--input", "-i", required=True, help="Path to the input JSONL file.")
    parser.add_argument("--output", "-o", required=True, help="Directory to save extracted HTML files.")
    parser.add_argument("--key", "-k", default="predict", help="JSON field containing the text (default: predict).")

    args = parser.parse_args()

    save_jsonl_html_blocks(args.input, args.output, args.key)


if __name__ == "__main__":
    main()

# python extract_html_code.py --input ./output_design2code.jsonl --output ./htmls_design2code --key predict
