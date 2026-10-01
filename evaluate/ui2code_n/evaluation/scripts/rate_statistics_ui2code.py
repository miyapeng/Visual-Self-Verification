import json
import re
import argparse

def extract_score(text):
    """Extract the number inside \\boxed{} from a text string."""
    match = re.search(r'\\boxed\{(\d+)\}', text)
    if match:
        return int(match.group(1))
    return None


def analyze_scores(file_path, verbose=False):
    """Analyze and count the distribution of scores from a JSONL file."""
    score_ranges = {
        '0-60': 0,
        '60-70': 0,
        '70-80': 0,
        '80-90': 0,
        '90-100': 0
    }
    total_scores = 0

    with open(file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            try:
                data = json.loads(line.strip())
                text = data.get('mllm_generate', '')
                if verbose:
                    print(f"[Line {line_num}] Raw text: {text[:200]}")  # print first 200 chars
                score = extract_score(text)
                if score is not None:
                    total_scores += 1
                    if score < 60:
                        score_ranges['0-60'] += 1
                    elif score < 70:
                        score_ranges['60-70'] += 1
                    elif score < 80:
                        score_ranges['70-80'] += 1
                    elif score < 90:
                        score_ranges['80-90'] += 1
                    else:
                        score_ranges['90-100'] += 1
            except json.JSONDecodeError:
                print(f"[Warning] Invalid JSON line skipped at line {line_num}")
                continue

    # Print summary
    print(f"\n✅ Total valid samples: {total_scores}")
    print("\n📊 Score range distribution:")
    for range_name, count in score_ranges.items():
        percentage = (count / total_scores * 100) if total_scores > 0 else 0
        print(f"  {range_name}: {percentage:.2f}% ({count} samples)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analyze similarity scores extracted from \\boxed{} in model outputs."
    )
    parser.add_argument(
        "--input", "-i", required=True,
        help="Path to the input JSONL file containing model outputs."
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print extracted text for debugging."
    )
    args = parser.parse_args()

    analyze_scores(args.input, verbose=args.verbose)


# python rate_statistics_ui2code.py --input design2code_output_jsons_2_try/final_merged.jsonl
