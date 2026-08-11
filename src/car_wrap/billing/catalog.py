"""Immutable RUB product catalog used as the sole price authority."""

from __future__ import annotations

from types import MappingProxyType

from car_wrap.billing.contracts import Product, ProductId, ProductKind


class ProductLookupError(ValueError):
    """A callback attempted to use an unknown or non-payable product."""


_PRODUCTS = MappingProxyType(
    {
        ProductId.INTRO_25: Product(ProductId.INTRO_25, ProductKind.INTRO, 2500, 1),
        ProductId.PACK_5: Product(ProductId.PACK_5, ProductKind.PACKAGE, 14900, 5),
        ProductId.PACK_15: Product(ProductId.PACK_15, ProductKind.PACKAGE, 34900, 15),
        ProductId.PACK_40: Product(ProductId.PACK_40, ProductKind.PACKAGE, 74900, 40),
        ProductId.PLUS: Product(ProductId.PLUS, ProductKind.MONTHLY, 49900, 30),
        ProductId.STUDIO: Product(ProductId.STUDIO, ProductKind.MONTHLY, 149900, 100),
        ProductId.ULTIMA: Product(ProductId.ULTIMA, ProductKind.LEAD, None, None),
    }
)


def get_product(product_id: str | ProductId) -> Product:
    """Resolve a product without accepting caller-supplied commercial terms."""

    try:
        identifier = ProductId(product_id)
    except ValueError as error:
        raise ProductLookupError("unknown billing product") from error
    return _PRODUCTS[identifier]


def get_payable_product(product_id: str | ProductId) -> Product:
    """Return a payable catalog offer, rejecting the manager-contact lead."""

    product = get_product(product_id)
    if not product.is_payable:
        raise ProductLookupError("billing product is not payable")
    return product
