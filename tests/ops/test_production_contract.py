"""Cross-plan Phase 5 production contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_every_operations_requirement_has_an_executable_artifact() -> None:
    mapping = {
        "OPS-01": ("docker-compose.yml", "ops/deploy.sh"),
        "OPS-02": ("nginx/car-wrap.conf", "tests/ops/test_compose_contract.py"),
        "OPS-03": (
            "ops/backup-postgres.sh",
            "ops/install-backup-timer.sh",
            "docs/runbook.md",
        ),
        "OPS-04": ("ops/production-drill.sh", "ops/privacy-scan.sh"),
    }
    for artifacts in mapping.values():
        for artifact in artifacts:
            assert (ROOT / artifact).is_file()


def test_runbook_covers_the_complete_fault_and_recovery_matrix() -> None:
    runbook = (ROOT / "docs/runbook.md").read_text().lower()
    required = (
        "duplicate",
        "redis",
        "provider",
        "delivery",
        "worker",
        "postgresql",
        "reboot",
        "certbot",
        "privacy",
        "restore",
        "rollback",
    )
    for term in required:
        assert term in runbook


def test_ops_do_not_add_frameworks_or_media_backup_paths() -> None:
    operational_text = "\n".join(
        path.read_text()
        for directory in ("ops", "nginx")
        for path in sorted((ROOT / directory).glob("*"))
        if path.is_file()
    ).lower()
    for forbidden in (
        "celery",
        "kubernetes",
        "langchain",
        "docker volume export",
        "custom_color_data.tar",
        "vehicle_data",
        "result_data",
    ):
        assert forbidden not in operational_text
