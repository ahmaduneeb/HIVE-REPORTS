# HIVE-REPORTS

Receipt and report generator. CLI + REST + folder-watch. One default template, one PDF renderer. No GUI.

## Install

```bash
pip install -e .
# for PNG export:
pip install -e ".[png]"
```

## Quick start

```bash
# Self-check (renders one PDF, verifies totals)
receipt-gen demo

# Render one receipt from JSON
receipt-gen generate -i sample.json -o receipt.pdf

# Batch from CSV (rows sharing `group_id` collapse into one transaction)
receipt-gen generate -i sales.csv -o receipt.pdf

# REST API on http://127.0.0.1:8765
receipt-gen serve

# Folder watch: drop *.json or *.csv into ./input, PDFs appear in ./out
receipt-gen watch
```

## Input shape

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
POST /api/generate-receipt  body = the input JSON above + "format": "pdf|json|png"
GET  /api/transactions?limit=50
GET  /health
```

## Storage

SQLite at `./hive.db` (override with `--db`). Tables: `transactions`, `templates`, `audit`.

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