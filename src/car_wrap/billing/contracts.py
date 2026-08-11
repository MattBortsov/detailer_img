"""Closed, scalar contracts for the billing domain."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProductId(StrEnum):
    INTRO_25 = "intro_25"
    PACK_5 = "pack_5"
    PACK_15 = "pack_15"
    PACK_40 = "pack_40"
    PLUS = "plus"
    STUDIO = "studio"
    ULTIMA = "ultima"


class ProductKind(StrEnum):
    INTRO = "intro"
    PACKAGE = "package"
    MONTHLY = "monthly"
    LEAD = "lead"


class AllowanceKind(StrEnum):
    FREE = "free"
    INTRO = "intro"
    PACKAGE = "package"
    BONUS = "bonus"
    MONTHLY = "monthly"


class LedgerEntryKind(StrEnum):
    GRANT = "grant"
    RESERVE = "reserve"
    CONSUME = "consume"
    RELEASE = "release"
    EXPIRE = "expire"


@dataclass(frozen=True, slots=True)
class Product:
    """A server-owned offer; callback data can only name this identifier."""

    id: ProductId
    kind: ProductKind
    amount_kopecks: int | None
    allowance: int | None
    currency: str = "RUB"

    @property
    def is_payable(self) -> bool:
        return self.amount_kopecks is not None
