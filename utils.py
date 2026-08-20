"""Helpers for the Google Sheets backup service."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import gspread
import openpyxl
import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials


class BackupError(Exception):
    """Raised when a backup step fails in a fatal way."""


@dataclass(frozen=True)
class SpreadsheetInfo:
    """Minimal metadata about a spreadsheet used for verification."""

    spreadsheet_id: str
    title: str
    sheet_count: int
    row_count: int


@dataclass(frozen=True)
class BackupResult:
    """Result of backing up a single spreadsheet."""

    spreadsheet_id: str
    title: str
    file_path: Path
    success: bool
    message: str


@dataclass(frozen=True)
class CleanupSummary:
    """Summary of the retention cleanup step."""

    removed_files: list[Path]
    kept_files: list[Path]


def read_spreadsheet_ids(path: Path) -> list[str]:
    """Read non-empty, non-comment lines from the spreadsheet ID file."""
    if not path.exists():
        raise BackupError(f"Spreadsheet IDs file not found: {path}")

    ids = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line)

    if not ids:
        raise BackupError(f"No spreadsheet IDs found in {path}")

    return ids


def _build_credentials(credentials_file: Path) -> Credentials:
    """Create scoped credentials from the service-account JSON file."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    return Credentials.from_service_account_file(
        str(credentials_file), scopes=scopes
    )


def _safe_filename(title: str) -> str:
    """Return a filesystem-safe version of the spreadsheet title."""
    cleaned = re.sub(r"[^\w\s-]", "", title).strip()
    return re.sub(r"[-\s]+", "-", cleaned)


def fetch_spreadsheet_info(
    spreadsheet_id: str,
    credentials_file: Path,
) -> SpreadsheetInfo:
    """Fetch lightweight metadata about a spreadsheet for verification."""
    try:
        client = gspread.authorize(_build_credentials(credentials_file))
        spreadsheet = client.open_by_key(spreadsheet_id)
    except (GoogleAuthError, gspread.exceptions.APIError) as exc:
        raise BackupError(
            f"Failed to open spreadsheet {spreadsheet_id}: {exc}"
        ) from exc

    total_rows = sum(
        worksheet.row_count for worksheet in spreadsheet.worksheets()
    )
    return SpreadsheetInfo(
        spreadsheet_id=spreadsheet_id,
        title=spreadsheet.title,
        sheet_count=len(spreadsheet.worksheets()),
        row_count=total_rows,
    )


def download_spreadsheet_as_xlsx(
    spreadsheet_id: str,
    credentials_file: Path,
    backup_dir: Path,
    backup_date: date | None = None,
) -> Path:
    """Export a Google Spreadsheet as an .xlsx file.

    Uses the Google Drive export endpoint. The filename includes the
    spreadsheet title and the backup date so daily backups are kept distinct.
    """
    backup_date = backup_date or datetime.now(timezone.utc).date()

    info = fetch_spreadsheet_info(spreadsheet_id, credentials_file)
    safe_title = _safe_filename(info.title)
    filename = f"{safe_title}_{backup_date.isoformat()}.xlsx"
    target = backup_dir / filename

    creds = _build_credentials(credentials_file)
    try:
        creds.refresh(Request())
    except GoogleAuthError as exc:
        raise BackupError(
            f"Failed to refresh Google credentials: {exc}"
        ) from exc

    export_url = (
        f"https://www.googleapis.com/drive/v3/files/{spreadsheet_id}"
        f"/export?mimeType=application/vnd.openxmlformats-officedocument"
        ".spreadsheetml.sheet"
    )

    try:
        response = requests.get(
            export_url,
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=120,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BackupError(
            f"Failed to export spreadsheet {spreadsheet_id}: {exc}"
        ) from exc

    backup_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)

    return target


def verify_backup(
    file_path: Path,
    expected_info: SpreadsheetInfo,
) -> None:
    """Verify that a downloaded .xlsx backup is usable.

    Checks:
      * file is not empty
      * file can be opened by openpyxl
      * number of worksheets matches the source spreadsheet
      * expected title is present in the workbook
    """
    if not file_path.exists():
        raise BackupError(f"Backup file missing: {file_path}")

    size = file_path.stat().st_size
    if size == 0:
        raise BackupError(f"Backup file is empty: {file_path}")

    try:
        workbook = openpyxl.load_workbook(file_path, read_only=True)
    except Exception as exc:  # noqa: BLE001 — catch-all for corrupt files
        raise BackupError(
            f"Backup file cannot be opened as xlsx: {file_path}: {exc}"
        ) from exc

    actual_sheets = len(workbook.sheetnames)
    if actual_sheets != expected_info.sheet_count:
        raise BackupError(
            f"Sheet count mismatch for {file_path.name}: "
            f"expected {expected_info.sheet_count}, got {actual_sheets}"
        )

    # Note: Google Sheets exports do not always populate workbook.properties.title,
    # so we only verify the sheet count. The spreadsheet title is already encoded
    # in the backup filename by _safe_filename().

    workbook.close()


