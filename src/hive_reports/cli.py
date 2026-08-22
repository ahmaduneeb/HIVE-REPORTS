"""CLI: generate, serve, watch, demo. That's all."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from .api import serve
from .calc import D, LineItem, Transaction
from .parse import from_csv, from_json, render
from .store import Store
from .thermal import print_to_printer, print_to_winspool
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
                fmt=args.format,
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
            fmt=args.format,
        )
        store.log("generate", str(path))
        print(f"wrote {path}")
    return 0


def cmd_print(args: argparse.Namespace) -> int:
    """Parse a receipt and stream ESC/POS bytes to a thermal printer.

    Supports:
      * Network printers: --host IP --port PORT (default 9100)
      * Serial/COM printers: --host COM3 --baud 9600
      * Windows USB printers with drivers: --printer-name "Printer Name"
    """
    inp = Path(args.input)
    store = Store(args.db)
    template = None
    if args.template_file:
        import json
        template = json.loads(Path(args.template_file).read_text())

    if inp.suffix.lower() == ".csv":
        txs = from_csv(inp)
    else:
        txs = [from_json(inp.read_text())]

    if args.printer_name:
        target_desc = args.printer_name
        for tx in txs:
            try:
                n = print_to_winspool(
                    tx,
                    printer_name=args.printer_name,
                    template=template,
                    receipt_id=args.receipt_id,
                )
            except RuntimeError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            store.save_transaction(
                payload=tx.to_dict(),
                template=(template or {}).get("name", "default"),
                output_path=None,
                total=str(tx.total()),
                fmt="thermal",
            )
            store.log("print", f"printer={target_desc} bytes={n}")
            print(f"printed {n} bytes to {target_desc} (total {tx.total()} {tx.currency})")
        return 0

    port = args.port
    baud = args.baud
    target_desc = args.host

    for tx in txs:
        try:
            n = print_to_printer(
                tx,
                host=args.host,
                port=port,
                template=template,
                receipt_id=args.receipt_id,
                baudrate=baud,
            )
        except OSError as e:
            print(f"ERROR: could not reach printer at {target_desc}: {e}", file=sys.stderr)
            print("      Check connection, IP/port, or that the device path is correct.", file=sys.stderr)
            return 1
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        store.save_transaction(
            payload=tx.to_dict(),
            template=(template or {}).get("name", "default"),
            output_path=None,
            total=str(tx.total()),
            fmt="thermal",
        )
        store.log("print", f"target={target_desc} bytes={n}")
        print(f"printed {n} bytes to {target_desc} (total {tx.total()} {tx.currency})")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    serve(host=args.host, port=args.port, db=args.db, out=args.out)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    watch_folder(args.input, args.out, args.archive, args.db, args.format)
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
    g.add_argument("--format", "-f", choices=["pdf", "png", "json", "thermal"], default="pdf")
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
    w.add_argument("--format", "-f", choices=["pdf", "png", "json", "thermal"], default="pdf")
    w.set_defaults(func=cmd_watch)

    d = sub.add_parser("demo", help="Render a known-good receipt and verify totals.")
    d.set_defaults(func=cmd_demo)

    p2 = sub.add_parser("print", help="Send a receipt to a thermal printer.")
    p2.add_argument("--input", "-i", required=True, help="JSON or CSV input file")
    p2.add_argument("--host", "-t", default=None,
                    help="Printer target: network host, host:port, or device path "
                         r"(Windows: \\.\USB002, COM3; Unix: /dev/usb/lp0)")
    p2.add_argument("--port", type=int, default=9100,
                    help="TCP port for network printers (default 9100)")
    p2.add_argument("--baud", type=int, default=9600,
                    help="Baud rate for serial/COM printers (default 9600)")
    p2.add_argument("--printer-name", "-p", default=None,
                    help="Windows printer name for winspool RAW printing "
                         "(e.g. 'Black Copper BC-85AC(copy of 1)')")
    p2.add_argument("--receipt-id")
    p2.add_argument("--template-file")
    p2.add_argument("--db", default="hive.db")
    p2.set_defaults(func=cmd_print)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())