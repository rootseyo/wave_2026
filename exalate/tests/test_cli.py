"""End-to-end CLI invocation test."""

import os
import subprocess
import sys
from pathlib import Path


def test_cli_end_to_end(tmp_path):
    """Test CLI execution end-to-end with mock API responses."""
    project_root = Path(__file__).parent.parent.resolve()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "JIRA_BASE_URL=https://jira.mock-test.com\n"
        "JIRA_USERNAME=audit_admin\n"
        "JIRA_PASSWORD=secret_pwd_mock\n"
        "OUTPUT_DIR=" + str(tmp_path / "output") + "\n"
        "VERIFY_SSL=false\n"
    )

    runner_script = tmp_path / "run_test.py"
    runner_script.write_text(f"""
import sys
from pathlib import Path
sys.path.insert(0, {repr(str(project_root))})

from unittest.mock import patch
from main import main

def mock_get_json(self, endpoint, params=None):
    if "syncevents" in endpoint:
        return {{"total": 10, "limit": 50, "results": [{{"id": 1, "issueKey": "MOCK-1"}}]}}
    if "syncrequests" in endpoint:
        return {{"total": 3, "limit": 50, "results": [{{"id": 2, "issueKey": "MOCK-2"}}]}}
    if "errors" in endpoint:
        return {{"total": 2, "limit": 50, "results": [{{"id": 3, "message": "error"}}]}}
    return {{}}

with patch("exalate.client.ExalateHttpClient.get_json", new=mock_get_json):
    sys.argv = ["main.py", "--env-file", {repr(str(env_file))}, "--all"]
    exit_code = main()
    sys.exit(exit_code)
""")

    result = subprocess.run(
        [sys.executable, str(runner_script)],
        cwd=str(project_root),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    assert "Exalate Migration Audit (Jira DC)" in result.stdout
    assert "Outgoing Queue : 10" in result.stdout
    assert "Incoming Queue : 3" in result.stdout
    assert "Errors         : 2" in result.stdout
    assert "Outgoing Queue != Total Outgoing Synced Issues" in result.stdout

    output_dir = tmp_path / "output"
    assert (output_dir / "exalate_report.html").exists()
    assert (output_dir / "exalate_summary.html").exists()
    assert (output_dir / "exalate_summary.json").exists()
    assert (output_dir / "exalate_summary.csv").exists()
    assert (output_dir / "exalate_outgoing.html").exists()
    assert (output_dir / "exalate_outgoing.json").exists()
    assert (output_dir / "exalate_outgoing.csv").exists()
    assert (output_dir / "exalate_incoming.html").exists()
    assert (output_dir / "exalate_incoming.json").exists()
    assert (output_dir / "exalate_incoming.csv").exists()
    assert (output_dir / "exalate_errors.html").exists()
    assert (output_dir / "exalate_errors.json").exists()
    assert (output_dir / "exalate_errors.csv").exists()
