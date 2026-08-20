# Google Sheets Backup Service

Python service that backs up Google Sheets to `.xlsx` files on a local/remote Linux server, verifies the backups, applies a retention policy, and sends Telegram notifications.

## What it does

1. Reads spreadsheet IDs from `gsheets_ids.txt`.
2. Downloads each spreadsheet as an `.xlsx` using a Google service account.
3. Verifies that the file is non-empty, can be opened with `openpyxl`, and that the sheet count matches the source.
4. Cleans up old backups: keeps the last 7 daily versions plus the newest backup of each month.
5. Sends a Telegram message with the run summary and attaches each successful backup as a document.

## Quick start

1. Copy `.env.example` to `.env` and fill in all values.
2. Place your Google service-account credentials in `credentials.json`.
3. Create `gsheets_ids.txt` with the spreadsheet IDs you want to back up.
4. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. Run a local test:

   ```bash
   python main.py
   ```

6. Deploy to the target Debian/Ubuntu server:

   ```bash
   python -m deploy
   ```

The deployed service runs daily at `00:00:00 Europe/Prague` via the `gsheets_backup.timer` systemd unit.

## Files

- `main.py` — backup job entry point.
- `deploy.py` — deployment script that copies files, creates a venv, and installs systemd units.
- `utils.py` — helpers: download, verify, retention cleanup, Telegram notifications.
- `config.py` — `.env` loader and validator.
- `.env.example` — required environment variables.
- `example_credentials.json` — example Google service-account credentials.
- `gsheets_ids.example.txt` — example spreadsheet ID list.

## Environment variables

See `.env.example` for the full list. Important paths:

- `PATH_TO_BACKUP_DIRECTORY` — where `.xlsx` backups are stored (e.g. `/data/gsheets-backup/backups`).
- `TARGET_SERVER_PROJECT_PATH` — remote path for the project files (default `/opt/gsheets-backup`).

## Useful server commands

```bash
# Check timer status
sudo systemctl status gsheets_backup.timer

# Trigger a manual run
sudo systemctl start gsheets_backup.service

# View recent logs
sudo journalctl -u gsheets_backup.service -n 50 --no-pager
```

## Notes

- Do not commit real credentials, `.env`, or `gsheets_ids.txt`.
- The deploy script requires passwordless sudo for the target user.
- The backup directory must be writable by the service user (`backup-user` by default).
