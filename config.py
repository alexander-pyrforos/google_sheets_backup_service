"""Configuration loader for the Google Sheets backup service."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """Runtime configuration."""

    backup_dir: Path
    credentials_file: Path
    spreadsheet_ids_file: Path

    target_server_ip: str
    target_server_username: str
    target_server_ssh_key: Path
    target_server_project_path: str

    telegram_bot_token: str
    telegram_chat_id: str


def load_config(env_file: Path | str | None = None) -> Config:
    """Load configuration from environment variables.

    Args:
        env_file: Optional path to a .env file. Uses the default .env lookup
            from python-dotenv when not provided.

    Returns:
        A validated Config instance.

    Raises:
        ValueError: When a required variable is missing or a path is invalid.
    """
    if env_file is None:
        load_dotenv()
    else:
        load_dotenv(dotenv_path=Path(env_file))

    def _require(name: str) -> str:
        value = os.getenv(name)
        if not value:
            raise ValueError(f"Missing required environment variable: {name}")
        return value

    def _path(name: str) -> Path:
        raw = _require(name)
        expanded = os.path.expandvars(os.path.expanduser(raw))
        return Path(expanded).resolve()

    return Config(
        backup_dir=_path("PATH_TO_BACKUP_DIRECTORY"),
        credentials_file=_path("PATH_TO_CREDENTIALS_FILE"),
        spreadsheet_ids_file=_path("G_SHEETS_SPREADSHEET_IDS_FILE"),
        target_server_ip=_require("TARGET_SERVER_IP_ADDRESS"),
        target_server_username=_require("TARGET_SERVER_USERNAME"),
        target_server_ssh_key=_path("TARGET_SERVER_SSH_KEY_PATH"),
        target_server_project_path=_require("TARGET_SERVER_PROJECT_PATH"),
        telegram_bot_token=_require("TG_NOTIFICATION_BOT_TOKEN"),
        telegram_chat_id=_require("TG_NOTIFICATION_CHAT_ID"),
    )
