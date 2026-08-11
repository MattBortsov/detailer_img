"""Server-owned billing products must not trust callback price data."""

import pytest

from car_wrap.billing.catalog import (
    ProductLookupError,
    get_payable_product,
    get_product,
)
from car_wrap.billing.contracts import AllowanceKind, ProductId, ProductKind


@pytest.mark.parametrize(
    ("product_id", "amount_kopecks", "allowance", "kind"),
    (
        (ProductId.INTRO_25, 2500, 1, ProductKind.INTRO),
        (ProductId.PACK_5, 14900, 5, ProductKind.PACKAGE),
        (ProductId.PACK_15, 34900, 15, ProductKind.PACKAGE),
        (ProductId.PACK_40, 74900, 40, ProductKind.PACKAGE),
        (ProductId.PLUS, 49900, 30, ProductKind.MONTHLY),
        (ProductId.STUDIO, 149900, 100, ProductKind.MONTHLY),
    ),
)
def test_payable_products_have_locked_rub_amounts_and_allowances(
    product_id: ProductId,
    amount_kopecks: int,
    allowance: int,
    kind: ProductKind,
) -> None:
    product = get_payable_product(product_id.value)

    assert product.id is product_id
    assert product.amount_kopecks == amount_kopecks
    assert product.allowance == allowance
    assert product.currency == "RUB"
    assert product.kind is kind


def test_ultima_is_a_non_payable_manager_lead() -> None:
    product = get_product(ProductId.ULTIMA.value)

    assert product.kind is ProductKind.LEAD
    assert product.amount_kopecks is None
    assert product.allowance is None
    with pytest.raises(ProductLookupError, match="not payable"):
        get_payable_product(ProductId.ULTIMA.value)


@pytest.mark.parametrize("value", ("pack_10", "", "intro_25:1", "unknown"))
def test_unknown_product_ids_are_rejected(value: str) -> None:
    with pytest.raises(ProductLookupError, match="unknown"):
        get_product(value)


def test_allowance_categories_keep_bonus_and_monthly_auditable() -> None:
    assert {
        AllowanceKind.FREE,
        AllowanceKind.INTRO,
        AllowanceKind.PACKAGE,
        AllowanceKind.BONUS,
        AllowanceKind.MONTHLY,
    } == set(AllowanceKind)
