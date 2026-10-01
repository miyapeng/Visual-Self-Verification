"""Compatibility shim for environments whose pip lacks modern PEP 660 support."""

from setuptools import find_packages, setup


setup(
    name="multimodalcode",
    version="0.1.0",
    description="Unified multimodal web-code benchmark runner",
    packages=find_packages("src"),
    package_dir={"": "src"},
    python_requires=">=3.10",
    extras_require={
        "render": ["Pillow>=9.0", "playwright==1.60.0"],
        "transformers": [
            "accelerate>=0.25",
            "Pillow>=9.0",
            "torch>=2.1",
            "transformers>=4.45",
        ],
        "flame": ["Pillow>=9.0"],
        "design2code": [
            "beautifulsoup4>=4.12",
            "colormath>=3.0",
            "matplotlib>=3.7",
            "numpy>=1.24,<2",
            "opencv-python-headless>=4.8",
            "openai-clip>=1.0.1",
            "packaging>=23",
            "scikit-learn>=1.3",
            "scipy>=1.10",
            "torch>=2.1",
            "tqdm>=4.66",
        ],
        "dev": ["pytest>=7"],
    },
)