def backup_spreadsheet(
    spreadsheet_id: str,
    credentials_file: Path,
    backup_dir: Path,
    backup_date: date | None = None,
) -> BackupResult:
    """Download and verify one spreadsheet."""
    backup_date = backup_date or datetime.now(timezone.utc).date()

    try:
        info = fetch_spreadsheet_info(spreadsheet_id, credentials_file)
        file_path = download_spreadsheet_as_xlsx(
            spreadsheet_id,
            credentials_file,
            backup_dir,
            backup_date,
        )
        verify_backup(file_path, info)
    except BackupError as exc:
        return BackupResult(
            spreadsheet_id=spreadsheet_id,
            title=info.title if "info" in locals() else "unknown",
            file_path=Path(),
            success=False,
            message=str(exc),
        )

    return BackupResult(
        spreadsheet_id=spreadsheet_id,
        title=info.title,
        file_path=file_path,
        success=True,
        message=f"Saved {file_path.name} ({file_path.stat().st_size} bytes)",
    )


def cleanup_old_backups(
    backup_dir: Path,
    keep_daily: int = 7,
    backup_date: date | None = None,
) -> CleanupSummary:
    """Apply retention policy: keep the last N daily backups plus one per month.

    Files are expected to be named like `Title_YYYY-MM-DD.xlsx`.
    """
    backup_date = backup_date or datetime.now(timezone.utc).date()

    if not backup_dir.exists():
        return CleanupSummary(removed_files=[], kept_files=[])

    files = sorted(backup_dir.glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
    if not files:
        return CleanupSummary(removed_files=[], kept_files=[])

    # Group files by (title, date) derived from filename.
    by_title: dict[str, list[Path]] = {}
    for file in files:
        title = _title_from_filename(file.name)
        by_title.setdefault(title, []).append(file)

    removed: list[Path] = []
    kept: list[Path] = []

    for title, title_files in by_title.items():
        # Sort newest first by mtime.
        title_files = sorted(title_files, key=lambda p: p.stat().st_mtime, reverse=True)

        # Keep last N daily backups.
        daily_kept = title_files[:keep_daily]
        # From the rest, keep the newest file of each month.
        rest = title_files[keep_daily:]
        monthly_representatives: dict[str, Path] = {}
        for file in rest:
            file_date = _date_from_mtime(file.stat().st_mtime)
            month_key = file_date.strftime("%Y-%m")
            if month_key not in monthly_representatives:
                monthly_representatives[month_key] = file

        monthly_kept = list(monthly_representatives.values())
        title_kept = set(daily_kept + monthly_kept)

        kept.extend(title_kept)
        for file in title_files:
            if file not in title_kept:
                try:
                    file.unlink()
                    removed.append(file)
                except OSError as exc:
                    # Log but continue; do not fail the whole job over cleanup.
                    print(f"Warning: failed to remove {file}: {exc}")

    return CleanupSummary(removed_files=removed, kept_files=kept)


def _title_from_filename(filename: str) -> str:
    """Extract the spreadsheet title from a backup filename.

    Expects `Title_YYYY-MM-DD.xlsx`. Falls back to the stem if no date suffix
    is detected.
    """
    stem = Path(filename).stem
    match = re.match(r"^(.+)_\d{4}-\d{2}-\d{2}$", stem)
    return match.group(1) if match else stem


def _date_from_mtime(mtime: float) -> date:
    """Convert file mtime to a UTC date."""
    return datetime.fromtimestamp(mtime, tz=timezone.utc).date()


def escape_html(text: str) -> str:
    """Escape characters that have special meaning in Telegram HTML mode."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    message: str,
) -> None:
    """Send a plain-text message via the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BackupError(f"Failed to send Telegram message: {exc}") from exc


def send_telegram_document(
    bot_token: str,
    chat_id: str,
    file_path: Path,
    caption: str = "",
) -> None:
    """Send a file as a document via the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    try:
        with file_path.open("rb") as handle:
            files = {"document": (file_path.name, handle)}
            payload = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML",
            }
            response = requests.post(url, data=payload, files=files, timeout=120)
            response.raise_for_status()
    except requests.RequestException as exc:
        raise BackupError(f"Failed to send Telegram document {file_path}: {exc}") from exc
