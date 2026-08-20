"""REST API. One blueprint, three endpoints, nothing else."""
from __future__ import annotations
from flask import Flask, jsonify, request

from .parse import from_json, render, new_receipt_id
from .store import Store


def create_app(store: Store | None = None, output_dir: str = "out") -> Flask:
    store = store or Store()
    app = Flask(__name__)
    app.config["STORE"] = store
    app.config["OUT_DIR"] = output_dir

    @app.post("/api/generate-receipt")
    def generate():
        payload = request.get_json(force=True)
        fmt = payload.get("format", "pdf")
        template = payload.get("template")
        rid = payload.get("receipt_id") or new_receipt_id()
        tx = from_json(payload)
        out_path = f"{app.config['OUT_DIR']}/{rid}.{fmt}"
        path = render(tx, fmt, out_path, template, rid)
        tx_id = store.save_transaction(
            payload={"receipt_id": rid, **payload},
            template=(template or {}).get("name", "default"),
            output_path=str(path),
            total=str(tx.total()),
        )
        store.log("generate_receipt", f"id={tx_id} rid={rid}")
        return jsonify({
            "receipt_id": rid,
            "transaction_id": tx_id,
            "output_path": str(path),
            "total": str(tx.total()),
        })

    @app.get("/api/transactions")
    def list_tx():
        return jsonify(app.config["STORE"].recent(limit=int(request.args.get("limit", 50))))

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def serve(host: str = "127.0.0.1", port: int = 8765, db: str = "hive.db", out: str = "out") -> None:
    app = create_app(Store(db), out)
    # ponytail: Flask dev server is fine for local tool. Put gunicorn in front if exposed.
    app.run(host=host, port=port, debug=False, use_reloader=False)