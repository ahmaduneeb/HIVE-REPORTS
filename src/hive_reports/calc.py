"""Money math. Decimal only - floats lie about money."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP


def D(x) -> Decimal:
    return Decimal(str(x))


def money(x: Decimal) -> Decimal:
    """Round to 2dp using banker-safe HALF_UP."""
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class LineItem:
    name: str
    qty: Decimal
    price: Decimal  # unit price
    tax_rate: Decimal = D("0")  # e.g. Decimal("0.20") for 20% VAT

    def line_total(self) -> Decimal:
        return self.qty * self.price


@dataclass
class Transaction:
    items: list[LineItem] = field(default_factory=list)
    discount: Decimal = D("0")  # absolute amount, applied to subtotal
    currency: str = "USD"
    notes: str = ""

    def subtotal(self) -> Decimal:
        return money(sum((i.line_total() for i in self.items), D("0")))

    def tax_total(self) -> Decimal:
        # ponytail: per-line tax then summed. Adequate for flat-rate VAT.
        # For jurisdiction-mixed tax, switch to per-item tax_id buckets.
        return money(sum((i.line_total() * i.tax_rate for i in self.items), D("0")))

    def total(self) -> Decimal:
        return money(self.subtotal() - self.discount + self.tax_total())

    def to_dict(self) -> dict:
        return {
            "items": [
                {
                    "name": i.name,
                    "qty": str(i.qty),
                    "price": str(i.price),
                    "tax_rate": str(i.tax_rate),
                    "line_total": str(money(i.line_total())),
                }
                for i in self.items
            ],
            "subtotal": str(self.subtotal()),
            "discount": str(self.discount),
            "tax_total": str(self.tax_total()),
            "total": str(self.total()),
            "currency": self.currency,
            "notes": self.notes,
        }