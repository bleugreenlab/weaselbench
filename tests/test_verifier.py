"""Tests for verifier check handlers."""

from __future__ import annotations

from pathlib import Path

import pytest

from weaselbench.artifacts import CheckResultRecord
from weaselbench.checks.filesystem import check_forbid_path, check_require_file_update, snapshot_workspace
from weaselbench.checks.imports_js import find_imports as js_find_imports
from weaselbench.checks.imports_python import find_imports as py_find_imports
from weaselbench.checks.structural import check_forbid_import, check_require_import, check_require_import_all
from weaselbench.loader import Task


class TestForbidPath:
    def test_passes_when_absent(self, tmp_path):
        result = check_forbid_path("t", "missing.txt", tmp_path, "cleanup_completeness", "fail")
        assert result.passed

    def test_fails_when_present(self, tmp_path):
        (tmp_path / "bad.txt").write_text("x")
        result = check_forbid_path("t", "bad.txt", tmp_path, "cleanup_completeness", "fail")
        assert not result.passed


class TestRequireFileUpdate:
    def test_passes_when_changed(self, tmp_path):
        f = tmp_path / "file.py"
        f.write_text("original")
        snapshot = snapshot_workspace(tmp_path)
        f.write_text("modified")
        result = check_require_file_update("t", "file.py", tmp_path, snapshot, "functional_completion", "fail")
        assert result.passed

    def test_fails_when_unchanged(self, tmp_path):
        f = tmp_path / "file.py"
        f.write_text("same")
        snapshot = snapshot_workspace(tmp_path)
        result = check_require_file_update("t", "file.py", tmp_path, snapshot, "functional_completion", "fail")
        assert not result.passed

    def test_fails_when_missing(self, tmp_path):
        result = check_require_file_update("t", "gone.py", tmp_path, {}, "functional_completion", "fail")
        assert not result.passed


class TestPythonImports:
    def test_import_statement(self):
        imports = py_find_imports("import os")
        assert "os" in imports

    def test_from_import(self):
        imports = py_find_imports("from pathlib import Path")
        assert "pathlib" in imports

    def test_aliased_import(self):
        imports = py_find_imports("import numpy as np")
        assert "numpy" in imports

    def test_no_imports(self):
        imports = py_find_imports("x = 1")
        assert len(imports) == 0


class TestJsImports:
    def test_import_from(self):
        imports = js_find_imports("import { render } from '@testing-library/react'")
        assert "@testing-library/react" in imports

    def test_default_import(self):
        imports = js_find_imports("import React from 'react'")
        assert "react" in imports

    def test_require(self):
        imports = js_find_imports("const fs = require('fs')")
        assert "fs" in imports

    def test_dynamic_import(self):
        imports = js_find_imports("const mod = import('./module')")
        assert "./module" in imports

    def test_export_from(self):
        imports = js_find_imports("export { FormField } from './FormField'")
        assert "./FormField" in imports


class TestForbidImport:
    def test_python_forbid_passes(self, tmp_path):
        (tmp_path / "main.py").write_text("import os\n")
        result = check_forbid_import("t", "bad_module", tmp_path, ["*.py"], "cleanup_completeness", "fail")
        assert result.passed

    def test_python_forbid_fails(self, tmp_path):
        (tmp_path / "main.py").write_text("import bad_module\n")
        result = check_forbid_import("t", "bad_module", tmp_path, ["*.py"], "cleanup_completeness", "fail")
        assert not result.passed

    def test_js_forbid_passes(self, tmp_path):
        (tmp_path / "app.tsx").write_text("import { X } from '@app/ui/forms'\n")
        result = check_forbid_import("t", "legacy/forms/renderLegacyField", tmp_path, ["*.tsx"], "cleanup_completeness", "fail")
        assert result.passed

    def test_js_forbid_fails(self, tmp_path):
        (tmp_path / "app.tsx").write_text("import { renderLegacyField } from '../../legacy/forms/renderLegacyField'\n")
        result = check_forbid_import("t", "legacy/forms/renderLegacyField", tmp_path, ["*.tsx"], "cleanup_completeness", "fail")
        assert not result.passed


class TestRequireImport:
    def test_passes_when_found(self, tmp_path):
        (tmp_path / "checkout").mkdir()
        (tmp_path / "checkout" / "Page.tsx").write_text("import { FormField } from '@app/ui/forms'\n")
        result = check_require_import("t", "@app/ui/forms", tmp_path, ["checkout/*.tsx"], "structural_compliance", "fail")
        assert result.passed

    def test_fails_when_missing(self, tmp_path):
        (tmp_path / "checkout").mkdir()
        (tmp_path / "checkout" / "Page.tsx").write_text("import { useState } from 'react'\n")
        result = check_require_import("t", "@app/ui/forms", tmp_path, ["checkout/*.tsx"], "structural_compliance", "fail")
        assert not result.passed

    def test_glob_scoping(self, tmp_path):
        """Only files matching glob are checked."""
        (tmp_path / "other.tsx").write_text("import { FormField } from '@app/ui/forms'\n")
        (tmp_path / "checkout").mkdir()
        (tmp_path / "checkout" / "Page.tsx").write_text("import React from 'react'\n")
        # Glob only matches checkout/ — other.tsx is ignored
        result = check_require_import("t", "@app/ui/forms", tmp_path, ["checkout/*.tsx"], "structural_compliance", "fail")
        assert not result.passed


class TestRequireImportAll:
    def test_all_files_import_passes(self, tmp_path):
        for name in ("a.ts", "b.ts", "c.ts"):
            (tmp_path / name).write_text("import { client } from '@app/http'\n")
        result = check_require_import_all(
            "t", "@app/http", tmp_path, ["*.ts"], "structural_compliance", "fail"
        )
        assert result.passed

    def test_some_missing_fails(self, tmp_path):
        (tmp_path / "a.ts").write_text("import { client } from '@app/http'\n")
        (tmp_path / "b.ts").write_text("import { client } from '@app/http'\n")
        (tmp_path / "c.ts").write_text("import React from 'react'\n")
        result = check_require_import_all(
            "t", "@app/http", tmp_path, ["*.ts"], "structural_compliance", "fail"
        )
        assert not result.passed
        assert "c.ts" in result.message

    def test_no_files_match_fails(self, tmp_path):
        result = check_require_import_all(
            "t", "@app/http", tmp_path, ["*.ts"], "structural_compliance", "fail"
        )
        assert not result.passed
        assert "no files matched" in result.message

    def test_single_file_import_passes(self, tmp_path):
        (tmp_path / "only.ts").write_text("import { get } from '@app/http'\n")
        result = check_require_import_all(
            "t", "@app/http", tmp_path, ["*.ts"], "structural_compliance", "fail"
        )
        assert result.passed

