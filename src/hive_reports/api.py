"""REST API. One blueprint, three endpoints, nothing else."""
from __future__ import annotations
from flask import Flask, jsonify, request

from .parse import from_json, render, new_receipt_id
from .store import Store
from .templates import (
    list_registered_templates,
    resolve_template,
)
from .thermal import print_to_printer


def create_app(store: Store | None = None, output_dir: str = "out") -> Flask:
    store = store or Store()
    app = Flask(__name__)
    app.config["STORE"] = store
    app.config["OUT_DIR"] = output_dir

    @app.post("/api/generate-receipt")
    def generate():
        payload = request.get_json(force=True, silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400

        template_name = payload.get("template_name")
        try:
            template = resolve_template(template_name, store=store) if template_name else payload.get("template")
        except KeyError as e:
            return jsonify({"error": str(e)}), 400

        fmt = payload.get("format", "pdf")
        rid = payload.get("receipt_id") or new_receipt_id()

        try:
            tx = from_json(payload)
        except (ValueError, TypeError, KeyError) as e:
            return jsonify({"error": "invalid payload", "detail": str(e)}), 400

        # Direct-to-printer streaming via Windows winspool (printer name)
        printer_name = payload.get("printer_name")
        if printer_name:
            from .thermal import print_to_winspool
            try:
                bytes_sent = print_to_winspool(
                    tx, printer_name=printer_name,
                    template=template, receipt_id=rid,
                )
            except (OSError, RuntimeError) as e:
                return jsonify({
                    "error": "Could not reach printer",
                    "detail": str(e),
                    "receipt_id": rid,
                    "total": str(tx.total()),
                }), 502
            store.save_transaction(
                payload={"receipt_id": rid, **payload},
                template=_audit_template_name(template_name, payload),
                output_path=None,
                total=str(tx.total()),
                fmt="thermal",
            )
            store.log("print_receipt", f"id={rid} printer={printer_name} bytes={bytes_sent}")
            return jsonify({
                "receipt_id": rid,
                "transaction_id": None,
                "output_path": None,
                "total": str(tx.total()),
                "bytes_sent": bytes_sent,
                "printed": True,
            })

        # Direct-to-printer streaming via network or serial port
        print_to = payload.get("print_to")
        if print_to:
            from .thermal import parse_printer_target
            host, port = parse_printer_target(print_to)
            baud = payload.get("baudrate", 9600)
            try:
                bytes_sent = print_to_printer(
                    tx, host=host, port=port or 9100,
                    template=template, receipt_id=rid, baudrate=baud,
                )
            except (OSError, RuntimeError) as e:
                return jsonify({
                    "error": "Could not reach printer",
                    "detail": str(e),
                    "receipt_id": rid,
                    "total": str(tx.total()),
                }), 502
            store.save_transaction(
                payload={"receipt_id": rid, **payload},
                template=_audit_template_name(template_name, payload),
                output_path=None,
                total=str(tx.total()),
                fmt="thermal",
            )
            store.log("print_receipt", f"id={rid} host={host}:{port} bytes={bytes_sent}")
            return jsonify({
                "receipt_id": rid,
                "transaction_id": None,
                "output_path": None,
                "total": str(tx.total()),
                "bytes_sent": bytes_sent,
                "printed": True,
            })

        out_path = f"{app.config['OUT_DIR']}/{rid}.{fmt}"
        path = render(tx, fmt, out_path, template, rid)
        tx_id = store.save_transaction(
            payload={"receipt_id": rid, **payload},
            template=_audit_template_name(template_name, payload),
            output_path=str(path),
            total=str(tx.total()),
            fmt=fmt,
        )
        store.log("generate_receipt", f"id={tx_id} rid={rid}")
        return jsonify({
            "receipt_id": rid,
            "transaction_id": tx_id,
            "output_path": str(path),
            "total": str(tx.total()),
        })

    @app.get("/api/templates")
    def list_templates_route():
        registered = list_registered_templates()
        saved = store.list_templates()
        return jsonify({"registered": registered, "saved": saved})

    @app.put("/api/templates/<name>")
    def save_template_route(name: str):
        body = request.get_json(force=True, silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400
        import json as _json
        store.upsert_template(name, _json.dumps(body))
        store.log("template_upsert", name)
        return jsonify({"name": name, "saved": True})

    @app.get("/api/transactions")
    def list_tx():
        return jsonify(app.config["STORE"].recent(limit=int(request.args.get("limit", 50))))

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def _audit_template_name(template_name: str | None, payload: dict) -> str:
    """Pick the template name to record in the audit log.

    Priority:
      1. ``template_name`` from the request (explicit lookup).
      2. Filename derived from ``template_file`` in the payload.
      3. ``"default"`` if neither was supplied.
    """
    if template_name:
        return template_name
    return "default"


def serve(host: str = "127.0.0.1", port: int = 8765, db: str = "hive.db", out: str = "out") -> None:
    app = create_app(Store(db), out)
    # ponytail: Flask dev server is fine for local tool. Put gunicorn in front if exposed.
    app.run(host=host, port=port, debug=False, use_reloader=False)