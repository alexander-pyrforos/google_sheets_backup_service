"""Deploy the backup service to a remote Debian/Ubuntu server.

Run from the local development machine:

    python -m deploy

The script expects a configured `.env` with target server details and SSH
access via key-based authentication.
"""

import shlex
import subprocess
import sys
from pathlib import Path

from config import Config, load_config


SYSTEMD_SERVICE_NAME = "gsheets_backup"
PROJECT_FILES = [
    "main.py",
    "config.py",
    "utils.py",
    "requirements.txt",
    ".env.example",
    "example_credentials.json",
    "gsheets_ids.example.txt",
    "README.md",
]


def _run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    """Run a local command and return the completed process."""
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        encoding="utf-8",
    )


def _remote(config: Config, remote_cmd: str, *, check: bool = True) -> str:
    """Run a command on the target server via SSH."""
    ssh_cmd = [
        "ssh",
        "-i", str(config.target_server_ssh_key),
        "-o", "StrictHostKeyChecking=accept-new",
        f"{config.target_server_username}@{config.target_server_ip}",
        remote_cmd,
    ]
    result = _run(ssh_cmd, check=check, capture=True)
    return result.stdout


def _copy_to_remote(config: Config, local_path: Path, remote_dest: str) -> None:
    """Copy a local file or directory to the target server with scp."""
    remote_target = f"{config.target_server_username}@{config.target_server_ip}:{remote_dest}"
    _run(["scp", "-i", str(config.target_server_ssh_key), "-r", str(local_path), remote_target])


def _ensure_remote_dir(config: Config, remote_path: str) -> None:
    """Create a remote directory owned by the backup user."""
    user = shlex.quote(config.target_server_username)
    quoted_path = shlex.quote(remote_path)
    _remote(config, f"sudo mkdir -p {quoted_path}")
    _remote(config, f"sudo chown -R {user}:{user} {quoted_path}")
    _remote(config, f"sudo chmod 750 {quoted_path}")


def _ensure_backup_dir(config: Config) -> None:
    """Create the backup data directory on /data and ensure permissions."""
    backup_dir = shlex.quote(str(config.backup_dir))
    user = shlex.quote(config.target_server_username)
    _remote(config, f"sudo mkdir -p {backup_dir}")
    _remote(config, f"sudo chown -R {user}:{user} {backup_dir}")
    _remote(config, f"sudo chmod 750 {backup_dir}")


def _sync_project_files(config: Config) -> None:
    """Copy project files to the target server."""
    project_path = config.target_server_project_path
    _ensure_remote_dir(config, project_path)
    _ensure_backup_dir(config)

    for filename in PROJECT_FILES:
        local = Path(filename)
        if not local.exists():
            print(f"Skipping missing file: {filename}")
            continue
        _copy_to_remote(config, local, project_path)

    # .env and real credentials must be present locally for deployment.
    for secret in [".env", "credentials.json", "gsheets_ids.txt"]:
        local = Path(secret)
        if local.exists():
            _copy_to_remote(config, local, project_path)
        else:
            print(f"Warning: {secret} not found locally; it must exist on the server.")


def _install_dependencies(config: Config) -> None:
    """Create a venv and install Python dependencies on the server."""
    project_path = config.target_server_project_path
    venv_path = f"{project_path}/venv"
    commands = [
        "sudo apt-get update",
        "sudo apt-get install -y python3-venv python3-pip",
        f"python3 -m venv {shlex.quote(venv_path)}",
        f"{shlex.quote(venv_path)}/bin/pip install --upgrade pip",
        f"{shlex.quote(venv_path)}/bin/pip install -r {shlex.quote(project_path)}/requirements.txt",
    ]
    for cmd in commands:
        print(f"Running: {cmd}")
        _remote(config, cmd)


def _systemd_unit_content(config: Config) -> str:
    """Render the systemd service unit file."""
    project_path = config.target_server_project_path
    return f"""[Unit]
Description=Google Sheets backup job
After=network.target

[Service]
Type=oneshot
WorkingDirectory={project_path}
ExecStart={project_path}/venv/bin/python {project_path}/main.py
User={config.target_server_username}
Environment=PYTHONUNBUFFERED=1
"""


def _systemd_timer_content() -> str:
    """Render the systemd timer unit file.

    Europe/Prague switches between CET (UTC+1) and CEST (UTC+2). The timer
    uses the `Europe/Prague` timezone and fires at 00:00:00 local time.
    """
    return """[Unit]
Description=Daily Google Sheets backup timer

[Timer]
OnCalendar=*-*-* 00:00:00 Europe/Prague
Persistent=true

[Install]
WantedBy=timers.target
"""


def _install_systemd_units(config: Config) -> None:
    """Install, enable and start the systemd service and timer."""
    service_content = _systemd_unit_content(config)
    timer_content = _systemd_timer_content()

    # Write the unit files via heredocs to avoid quoting issues.
    _remote(
        config,
        f"cat <<'SYSTEMD_EOF' | sudo tee /etc/systemd/system/{SYSTEMD_SERVICE_NAME}.service > /dev/null\n{service_content}SYSTEMD_EOF",
    )
    _remote(
        config,
        f"cat <<'SYSTEMD_EOF' | sudo tee /etc/systemd/system/{SYSTEMD_SERVICE_NAME}.timer > /dev/null\n{timer_content}SYSTEMD_EOF",
    )

    _remote(config, "sudo systemctl daemon-reload")
    _remote(config, f"sudo systemctl enable {SYSTEMD_SERVICE_NAME}.timer")
    _remote(config, f"sudo systemctl start {SYSTEMD_SERVICE_NAME}.timer")


def _validate_local_env() -> None:
    """Check that required local secrets are in place before deploying."""
    missing: list[str] = []
    for secret in [".env", "credentials.json", "gsheets_ids.txt"]:
        if not Path(secret).exists():
            missing.append(secret)

    if missing:
        print(
            "Warning: the following files are missing locally and are needed on the server: "
            + ", ".join(missing)
        )


def deploy(config: Config) -> int:
    """Run the full deployment flow.

    Returns:
        0 on success, 1 on failure.
    """
    _validate_local_env()

    try:
        print("Syncing project files...")
        _sync_project_files(config)

        print("Installing dependencies on target server...")
        _install_dependencies(config)

        print("Installing systemd units...")
        _install_systemd_units(config)

        print("Deployment complete.")
        print(
            _remote(
                config,
                f"sudo systemctl status --no-pager {SYSTEMD_SERVICE_NAME}.timer",
                check=False,
            )
        )
    except subprocess.CalledProcessError as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1

    return 0


def main() -> int:
    """CLI entry point."""
    try:
        config = load_config()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    return deploy(config)


if __name__ == "__main__":
    raise SystemExit(main())
