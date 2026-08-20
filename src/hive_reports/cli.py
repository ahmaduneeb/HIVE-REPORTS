"""CLI: generate, serve, watch, demo. That's all."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .api import serve
from .calc import D
from .parse import from_csv, from_json, render
from .store import Store
from .watch import watch_folder


def cmd_generate(args: argparse.Namespace) -> int:
    inp = Path(args.input)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    template = None
    if args.template_file:
        import json
        template = json.loads(Path(args.template_file).read_text())

    store = Store(args.db)

    if inp.suffix.lower() == ".csv":
        transactions = from_csv(inp)
        for idx, tx in enumerate(transactions, start=1):
            target = out if len(transactions) == 1 else out.with_name(f"{out.stem}-{idx}{out.suffix}")
            path = render(tx, args.format, target, template)
            store.save_transaction(
                payload=tx.to_dict(),
                template=(template or {}).get("name", "default"),
                output_path=str(path),
                total=str(tx.total()),
            )
            store.log("generate", str(path))
            print(f"wrote {path}")
    else:
        tx = from_json(inp.read_text())
        path = render(tx, args.format, out, template)
        store.save_transaction(
            payload=tx.to_dict(),
            template=(template or {}).get("name", "default"),
            output_path=str(path),
            total=str(tx.total()),
        )
        store.log("generate", str(path))
        print(f"wrote {path}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    serve(host=args.host, port=args.port, db=args.db, out=args.out)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    watch_folder(args.input, args.out, args.archive, args.db)
    return 0


def cmd_demo(_args: argparse.Namespace) -> int:
    """Self-check: render one receipt with known totals, verify they match."""
    from .calc import LineItem, Transaction, money

    tx = Transaction(
        items=[
            LineItem("Coffee", qty=D("2"), price=D("3.50"), tax_rate=D("0.10")),
            LineItem("Bagel",  qty=D("1"), price=D("4.00"), tax_rate=D("0.10")),
        ],
        discount=D("1.00"),
        currency="USD",
        notes="Self-check receipt.",
    )
    # Coffee 7.00 + Bagel 4.00 = 11.00 subtotal; -1.00 discount; +1.10 tax (10% of 11.00) = 11.10
    assert tx.subtotal() == money(D("11.00")), tx.subtotal()
    assert tx.tax_total() == money(D("1.10")), tx.tax_total()
    assert tx.total() == money(D("11.10")), tx.total()

    out = render(tx, "pdf", "demo-receipt.pdf")
    print(f"demo ok: {out} (total {tx.total()} {tx.currency})")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    p = argparse.ArgumentParser(prog="receipt-gen", description="Receipt & report generator.")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="One-off render from JSON/CSV.")
    g.add_argument("--input", "-i", required=True)
    g.add_argument("--output", "-o", required=True)
    g.add_argument("--format", "-f", choices=["pdf", "png", "json"], default="pdf")
    g.add_argument("--template-file")
    g.add_argument("--db", default="hive.db")
    g.set_defaults(func=cmd_generate)

    s = sub.add_parser("serve", help="Run the REST API.")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--db", default="hive.db")
    s.add_argument("--out", default="out")
    s.set_defaults(func=cmd_serve)

    w = sub.add_parser("watch", help="Watch a folder for JSON/CSV drops.")
    w.add_argument("--input", default="input")
    w.add_argument("--out", default="out")
    w.add_argument("--archive", default="processed")
    w.add_argument("--db", default="hive.db")
    w.set_defaults(func=cmd_watch)

    d = sub.add_parser("demo", help="Render a known-good receipt and verify totals.")
    d.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())