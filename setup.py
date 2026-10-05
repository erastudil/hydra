#!/usr/bin/env python3
from setuptools import setup, find_packages

setup(
    name="hydra-ai-cli",
    version="1.0.0",
    description="Sovereign multi-headed AI summoning CLI.",
    long_description=open("README.md", encoding="utf-8").read() if __import__("os").path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    author="erastudil",
    url="https://github.com/erastudil/hydra",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[],
    entry_points={
        "console_scripts": [
            "hydra = hydra_cli.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
)
