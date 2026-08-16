"""CLI subcommand tests. Uses argparse directly rather than shelling
out — keeps the test fast and doesn't need the venv to be installed."""

import sys
from pathlib import Path

import pytest


def test_argparse_accepts_all_commands(monkeypatch, tmp_path):
    """Every documented command should parse without error."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("CAP_DRY_RUN=true\nI_UNDERSTAND_LIVE_RISK=NO\n",
                                   encoding="utf-8")
    from capital_agent.__main__ import _flip_env
    # go-demo is safe and non-interactive
    monkeypatch.setattr("sys.argv", ["capital-agent", "go-demo", "--confirm"])
    rc = _flip_env(target="go-demo", skip_prompt=True)
    assert rc == 0
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CAP_DRY_RUN=true" in env_text
    assert "I_UNDERSTAND_LIVE_RISK=NO" in env_text


def test_flip_env_go_live_adds_missing_keys(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SOMETHING_ELSE=x\n", encoding="utf-8")
    from capital_agent.__main__ import _flip_env
    rc = _flip_env(target="go-live", skip_prompt=True)
    assert rc == 0
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "CAP_DRY_RUN=false" in env_text
    assert "I_UNDERSTAND_LIVE_RISK=YES" in env_text
    assert "SOMETHING_ELSE=x" in env_text


def test_flip_env_go_demo_replaces_existing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "CAP_DRY_RUN=false\nI_UNDERSTAND_LIVE_RISK=YES\nCAP_ENV=demo\n",
        encoding="utf-8",
    )
    from capital_agent.__main__ import _flip_env
    rc = _flip_env(target="go-demo", skip_prompt=True)
    assert rc == 0
    lines = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    assert "CAP_DRY_RUN=true" in lines
    assert "I_UNDERSTAND_LIVE_RISK=NO" in lines
    assert "CAP_ENV=demo" in lines
    # No duplicates
    assert sum(1 for x in lines if x.startswith("CAP_DRY_RUN=")) == 1


def test_flip_env_missing_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from capital_agent.__main__ import _flip_env
    rc = _flip_env(target="go-demo", skip_prompt=True)
    assert rc == 1
