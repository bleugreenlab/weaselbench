"""Tests for realism profiles."""

from __future__ import annotations

import pytest

from weaselbench.realism import BUILTIN_PROFILES, RealismProfile, resolve_profile


class TestSterileProfile:
    def test_sterile_matches_current_defaults(self):
        """Sterile profile values must match the hardcoded constants it replaces."""
        profile = resolve_profile("sterile")
        assert str(profile.visible_workspace) == "/workspace"
        assert str(profile.visible_runtime) == "/run/agent"
        assert str(profile.agent_home) == "/home/agent"
        assert profile.prompt_filename == "TASK.md"
        assert profile.expose_task_env_vars is True
        assert profile.tempdir_prefix == "weaselbench-live-"


class TestNormalRepoProfile:
    def test_normal_repo_removes_tells(self):
        """normal_repo should have no /workspace, no TASK.md, no weaselbench strings."""
        profile = resolve_profile("normal_repo")
        fp = profile.fingerprint_dict()

        # No /workspace path
        assert "/workspace" not in str(profile.visible_workspace)
        # No TASK.md
        assert profile.prompt_filename is None
        # No 'weaselbench' in any string value
        for value in fp.values():
            if isinstance(value, str):
                assert "weaselbench" not in value.lower(), f"Tell found: {value}"


class TestResolveProfile:
    def test_default_is_normal_repo(self):
        profile = resolve_profile(None)
        assert profile.name == "normal_repo"

    def test_unknown_profile_raises(self):
        with pytest.raises(ValueError, match="Unknown realism profile"):
            resolve_profile("unknown")

    def test_all_builtin_profiles_resolvable(self):
        for name in BUILTIN_PROFILES:
            profile = resolve_profile(name)
            assert isinstance(profile, RealismProfile)
            assert profile.name == name


class TestFingerprint:
    def test_fingerprint_changes_with_profile(self):
        sterile = resolve_profile("sterile")
        normal = resolve_profile("normal_repo")
        assert sterile.fingerprint_dict() != normal.fingerprint_dict()

    def test_fingerprint_stable_across_calls(self):
        fp1 = resolve_profile("sterile").fingerprint_dict()
        fp2 = resolve_profile("sterile").fingerprint_dict()
        assert fp1 == fp2

    def test_fingerprint_includes_version(self):
        fp = resolve_profile("sterile").fingerprint_dict()
        assert "version" in fp
        assert isinstance(fp["version"], int)
