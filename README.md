# HIVE-REPORTS

Receipt and report generator. CLI + GUI + REST + folder-watch. PDF, PNG, JSON, and advanced ESC/POS thermal output.

## Install

```bash
pip install -e .
# for PNG export:
pip install -e ".[png]"
# for Windows printer driver support (winspool):
pip install -e ".[windows_printer]"
```

## GUI Console

Launch the desktop GUI to pick printers, trigger test prints, or run software background actions:

- **Double-click `run_gui.bat`** on Windows (launches in background without leaving an open CMD window).
- Or run `receipt-gui` / `python -m hive_reports.gui` in your terminal.

Features:
- **Printer Selector**: Dropdown listing installed Windows printers (e.g. `Black Copper BC-85AC`) and default system printer, with optional network (`192.168.x.x:9100`) or serial (`COM3`) host target inputs.
- **1-Click Test Print**: Generates an in-memory sample transaction and sends native ESC/POS thermal bytes directly to the printer.
- **Software Runner**: Dropdown to launch PDF/PNG/JSON/Thermal generation, start REST server, run file watcher, or run self-check demo.
- **Live Log**: Scrollable text panel streaming stdout/stderr outputs and process status in real-time.

## Quick start

```bash
# Self-check (renders one PDF, verifies totals)
receipt-gen demo

# Render one receipt from JSON to PDF
receipt-gen generate -i sample.json -o receipt.pdf

# Batch from CSV (rows sharing `group_id` collapse into one transaction)
receipt-gen generate -i sales.csv -o receipt.pdf

# REST API on http://127.0.0.1:8765
receipt-gen serve

# Folder watch: drop *.json or *.csv into ./input, PDFs appear in ./out
receipt-gen watch
```

## Thermal receipt printing

Generates rich 80mm & 58mm POS thermal invoices with native ESC/POS commands:
- Large centered store headers & sub-headers (`MAKKI OIL STORE` style).
- Inverted highlight title banners (`CASH SALE INVOICE`, `NET PAYABLE`).
- Structured itemized tables with wrapped item names, `Qty`, `Price`, `GST %`, and line `Amount` alignment.
- Hardware QR code generation (`GS ( k`) for FBR POS verification / invoice lookup.

```bash
# Write raw ESC/POS bytes to a file
receipt-gen generate -i sample.json -o receipt.txt -f thermal

# Send directly to a Windows printer driver (e.g. Black Copper BC-85AC)
receipt-gen print -i sample.json --printer-name "Black Copper BC-85AC"

# Send directly to a network printer on port 9100
receipt-gen print -i sample.json --host 192.168.1.100 --port 9100

# Watch folder and emit thermal bytes instead of PDFs
receipt-gen watch -f thermal
```

Printer connection via REST:

```bash
curl -X POST http://127.0.0.1:8765/api/generate-receipt \
  -H "Content-Type: application/json" \
  -d '{\"items\":[{\"name\":\"Coffee\",\"qty\":2,\"price\":\"3.50\",\"tax_rate\":\"0.10\"}],
       \"currency\":\"USD\",\"format\":\"thermal\"}'

# Direct-to-printer streaming (no file saved):
curl -X POST http://127.0.0.1:8765/api/generate-receipt \
  -H "Content-Type: application/json" \
  -d '{\"items\":[{\"name\":\"Coffee\",\"qty\":2,\"price\":\"3.50\",\"tax_rate\":\"0.10\"}],
       \"currency\":\"USD\",\"print_to\":\"192.168.1.100:9100\"}'
```

`print_to` format is `"host:port"` (port defaults to 9100). The receipt data is built
and validated via the same pipeline as PDFs; only the output differs.

## Input shape

In addition to `pdf`, `png`, and `json`, the REST API also accepts `format: "thermal"`.
Set `print_to` instead to stream ESC/POS bytes directly to a network printer and skip
the file entirely.

```json
{
  "items": [
    {"name": "Coffee", "qty": 2, "price": "3.50", "tax_rate": "0.10"},
    {"name": "Bagel",  "qty": 1, "price": "4.00", "tax_rate": "0.10"}
  ],
  "discount": "1.00",
  "currency": "USD",
  "notes": "Thanks!",
  "template": {"company": "Cafe X", "address": "1 Main St"}
}
```

CSV columns: `name, qty, price, tax_rate, currency, discount, notes, group_id`.

## REST

```
POST /api/generate-receipt  body = the input JSON above + "format": "pdf|json|png|thermal"
                              add "print_to": "host:port" to stream ESC/POS bytes directly
GET  /api/transactions?limit=50
GET  /health
```

## Storage

SQLite at `./hive.db` (override with `--db`). Tables: `transactions`, `templates`, `audit`.
Each transaction row records `format` (`pdf`, `json`, `png`, or `thermal`) so you can filter what was printed vs. saved to disk.

## What this is NOT (yet)

Skipped on purpose. Add when a real user needs it:

- **Desktop GUI** — use the REST API + a thin HTML page or the file-watch folder. Building PyQt/Electron for a CLI-only tool is pure overhead.
- **Drag-and-drop template editor** — templates are JSON, edit them in any text editor.
- **ERP/POS/CRM connectors** — those systems already have outbound HTTP; our `/api/generate-receipt` is the inbound. No need for a per-vendor adapter until someone is paying for it.
- **Plugin system** — `templates/` is JSON, `_templates[name].py` if you need Python. A registry for one plugin is a registry for zero plugins.
- **Authentication / RBAC / encryption-at-rest** — single-user local tool. OS file perms are the perimeter. Add when this leaves your laptop.
- **Auto-update, MSI/DMG/AppImage installers** — `pip install`. When you have paying customers, use PyInstaller + a real channel.
- **Charts/graphs, BI integration, ML categorization, blockchain receipts, mobile companion, multi-language i18n** — speculative. None of this matters until v1 ships and someone asks.

If you actually need any of these, say which one and I'll wire it. Don't build all twelve.