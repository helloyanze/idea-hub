"""Tests for idea_hub.config: typed config loader with env override."""
from pathlib import Path

import pytest

from idea_hub import config

# NOTE: yaml fixtures are written to tmp_path instead of committed files
# because .gitignore excludes config*.yaml as sensitive (auth/API secrets).


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep IDEAHUB_*/DEEPSEEK_* env vars from leaking into these tests."""
    for name in ("IDEAHUB_HOST", "IDEAHUB_PORT", "IDEAHUB_AUTH_USER",
                 "IDEAHUB_AUTH_PASS", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield


def _write_yaml(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


FULL_YAML = """\
host: "0.0.0.0"
port: 9000
db_path: "custom/data/idea.db"
base_path: "custom/base"
auth_user: "admin"
auth_pass: "secret"
deepseek_api_key: "sk-test-123"
rate_limit_per_min: 30
log_level: "DEBUG"
"""

MISSING_AUTH_YAML = """\
host: "127.0.0.1"
port: 8000
db_path: "data/idea.db"
"""


def test_load_defaults_without_config_file(tmp_path, monkeypatch):
    """config.load() with no config.yaml present uses defaults."""
    monkeypatch.chdir(tmp_path)
    cfg = config.load()
    assert cfg.db_path == "data/idea.db"
    assert cfg.base_path == str(Path.cwd())
    # typed object carries every declared field
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8000
    assert cfg.auth_user == ""
    assert cfg.auth_pass == ""
    assert cfg.deepseek_api_key == ""
    assert cfg.rate_limit_per_min == 60
    assert cfg.log_level == "INFO"


def test_load_custom_yaml(tmp_path):
    """config.load(path) reads values from the given yaml verbatim."""
    cfg = config.load(_write_yaml(tmp_path, FULL_YAML))
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9000
    assert cfg.db_path == "custom/data/idea.db"
    assert cfg.base_path == "custom/base"
    assert cfg.auth_user == "admin"
    assert cfg.auth_pass == "secret"
    assert cfg.deepseek_api_key == "sk-test-123"
    assert cfg.rate_limit_per_min == 30
    assert cfg.log_level == "DEBUG"


def test_missing_auth_user_raises(tmp_path):
    """An explicit config file without auth_user is rejected."""
    with pytest.raises(config.ConfigError):
        config.load(_write_yaml(tmp_path, MISSING_AUTH_YAML))


def test_env_override_wins_over_yaml(tmp_path, monkeypatch):
    """Env vars override yaml values for the covered fields."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-999")
    monkeypatch.setenv("IDEAHUB_AUTH_USER", "envuser")
    cfg = config.load(_write_yaml(tmp_path, FULL_YAML))
    assert cfg.deepseek_api_key == "sk-env-999"
    assert cfg.auth_user == "envuser"
    # non-env fields still come from the file
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9000


def test_yaml_port_string_raises_config_error(tmp_path):
    """A quoted YAML port is rejected as non-integer configuration."""
    yaml_text = 'auth_user: "admin"\nport: "8000"\n'
    with pytest.raises(config.ConfigError, match="port must be an integer"):
        config.load(_write_yaml(tmp_path, yaml_text))


def test_yaml_port_null_raises_config_error(tmp_path):
    """A null YAML port is rejected as non-integer configuration."""
    yaml_text = 'auth_user: "admin"\nport: null\n'
    with pytest.raises(config.ConfigError, match="port must be an integer"):
        config.load(_write_yaml(tmp_path, yaml_text))


def test_yaml_rate_limit_string_raises_config_error(tmp_path):
    """A non-numeric YAML rate limit is rejected as non-integer configuration."""
    yaml_text = 'auth_user: "admin"\nrate_limit_per_min: "abc"\n'
    with pytest.raises(config.ConfigError, match="rate_limit_per_min must be an integer"):
        config.load(_write_yaml(tmp_path, yaml_text))


def test_yaml_numeric_port_and_rate_limit_load(tmp_path):
    """Numeric YAML port and rate limit values load as integers."""
    yaml_text = 'auth_user: "admin"\nport: 9000\nrate_limit_per_min: 30\n'
    cfg = config.load(_write_yaml(tmp_path, yaml_text))
    assert cfg.port == 9000
    assert cfg.rate_limit_per_min == 30
