"""The production T-Bank boundary must remain deliberately fail-closed."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import yaml

from car_wrap.billing.tbank import (
    PaymentActivationDenied,
    TBankClient,
    phase1_payment_report_passes,
)
from car_wrap.config import AppSettings
from car_wrap.eval.gate import evaluate_gate
from car_wrap.eval.manifest import load_manifest
from car_wrap.eval.models import CaseScores, GateThresholds, ProviderUsage, ScoredCase
from car_wrap.eval.report import build_report, write_report
from car_wrap.eval.run_manifest import (
    GenerationCaseAttempt,
    GenerationRun,
    SafeOutcome,
    validate_evidence_binding,
)

ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def _settings(**overrides: object) -> AppSettings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://user:pass@db/test",
        "bot_token": "bot-token",
        "bot_username": "CarWrapBot",
        "mini_app_url": "https://wrap.example.com/app",
        "tbank_terminal_key": "terminal-canary",
        "tbank_password": "password-canary",
        "tbank_notification_url": "https://wrap.example.com/api/v1/payments/tbank/webhook",
        "tbank_success_url": "https://wrap.example.com/app/payment/success",
        "tbank_fail_url": "https://wrap.example.com/app/payment/fail",
    }
    values.update(overrides)
    return AppSettings.model_validate(values)


def _write_exact_passing_report(destination: Path) -> None:
    """Build real bound Phase 1 evidence instead of a hand-written pass JSON."""

    manifest = load_manifest(ROOT / "eval/corpus.example.yaml")
    attempts: list[GenerationCaseAttempt] = []
    scores: list[ScoredCase] = []
    for index, case in enumerate(manifest.cases, start=1):
        output_sha256 = hashlib.sha256(f"{case.case_id}-output".encode()).hexdigest()
        attempts.append(
            GenerationCaseAttempt(
                case_id=case.case_id,
                source_sha256=case.source_sha256,
                attempt=1,
                model="openai/gpt-image-2",
                prompt_revision="recolor-v1",
                started_at=NOW,
                finished_at=NOW,
                latency_ms=index,
                output_bytes=4096,
                output_sha256=output_sha256,
                usage=ProviderUsage(input_tokens=1, output_tokens=2, total_tokens=3),
                cost=Decimal("0.05"),
                outcome=SafeOutcome(status="succeeded"),
            )
        )
        scores.append(
            ScoredCase(
                case_id=case.case_id,
                source_sha256=case.source_sha256,
                output_sha256=output_sha256,
                scores=CaseScores.model_validate(
                    dict.fromkeys(CaseScores.model_fields, 4)
                ),
            )
        )
    run = GenerationRun(
        schema_version="1",
        run_id="payment-activation-fixture",
        model="openai/gpt-image-2",
        prompt_revision="recolor-v1",
        attempts=tuple(attempts),
    )
    binding = validate_evidence_binding(manifest, run, scores)
    thresholds = GateThresholds.model_validate(
        yaml.safe_load((ROOT / "eval/thresholds.yaml").read_text())
    )
    gate = evaluate_gate(manifest, binding, thresholds)
    write_report(destination, build_report(manifest, binding, thresholds, gate))


def _client(settings: AppSettings) -> TBankClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Init"):
            return httpx.Response(
                200,
                json={
                    "Success": True,
                    "PaymentId": "payment-1",
                    "PaymentURL": "https://pay.example/checkout",
                },
            )
        return httpx.Response(200, json={"Success": True, "PaymentId": "payment-1"})

    return TBankClient(settings, transport=httpx.MockTransport(handler))


async def _assert_movement_denied(client: TBankClient) -> None:
    with pytest.raises(PaymentActivationDenied):
        await client.init_payment(
            order_id="order-1",
            amount_kopecks=2500,
            description="Car Wrap generation",
            customer_key="1001",
            recurrent=False,
        )
    with pytest.raises(PaymentActivationDenied):
        await client.charge(payment_id="payment-1", rebill_id="rebill-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"payments_production_enabled": True},
        {"payments_owner_approved": True},
    ],
)
async def test_each_explicit_control_independently_blocks_init_and_charge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    report = tmp_path / "eval/reports/phase-01.json"
    report.parent.mkdir(parents=True)
    _write_exact_passing_report(report)
    monkeypatch.chdir(tmp_path)

    await _assert_movement_denied(_client(_settings(**overrides)))


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [None, "not-json", '{"verdict":"pass"}'])
async def test_missing_malformed_or_incomplete_report_blocks_money_movement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contents: str | None,
) -> None:
    report = tmp_path / "eval/reports/phase-01.json"
    report.parent.mkdir(parents=True)
    if contents is not None:
        report.write_text(contents)
    monkeypatch.chdir(tmp_path)

    await _assert_movement_denied(
        _client(
            _settings(
                payments_production_enabled=True,
                payments_owner_approved=True,
            )
        )
    )


@pytest.mark.asyncio
async def test_fail_or_noncanonical_report_path_blocks_movement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "eval/reports/phase-01.json"
    report.parent.mkdir(parents=True)
    _write_exact_passing_report(report)
    report.write_text(
        report.read_text().replace('"verdict":"pass"', '"verdict":"fail"')
    )
    monkeypatch.chdir(tmp_path)

    assert not phase1_payment_report_passes(Path("elsewhere/phase-01.json"))
    await _assert_movement_denied(
        _client(
            _settings(
                payments_production_enabled=True,
                payments_owner_approved=True,
            )
        )
    )


@pytest.mark.asyncio
async def test_only_two_true_controls_and_exact_passing_evidence_enable_movement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "eval/reports/phase-01.json"
    report.parent.mkdir(parents=True)
    _write_exact_passing_report(report)
    monkeypatch.chdir(tmp_path)
    client = _client(
        _settings(payments_production_enabled=True, payments_owner_approved=True)
    )

    initialized = await client.init_payment(
        order_id="order-1",
        amount_kopecks=2500,
        description="Car Wrap generation",
        customer_key="1001",
        recurrent=False,
    )
    charged = await client.charge(
        payment_id=initialized.payment_id,
        rebill_id="rebill-1",
    )

    assert initialized.payment_url == "https://pay.example/checkout"
    assert charged.payment_id == "payment-1"
