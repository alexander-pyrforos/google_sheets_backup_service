"""Google Sheets backup job.

Run locally for testing:

    python main.py

The service itself is invoked by systemd via the same entry point.
"""

import sys
from datetime import datetime, timezone

from config import Config, load_config
from utils import (
    BackupError,
    backup_spreadsheet,
    cleanup_old_backups,
    escape_html,
    read_spreadsheet_ids,
    send_telegram_document,
    send_telegram_message,
)


def _build_summary(results: list, cleanup, start_time: datetime, end_time: datetime) -> str:
    """Build an HTML-ish Telegram message summarising the run."""
    success_count = sum(1 for r in results if r.success)
    failure_count = len(results) - success_count
    duration = end_time - start_time

    lines = [
        "<b>Google Sheets backup report</b>",
        f"Time (UTC): {start_time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Duration: {duration}",
        f"Success: {success_count} | Failed: {failure_count}",
        "",
    ]

    for result in results:
        icon = "✅" if result.success else "❌"
        title = escape_html(result.title)
        message = escape_html(result.message)
        lines.append(f"{icon} <b>{title}</b> ({result.spreadsheet_id})")
        lines.append(f"   {message}")

    if cleanup.removed_files:
        lines.append("")
        lines.append(f"🗑 Cleaned up {len(cleanup.removed_files)} old backup(s).")

    if failure_count > 0:
        lines.append("")
        lines.append("Please check the server logs for details.")

    return "\n".join(lines)


def run_backup(config: Config) -> int:
    """Execute the full backup pipeline.

    Returns:
        0 on full success, 1 if any spreadsheet failed.
    """
    start_time = datetime.now(timezone.utc)

    try:
        spreadsheet_ids = read_spreadsheet_ids(config.spreadsheet_ids_file)
    except BackupError as exc:
        send_telegram_message(
            config.telegram_bot_token,
            config.telegram_chat_id,
            f"❌ Backup job failed to start:\n{exc}",
        )
        return 1

    results = []
    for spreadsheet_id in spreadsheet_ids:
        result = backup_spreadsheet(
            spreadsheet_id,
            config.credentials_file,
            config.backup_dir,
        )
        results.append(result)

        if result.success and result.file_path.exists():
            caption = escape_html(f"✅ {result.title} ({result.spreadsheet_id})")
            send_telegram_document(
                config.telegram_bot_token,
                config.telegram_chat_id,
                result.file_path,
                caption=caption,
            )

    cleanup = cleanup_old_backups(config.backup_dir)

    end_time = datetime.now(timezone.utc)
    summary = _build_summary(results, cleanup, start_time, end_time)

    send_telegram_message(
        config.telegram_bot_token,
        config.telegram_chat_id,
        summary,
    )

    return 0 if all(r.success for r in results) else 1


def main() -> int:
    """CLI entry point."""
    try:
        config = load_config()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    return run_backup(config)


if __name__ == "__main__":
    raise SystemExit(main())
