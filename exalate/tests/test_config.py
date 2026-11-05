"""Tests for exalate.config resolution with ../env variables."""

import os
from unittest.mock import patch
import pytest

from exalate.config import ExalateConfig, load_environment, normalize_target_env, resolve_config


def test_normalize_target_env():
    assert normalize_target_env("ops") == "JIRA_DC_OPS"
    assert normalize_target_env("OPS") == "JIRA_DC_OPS"
    assert normalize_target_env("JIRA_DC_OPS") == "JIRA_DC_OPS"
    assert normalize_target_env("stg") == "JIRA_DC_STG"
    assert normalize_target_env("STG") == "JIRA_DC_STG"
    assert normalize_target_env("JIRA_DC_STG") == "JIRA_DC_STG"
    assert normalize_target_env(None) == ""


def test_resolve_config_ops(tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text(
        "JIRA_DC_OPS_URL=https://jira-ops.company.internal\n"
        "JIRA_DC_OPS_API_TOKEN=mock_ops_pat_token\n"
        "JIRA_DC_OPS_USERNAME=ops_admin\n"
        "JIRA_DC_OPS_PASSWORD=ops_secret\n"
        "JIRA_SSL_VERIFY=false\n"
    )

    cfg = resolve_config(target="OPS", env_file_path=str(env_file))
    assert cfg.target_env == "JIRA_DC_OPS"
    assert cfg.base_url == "https://jira-ops.company.internal"
    assert cfg.token == "mock_ops_pat_token"
    assert cfg.username == "ops_admin"
    assert cfg.password == "ops_secret"
    assert cfg.verify_ssl is False


def test_resolve_config_stg(tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text(
        "JIRA_DC_STG_URL=https://jira-stg.company.internal\n"
        "JIRA_DC_STG_API_TOKEN=mock_stg_pat_token\n"
        "JIRA_DC_STG_USERNAME=stg_admin\n"
        "JIRA_DC_STG_PASSWORD=stg_secret\n"
        "JIRA_SSL_VERIFY=true\n"
    )

    cfg = resolve_config(target="STG", env_file_path=str(env_file))
    assert cfg.target_env == "JIRA_DC_STG"
    assert cfg.base_url == "https://jira-stg.company.internal"
    assert cfg.token == "mock_stg_pat_token"
    assert cfg.verify_ssl is True


def test_resolve_config_missing_url(tmp_path):
    empty_env = tmp_path / "empty_env"
    empty_env.write_text("# No jira vars\n")

    # Clear environment variables for test
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError) as exc_info:
            resolve_config(target="OPS", env_file_path=str(empty_env))
        assert "No Jira Base URL found for target 'JIRA_DC_OPS'" in str(exc_info.value)
