"""Narrow signed T-Bank Kassa boundary with a fail-closed release gate."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from car_wrap.config import AppSettings
from car_wrap.eval.report import EvaluationReport

_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)


class PaymentActivationDenied(RuntimeError):
    """Production payment movement is not explicitly authorized."""

    def __init__(self) -> None:
        super().__init__("payment activation is unavailable")


class TBankProtocolError(RuntimeError):
    """A provider reply was not a safe successful payment response."""

    def __init__(self) -> None:
        super().__init__("payment provider request failed")


class TBankRequestNotSent(TBankProtocolError):
    """The request failed before any payment intent could reach T-Bank."""


class TBankInitRejected(TBankProtocolError):
    """T-Bank returned a valid, definitive unsuccessful Init response."""


class TBankOutcomeAmbiguous(TBankProtocolError):
    """The request may have reached T-Bank and must not be retried blindly."""


@dataclass(frozen=True, slots=True)
class TBankInitResult:
    payment_id: str
    payment_url: str | None


@dataclass(frozen=True, slots=True)
class TBankChargeResult:
    payment_id: str


def canonical_token(payload: Mapping[str, Any], password: Any) -> str:
    """Return T-Bank's SHA-256 token over sorted non-nested scalar fields."""

    secret = (
        password.get_secret_value()
        if hasattr(password, "get_secret_value")
        else str(password)
    )
    values = {
        key: value
        for key, value in payload.items()
        if key != "Token" and value is not None and not isinstance(value, (dict, list))
    }
    values["Password"] = secret
    encoded = "".join(
        (
            str(values[key]).lower()
            if isinstance(values[key], bool)
            else str(values[key])
        )
        for key in sorted(values)
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_notification_token(payload: Mapping[str, Any], password: Any) -> bool:
    """Compare webhook token in constant time without logging its payload."""

    if password is None:
        return False
    supplied = payload.get("Token")
    return isinstance(supplied, str) and hmac.compare_digest(
        supplied.lower(), canonical_token(payload, password).lower()
    )


def phase1_payment_report_passes(path: Path) -> bool:
    """Accept only the exact canonical Phase 1 report contract with a pass verdict."""

    if path != Path("eval/reports/phase-01.json"):
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        report = EvaluationReport.model_validate(raw)
    except (OSError, ValueError, TypeError):
        return False
    return (
        report.schema_version == "1"
        and report.verdict == "pass"
        and not report.failed_rules
    )


class TBankClient:
    """Only T-Bank Init and Charge cross this provider boundary."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        production_approved: Callable[[], bool] | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._production_approved = production_approved or self._production_gate

    def _production_gate(self) -> bool:
        return bool(
            self._settings.payments_production_enabled
            and self._settings.payments_owner_approved
            and self._settings.tbank_terminal_key
            and self._settings.tbank_password
            and self._settings.tbank_notification_url
            and self._settings.tbank_success_url
            and self._settings.tbank_fail_url
        )

    @property
    def terminal_key(self) -> str | None:
        """Configured merchant terminal used to bind trusted notifications."""

        return self._settings.tbank_terminal_key

    @property
    def production_available(self) -> bool:
        """Report the same fail-closed predicate used before any money movement."""

        return self._production_approved()

    @property
    def webhook_password(self) -> Any:
        """Keep signature-secret access within the payment boundary."""

        return self._settings.tbank_password

    async def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._production_approved():
            raise PaymentActivationDenied
        password = self._settings.tbank_password
        terminal = self._settings.tbank_terminal_key
        if password is None or terminal is None:
            raise PaymentActivationDenied
        payload["Token"] = canonical_token(payload, password)
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._settings.tbank_api_base_url}/{method}", json=payload
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise TBankRequestNotSent from None
        except (httpx.HTTPError, ValueError):
            raise TBankOutcomeAmbiguous from None
        if not isinstance(data, dict):
            raise TBankOutcomeAmbiguous
        if data.get("Success") is not True:
            raise TBankInitRejected
        return data

    async def init_payment(
        self,
        *,
        order_id: str,
        amount_kopecks: int,
        description: str,
        customer_key: str,
        recurrent: bool,
        operation_initiator_type: str | None = None,
    ) -> TBankInitResult:
        payload: dict[str, Any] = {
            "TerminalKey": self._settings.tbank_terminal_key,
            "Amount": amount_kopecks,
            "OrderId": order_id,
            "Description": description,
            "CustomerKey": customer_key,
            "Currency": "RUB",
            "NotificationURL": self._settings.tbank_notification_url,
            "SuccessURL": self._settings.tbank_success_url,
            "FailURL": self._settings.tbank_fail_url,
            "Recurrent": "Y" if recurrent else "N",
            "Language": "ru",
        }
        if operation_initiator_type is not None:
            payload["OperationInitiatorType"] = operation_initiator_type
        data = await self._post("Init", payload)
        payment_id, payment_url = data.get("PaymentId"), data.get("PaymentURL")
        if not isinstance(payment_id, str) or (
            payment_url is not None and not isinstance(payment_url, str)
        ):
            raise TBankOutcomeAmbiguous
        return TBankInitResult(payment_id=payment_id, payment_url=payment_url)

    async def charge(self, *, payment_id: str, rebill_id: str) -> TBankChargeResult:
        data = await self._post(
            "Charge",
            {
                "TerminalKey": self._settings.tbank_terminal_key,
                "PaymentId": payment_id,
                "RebillId": rebill_id,
            },
        )
        confirmed_id = data.get("PaymentId")
        if not isinstance(confirmed_id, str):
            raise TBankProtocolError
        return TBankChargeResult(payment_id=confirmed_id)
