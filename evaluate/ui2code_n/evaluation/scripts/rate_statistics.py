import json
import re
import argparse

# Mapping of Chinese numerals to integers
CN_NUM_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10
}


def extract_score(text):
    """
    Extract text inside \\boxed{} using regular expressions.
    """
    match = re.search(r'\\boxed\{([^}]*)\}', text)
    if match:
        return match.group(1)
    return None


def chinese_num_to_int(text):
    """
    Convert a Chinese numeral (e.g., "三") or digit string (e.g., "3") to an integer.
    Supports only 1–10 for now.
    """
    if text in CN_NUM_MAP:
        return CN_NUM_MAP[text]
    if text.isdigit():
        return int(text)
    return None


def analyze_scores(file_path, verbose=False):
    """
    Analyze model outputs in a JSONL file to count how many times
    the extracted text mentions the 3rd image (e.g., '第3张' or '第三张').
    """
    total_samples = 0
    polish_correct = 0

    with open(file_path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            try:
                data = json.loads(line.strip())
                text = data.get('mllm_generate', '')

                if verbose:
                    print(f"[Line {i}] Raw text: {text[:200]}")

                score = extract_score(text)
                if score:
                    if verbose:
                        print(f"[Line {i}] Extracted score: {score}")

                    # Match patterns like "第三张" or "第3张"
                    match = re.search(r'第([一二三四五六七八九十\d]+)张', score)
                    if match:
                        cn_num = match.group(1)
                        num = chinese_num_to_int(cn_num)

                        if verbose:
                            print(f"Matched phrase: 第{cn_num}张 → numeric: {num}")

                        if num == 3:
                            polish_correct += 1
                        else:
                            if verbose:
                                print(f"[Line {i}] ⚠️ Incorrect polish result: {score}")
                    else:
                        if verbose:
                            print(f"[Line {i}] No valid match found in extracted score.")
                total_samples += 1

            except json.JSONDecodeError:
                print(f"[Warning] Invalid JSON skipped at line {i}")
                continue

    # Print results
    print(f"\n✅ Total samples: {total_samples}")
    print(f"🎯 Correct Polish samples (第3张): {polish_correct}")
    if total_samples > 0:
        print(f"Accuracy: {polish_correct / total_samples * 100:.2f}%")
    else:
        print("No valid samples found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analyze model outputs to count correct UI polishing predictions."
    )
    parser.add_argument("--input", "-i", required=True, help="Path to the input JSONL file.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed debug info.")
    args = parser.parse_args()

    analyze_scores(args.input, verbose=args.verbose)


# python rate_statistics.py --input polish_output_jsons_gpt_5/final_merged.jsonl
