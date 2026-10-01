import os
import json
import base64
from PIL import Image
from multiprocessing import Process
import openai
from openai import OpenAI
import math
import argparse


def is_contains_chinese(s):
    """Check if a string contains Chinese characters."""
    return any('\u4e00' <= c <= '\u9fa5' for c in s)


def get_response(inputs, idx, worker_id, output_file, index_file, error_idxs, client, model_name):
    """Send one sample to the model API and write the response."""
    try:
        prompt = inputs[idx]

        question = (
            "I will provide you with two images. The first is the reference image, "
            "and the second is generated based on the first one. Please evaluate "
            "their visual similarity with a score from 0 to 100 (0 = completely different, "
            "100 = identical). Put your final score inside LaTeX \\boxed{} and explain your reasoning."
        )

        prompt_text = "question: " + str(question)
        image_content = [{"type": "text", "text": prompt_text}]

        # Base64 encode input image
        img_path = prompt["image_path"]
        if isinstance(img_path, list) and len(img_path) == 1:
            img_path = img_path[0]

        with open(img_path, "rb") as image_file:
            imagebytes = base64.b64encode(image_file.read()).decode("utf-8")

        image_content.append(
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + imagebytes}}
        )

        # Compare with rendered image
        image_name = os.path.splitext(os.path.basename(img_path))[0]
        img_2_path = f"design2code_images/{image_name}.png"
        with open(img_2_path, "rb") as image_file:
            imagebytes = base64.b64encode(image_file.read()).decode("utf-8")

        image_content.append(
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + imagebytes}}
        )

        # Model call
        if model_name == "o4-mini-2025-04-16":
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": image_content}],
                max_completion_tokens=10000,
            )
        else:
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": image_content}],
                max_tokens=10000,
            )

        output = response.choices[0].message

    except openai.BadRequestError as e:
        error_response = e.response.json()
        error_type = error_response.get("error", {}).get("code", "Unknown")
        print(f"[Worker {worker_id}] BadRequestError: {error_type}")
        if error_type == "content_policy_violation":
            output = ""
        else:
            error_idxs.append(idx)
            raise e
    except Exception as ex:
        error_idxs.append(idx)
        print(f"[Worker {worker_id}] Exception at {idx}: {ex}")
        return

    result = inputs[idx]
    result["mllm_generate"] = str(output)

    with open(output_file, "a", encoding="utf-8") as fout:
        fout.write(json.dumps(result, ensure_ascii=False) + "\n")

    with open(index_file, "w") as fidx:
        fidx.write(str(idx + 1))

    print(f"[Worker {worker_id}] Processed index: {idx}")


def worker_fn(worker_id, inputs, start_idx, end_idx, args, client):
    """Worker function executed by each process."""
    index_file = f"{args.index_prefix}_{worker_id}.txt"
    output_file = f"{args.output_prefix}_{worker_id}.jsonl"

    if os.path.exists(index_file):
        with open(index_file, "r") as f:
            start_idx = int(f.read().strip())

    error_idxs = []
    for idx in range(start_idx, end_idx):
        get_response(inputs, idx, worker_id, output_file, index_file, error_idxs, client, args.model)

    if args.repeat_try:
        repeat_times = 0
        while repeat_times < args.max_repeat_times and len(error_idxs) > 0:
            new_error_idxs = []
            for idx in error_idxs:
                get_response(inputs, idx, worker_id, output_file, index_file, new_error_idxs, client, args.model)
            error_idxs = new_error_idxs
            repeat_times += 1


def run_multiprocess(args, client):
    """Run multiple workers in parallel."""
    with open(args.input, "r", encoding="utf-8") as f:
        all_inputs = [json.loads(line) for line in f if line.strip()]

    total = len(all_inputs)
    chunk_size = math.ceil(total / args.num_workers)
    processes = []

    for i in range(args.num_workers):
        start = i * chunk_size
        end = min(start + chunk_size, total)
        p = Process(target=worker_fn, args=(i, all_inputs, start, end, args, client))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()


def main():
    parser = argparse.ArgumentParser(
        description="Parallel OpenAI visual evaluation script with multi-process support."
    )
    parser.add_argument("--input", "-i", required=True, help="Path to input JSONL file.")
    parser.add_argument("--output-prefix", "-o", default="output_worker", help="Prefix for output files.")
    parser.add_argument("--index-prefix", "-x", default="index_worker", help="Prefix for index tracking files.")
    parser.add_argument("--num-workers", "-n", type=int, default=4, help="Number of worker processes.")
    parser.add_argument("--model", "-m", default="o4-mini-2025-04-16", help="Model name to call.")
    parser.add_argument("--repeat-try", action="store_true", help="Retry failed samples within a loop.")
    parser.add_argument("--max-repeat-times", type=int, default=1, help="Maximum retry count.")
    parser.add_argument("--api-base", type=str, default="", help="Custom API base URL.")
    parser.add_argument("--api-key", type=str, default="", help="OpenAI API key.")
    args = parser.parse_args()

    # Initialize client
    client = OpenAI(api_key=args.api_key, base_url=args.api_base)

    print(f"🚀 Starting processing: {args.input}")
    print(f"Model: {args.model}, Workers: {args.num_workers}")
    os.makedirs(os.path.dirname(args.output_prefix), exist_ok=True)

    run_multiprocess(args, client)
    print("✅ All processes completed successfully.")


if __name__ == "__main__":
    main()

# python autocall_multi_judge.py \
#   --input model_generation_design2code.jsonl \
#   --output-prefix ./design2code_try/generate_worker \
#   --index-prefix ./design2code_try/data_index_worker \
#   --num-workers 8 \
#   --model o4-mini-2025-04-16 \
#   --api-key sk-your-key \
#   --api-base https://api.openai.com/v1

