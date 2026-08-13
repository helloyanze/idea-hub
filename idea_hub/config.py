"""Configuration loading for Idea Hub."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class Config:
    host: str
    port: int
    db_path: str
    base_path: str
    auth_user: str
    auth_pass: str
    deepseek_api_key: str
    rate_limit_per_min: int
    log_level: str


def load(path: str | None = None) -> Config:
    """Load configuration from defaults, an optional YAML file, and environment."""
    values = {
        "host": "127.0.0.1",
        "port": 8000,
        "db_path": "data/idea.db",
        "base_path": str(Path.cwd()),
        "auth_user": "",
        "auth_pass": "",
        "deepseek_api_key": "",
        "rate_limit_per_min": 60,
        "log_level": "INFO",
    }

    config_path = Path("config.yaml") if path is None else Path(path)
    file_exists = config_path.is_file()

    if file_exists:
        with config_path.open(encoding="utf-8") as config_file:
            file_values = yaml.safe_load(config_file) or {}
        if not isinstance(file_values, dict):
            raise ConfigError("Configuration file must contain a mapping")
        for key in values:
            if key in file_values:
                values[key] = file_values[key]

    environment_overrides = {
        "DEEPSEEK_API_KEY": "deepseek_api_key",
        "IDEAHUB_AUTH_USER": "auth_user",
        "IDEAHUB_AUTH_PASS": "auth_pass",
        "IDEAHUB_HOST": "host",
    }
    for environment_name, config_name in environment_overrides.items():
        if environment_name in os.environ:
            values[config_name] = os.environ[environment_name]

    if "IDEAHUB_PORT" in os.environ:
        try:
            values["port"] = int(os.environ["IDEAHUB_PORT"])
        except ValueError as error:
            raise ConfigError("IDEAHUB_PORT must be an integer") from error

    for key in ("port", "rate_limit_per_min"):
        value = values[key]
        try:
            converted_value = int(value)
        except (ValueError, TypeError) as error:
            raise ConfigError(f"{key} must be an integer") from error
        if type(value) is not int:
            raise ConfigError(f"{key} must be an integer")
        values[key] = converted_value

    if path is not None and file_exists and not values["auth_user"]:
        raise ConfigError("auth_user is required in an explicit configuration file")

    return Config(**values)
