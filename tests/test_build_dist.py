"""
Integration test suite for Hydra Desktop Packaging and Distribution Build Validator.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import pytest

from desktop.build_dist import (
    validate_asset_tree,
    validate_api_contracts,
    validate_session_playback_engine,
    generate_distribution_artifacts,
    run_full_validation,
    REQUIRED_DESKTOP_ASSETS,
    REQUIRED_API_ROUTES,
)
from hydra_cli import __version__
from hydra_cli.config import MODEL_MAP
from scripts.package_desktop import run_checks as run_package_desktop_checks
from scripts.verify import _runtime_files


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_desktop_asset_tree_validation():
    """Verify presence and non-emptiness of all mandatory desktop distribution assets."""
    assets_ok, missing = validate_asset_tree()
    assert assets_ok is True, f"Missing required desktop assets: {missing}"
    assert len(missing) == 0

    for rel_path in REQUIRED_DESKTOP_ASSETS:
        full_path = os.path.join(REPO_ROOT, rel_path.replace("/", os.sep))
        assert os.path.isfile(full_path), f"Asset file missing on disk: {full_path}"
        assert os.path.getsize(full_path) > 0, f"Asset file is empty: {full_path}"


def test_desktop_api_contracts_validation():
    """Verify route registration and execution against live TestClient."""
    api_ok, failures = validate_api_contracts()
    assert api_ok is True, f"API contracts failed: {failures}"
    assert len(failures) == 0
    assert len(REQUIRED_API_ROUTES) == 19


def test_session_playback_engine_validation():
    """Verify action tracing, scrubbing, and report rendering validation."""
    player_ok, failures = validate_session_playback_engine()
    assert player_ok is True, f"Session playback engine failed: {failures}"
    assert len(failures) == 0


def test_distribution_artifacts_generation():
    """Verify standalone launcher and batch runner generation."""
    with tempfile.TemporaryDirectory() as tmp_dist:
        artifacts = generate_distribution_artifacts(tmp_dist)
        assert "launcher" in artifacts
        assert "batch" in artifacts

        launcher_path = artifacts["launcher"]
        batch_path = artifacts["batch"]

        assert os.path.isfile(launcher_path)
        assert os.path.isfile(batch_path)

        with open(launcher_path, "r", encoding="utf-8") as f:
            launcher_text = f.read()
            assert "run_desktop_app" in launcher_text
            assert "HYDRA_DESKTOP_PORT" in launcher_text

        with open(batch_path, "r", encoding="utf-8") as f:
            batch_text = f.read()
            assert "python -m hydra_cli.desktop" in batch_text


def test_build_dist_full_validation_run():
    """Verify desktop/build_dist.py run_full_validation returns exit code 0."""
    rc = run_full_validation()
    assert rc == 0


def test_package_desktop_integration_gate():
    """Verify scripts/package_desktop.py run_checks passes with exit code 0."""
    rc = run_package_desktop_checks()
    assert rc == 0


def test_pyproject_toml_distribution_contract():
    """Verify pyproject.toml distribution specification, dependencies, and metadata."""
    pyproject_path = os.path.join(REPO_ROOT, "pyproject.toml")
    assert os.path.isfile(pyproject_path)

    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert 'name = "hydra-ai-cli"' in content
    assert f'version = "{__version__}"' in content
    assert 'build-backend = "setuptools.build_meta"' in content
    assert 'hydra = "hydra_cli.cli:main"' in content
    assert "prompt_toolkit" in content
    assert "catalog.json" in content


def test_package_json_distribution_contract():
    """Verify package.json Node distribution manifest, version parity, and files list."""
    package_json_path = os.path.join(REPO_ROOT, "package.json")
    assert os.path.isfile(package_json_path)

    with open(package_json_path, "r", encoding="utf-8") as f:
        pkg = json.load(f)

    assert pkg["name"] == "hydra-agent-cli"
    assert pkg["version"] == __version__
    assert pkg["bin"]["hydra"] == "bin/hydra.js"
    assert pkg["main"] == "bin/hydra.js"

    for file_entry in pkg.get("files", []):
        entry_path = os.path.join(REPO_ROOT, file_entry)
        assert os.path.exists(entry_path), f"File in package.json not found: {entry_path}"


def test_executable_launcher_scripts():
    """Verify bin/hydra and bin/hydra.js launcher existence and executable shebangs."""
    bin_hydra = os.path.join(REPO_ROOT, "bin", "hydra")
    bin_hydra_js = os.path.join(REPO_ROOT, "bin", "hydra.js")

    assert os.path.isfile(bin_hydra)
    assert os.path.isfile(bin_hydra_js)

    with open(bin_hydra, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()
        assert first_line in ("#!/usr/bin/env python3", "#!/bin/sh", "#!/usr/bin/env bash")

    with open(bin_hydra_js, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()
        assert first_line == "#!/usr/bin/env node"


def test_installer_manifest_synchronization():
    """Verify install.sh and install.ps1 file lists match runtime files without drift."""
    expected_files = _runtime_files()

    sh_path = os.path.join(REPO_ROOT, "install.sh")
    ps_path = os.path.join(REPO_ROOT, "install.ps1")

    with open(sh_path, "r", encoding="utf-8") as f:
        sh_text = f.read()

    with open(ps_path, "r", encoding="utf-8") as f:
        ps_text = f.read()

    sh_match = re.search(r'FILES="(.*?)"', sh_text, re.S)
    ps_match = re.search(r"\$Files = @\((.*?)\)", ps_text, re.S)

    assert sh_match is not None, "FILES block missing from install.sh"
    assert ps_match is not None, "$Files block missing from install.ps1"

    sh_files = [line.strip() for line in sh_match.group(1).splitlines() if line.strip()]
    ps_files = re.findall(r"'([^']+)'", ps_match.group(1))

    assert sh_files == expected_files, "install.sh drifted from _runtime_files()"
    assert ps_files == expected_files, "install.ps1 drifted from _runtime_files()"


def test_distribution_catalog_and_license():
    """Verify catalog.json integrity and Apache-2.0 LICENSE file presence."""
    catalog_path = os.path.join(REPO_ROOT, "hydra_cli", "catalog.json")
    license_path = os.path.join(REPO_ROOT, "LICENSE")
    readme_path = os.path.join(REPO_ROOT, "README.md")

    assert os.path.isfile(catalog_path)
    assert os.path.isfile(license_path)
    assert os.path.isfile(readme_path)

    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    assert catalog["version"] == __version__
    assert "aliases" in catalog

    with open(license_path, "r", encoding="utf-8") as f:
        lic = f.read()
    assert "Apache License" in lic
    assert "Version 2.0" in lic
