"""Shared render pipeline. CLI, REST, file-watch all funnel through here."""
from __future__ import annotations
import csv
import json
import secrets
from decimal import Decimal
from pathlib import Path

from .calc import D, LineItem, Transaction
from .pdf_render import render_pdf, render_png
from .thermal import render_thermal, print_to_printer


SUPPORTED_FORMATS = {"pdf", "json", "png", "thermal"}


def new_receipt_id() -> str:
    return "RCT-" + secrets.token_hex(4).upper()


def _build_tx(payload: dict) -> Transaction:
    items = [
        LineItem(
            name=i["name"],
            qty=D(i.get("qty", 1)),
            price=D(i["price"]),
            tax_rate=D(i.get("tax_rate", "0")),
        )
        for i in payload["items"]
    ]
    return Transaction(
        items=items,
        discount=D(payload.get("discount", "0")),
        currency=payload.get("currency", "USD"),
        notes=payload.get("notes", ""),
    )


def from_json(data: str | bytes | dict) -> Transaction:
    if isinstance(data, (str, bytes)):
        return _build_tx(json.loads(data))
    return _build_tx(data)


def from_csv(path: str | Path) -> list[Transaction]:
    """Batch mode: one Transaction per row.
    Expected columns: name, qty, price, tax_rate, [currency, discount, notes, group_id].
    Rows sharing group_id form a single transaction."""
    path = Path(path)
    out: list[Transaction] = []
    groups: dict[str, Transaction] = {}

    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gid = row.get("group_id") or f"g{len(groups) + 1}"
            tx = groups.get(gid)
            if tx is None:
                tx = Transaction(
                    discount=D(row.get("discount", "0")),
                    currency=row.get("currency", "USD"),
                    notes=row.get("notes", ""),
                )
                groups[gid] = tx
                out.append(tx)
            tx.items.append(LineItem(
                name=row["name"],
                qty=D(row.get("qty", "1")),
                price=D(row["price"]),
                tax_rate=D(row.get("tax_rate", "0")),
            ))
    return out


def render(
    tx: Transaction,
    fmt: str,
    out_path: str | Path,
    template: dict | None = None,
    receipt_id: str | None = None,
) -> Path:
    fmt = fmt.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"format {fmt!r} not supported; pick from {SUPPORTED_FORMATS}")
    rid = receipt_id or new_receipt_id()

    if fmt == "pdf":
        return render_pdf(tx, out_path, template, rid)
    if fmt == "png":
        return render_png(tx, out_path, template, rid)
    if fmt == "thermal":
        return render_thermal(tx, out_path, template, rid)
    if fmt == "json":
        out = Path(out_path); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "receipt_id": rid,
            "summary": tx.to_dict(),
            "template": template or {},
        }, indent=2))
        return out
    raise AssertionError("unreachable")