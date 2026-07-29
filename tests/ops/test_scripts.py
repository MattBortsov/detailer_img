"""Safety contracts for production operation scripts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops"


def scripts() -> list[Path]:
    return sorted(OPS.glob("*.sh"))


def test_scripts_use_strict_bash_and_never_dump_environment() -> None:
    assert scripts()
    for path in scripts():
        text = path.read_text()
        assert text.startswith("#!/usr/bin/env bash")
        assert "set -Eeuo pipefail" in text
        assert "printenv" not in text
        assert ".Config.Env" not in text
        assert "cat .env" not in text
        assert "source .env" not in text


def test_backup_is_metadata_only_and_restore_refuses_live_target() -> None:
    backup = (OPS / "backup-postgres.sh").read_text()
    restore = (OPS / "restore-drill.sh").read_text()
    assert "pg_dump" in backup
    assert "--format=custom" in backup
    assert "umask 077" in backup
    assert "custom_color_data" not in backup
    assert "docker volume" not in backup
    assert "car_wrap_restore_" in restore
    assert "template0" in restore
    assert "dropdb --if-exists" in restore
    assert "POSTGRES_DB" not in restore.split("restore_db=", 1)[1].splitlines()[0]


def test_backup_timer_is_daily_persistent_and_path_scoped() -> None:
    installer = (OPS / "install-backup-timer.sh").read_text()
    assert "OnCalendar=*-*-* 03:30:00 UTC" in installer
    assert "RandomizedDelaySec=30m" in installer
    assert "Persistent=true" in installer
    assert "ExecStart=/root/detailer_img/ops/backup-postgres.sh" in installer
    assert "ReadWritePaths=/var/backups/detailer-img" in installer
    assert "systemctl enable --now detailer-img-backup.timer" in installer
    assert '"${OPS_DIR}/install-backup-timer.sh"' in (OPS / "deploy.sh").read_text()


def test_privacy_scanner_reports_boundaries_not_matches() -> None:
    scanner = (OPS / "privacy-scan.sh").read_text()
    assert "grep -F --" in scanner
    assert "grep -Fq" not in scanner
    assert "PRIVACY_CANARY" in scanner
    assert "data:image/" in scanner
    assert "b64_json" in scanner
    assert "grep -Fn" not in scanner


def test_narrow_directory_guard_rejects_traversal() -> None:
    library = (OPS / "lib.sh").read_text()
    assert 'target" != *"/../"*' in library
    assert "directory traversal is not allowed" in library


def test_bootstrap_is_narrow_and_does_not_touch_blogger_bot() -> None:
    bootstrap = (OPS / "bootstrap-server.sh").read_text()
    assert "fallocate -l 2G /swapfile" in bootstrap
    assert "/var/backups/detailer-img" in bootstrap
    assert "docker stop" not in bootstrap
    assert "bloger_tg_bot" not in bootstrap


def test_deploy_uses_exact_sha_and_scoped_rollback() -> None:
    deploy = (OPS / "deploy.sh").read_text()
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "^[0-9a-f]{40}$" in deploy
    assert "rev-parse HEAD" in deploy
    assert 'reset --hard "$PREVIOUS_SHA"' in deploy
    assert "clean -fd" in deploy
    assert "git clean -fd" in workflow
    assert deploy.count("compose restart nginx") == 2
    assert deploy.index("for service in postgres redis clamav api") < deploy.rindex(
        "compose restart nginx"
    )
    assert deploy.rindex("compose restart nginx") < deploy.index(
        "wait_for_service nginx"
    )
    assert "docker system prune" not in deploy
    assert "docker volume rm" not in deploy


def test_drill_refuses_active_jobs_and_preserves_blogger_baseline() -> None:
    drill = (OPS / "production-drill.sh").read_text()
    assert "active generation jobs block fault drills" in drill
    assert "bloger_tg_bot-" in drill
    assert "provider" not in drill.lower()
    assert "OPENROUTER" not in drill
