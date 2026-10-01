# Evaluation

We evaluate the performance of **UI2Code^N** using the following benchmarks:
*   **[Design2Code](https://huggingface.co/datasets/SALT-NLP/Design2Code-hf)**
*   **[Flame-React-Eva](https://huggingface.co/datasets/Flame-Code-VLM/Flame-Eval-React)**
*   **[Web2Code](https://huggingface.co/datasets/MBZUAI/Web2Code/blob/main/Web2Code_eval.jsonl)**
*   **[UI2Code-Real]()**
*   **[UIPolish-Real]()**
*   **[UIPolish-Synthetic]()**

  
**Evaluation Steps:**
1.  **Data Preparation**: Please download the preprocessed dataset and put it in the `./data/` directory.

2.  **Model Evaluation**: Once model prediction is complete, run the evaluation script to obtain the results. 
    ```bash
    cd scripts
    ```
    1) extract html code
    ```bash
    python extract_html_code.py --input ./output_design2code.jsonl --output ./htmls_design2code --key predict
    ```
    2) render image from html file
    ```bash
    python render.py --input htmls_design2code --output design2code_images
    ```
    3) compare reference UI screenshot and rendered image
    ```bash
    python autocall_multi_judge.py \
      --input model_generation_design2code.jsonl \
      --output-prefix ./design2code_try/generate_worker \
      --index-prefix ./design2code_try/data_index_worker \
      --num-workers 8 \
      --model o4-mini-2025-04-16 \
      --api-key sk-your-key \
      --api-base https://api.openai.com/v1
    ```
    4) calculate accuracy for UI-to-code generation
    ```bash
    python rate_statistics_ui2code.py --input design2code_output_jsons/final_merged.jsonl
    ```
    6) calculate accuracy for UI polishing
     ```bash
    python rate_statistics.py --input polish_output_jsons_gpt_5/final_merged.jsonl
    ```
    

