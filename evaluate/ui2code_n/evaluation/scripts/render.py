import os
import glob
import logging
import argparse
import concurrent.futures
from tqdm import tqdm
from playwright.sync_api import sync_playwright

# -------------------- Logging Setup --------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# -------------------- Core Functions --------------------
def html_to_image(html_file, output_file):
    """
    Convert a single HTML file to a PNG image using Playwright.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            page = browser.new_page()

            try:
                abs_path = os.path.abspath(html_file)
                page.goto(f'file://{abs_path}')
                page.wait_for_selector('body')

                # Get page dimensions dynamically
                size = page.evaluate("""() => {
                    return {
                        width: Math.max(
                            document.documentElement.clientWidth || 1920,
                            document.body ? document.body.scrollWidth : 1920,
                            document.documentElement.scrollWidth || 1920,
                            document.documentElement.offsetWidth || 1920
                        ),
                        height: Math.max(
                            document.documentElement.clientHeight || 1080,
                            document.body ? document.body.scrollHeight : 1080,
                            document.documentElement.scrollHeight || 1080,
                            document.documentElement.offsetHeight || 1080
                        )
                    };
                }""")

                page.set_viewport_size({
                    "width": max(size["width"], 1920),
                    "height": max(size["height"], 1080)
                })

                # Screenshot
                page.screenshot(path=output_file, full_page=True)
                return True

            finally:
                browser.close()

    except Exception as e:
        logger.error(f"❌ Error converting {html_file}: {str(e)}")
        return False


def process_file(file_data):
    """
    Helper function to process a single (HTML, output) pair.
    """
    html_file, output_file = file_data
    filename = os.path.basename(html_file)
    try:
        result = html_to_image(html_file, output_file)
        return (filename, result)
    except Exception as e:
        logger.error(f"⚠️ Error processing {filename}: {str(e)}")
        return (filename, False)


def batch_convert(html_dir, output_dir, max_workers=4):
    """
    Convert all HTML files in a directory to PNG images using multithreading.
    """
    os.makedirs(output_dir, exist_ok=True)
    html_files = glob.glob(os.path.join(html_dir, "*.html"))
    total = len(html_files)

    if total == 0:
        logger.warning("No HTML files found.")
        return

    logger.info(f"Found {total} HTML files to convert.")

    # Build task list
    tasks = []
    for html_file in html_files:
        filename = os.path.basename(html_file)
        output_file = os.path.join(output_dir, filename.replace(".html", ".png"))

        if os.path.exists(output_file):
            logger.info(f"{output_file} already exists, skipping.")
            continue

        tasks.append((html_file, output_file))

    success, failed = 0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_file, t) for t in tasks]
        for future in tqdm(concurrent.futures.as_completed(futures), total=len(futures), desc="Converting"):
            filename, result = future.result()
            if result:
                success += 1
            else:
                failed += 1

    logger.info(f"✅ Conversion finished: {success} succeeded, {failed} failed (out of {total} files).")


# -------------------- CLI Entry --------------------
def main():
    parser = argparse.ArgumentParser(
        description="Batch convert HTML files to images using Playwright."
    )
    parser.add_argument("--input", "-i", required=True, help="Directory containing HTML files.")
    parser.add_argument("--output", "-o", required=True, help="Directory to save output PNG images.")
    parser.add_argument("--workers", "-w", type=int, default=8, help="Number of concurrent workers (default: 8).")
    args = parser.parse_args()

    batch_convert(args.input, args.output, args.workers)


if __name__ == "__main__":
    main()


# python render.py --input htmls_design2code --output design2code_images
