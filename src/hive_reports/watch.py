"""Watch a folder for *.json or *.csv drops and render them. Drop, get receipt."""
from __future__ import annotations
import shutil
from pathlib import Path

from watchfiles import watch

from .parse import from_csv, from_json, render
from .store import Store


def watch_folder(
    input_dir: str = "input",
    output_dir: str = "out",
    archive_dir: str = "processed",
    db_path: str = "hive.db",
) -> None:
    inp = Path(input_dir); inp.mkdir(exist_ok=True)
    out = Path(output_dir); out.mkdir(exist_ok=True)
    archive = Path(archive_dir); archive.mkdir(exist_ok=True)
    store = Store(db_path)

    print(f"hive-reports: watching {inp.resolve()} -> {out.resolve()}")
    for changes in watch(inp):
        for _change_type, path_str in changes:
            path = Path(path_str)
            if not path.is_file():
                continue
            try:
                if path.suffix.lower() == ".json":
                    tx = from_json(path.read_text())
                    rid = render(tx, "pdf", out / f"{path.stem}.pdf")
                    store.save_transaction(
                        payload=tx.to_dict(),
                        template="default",
                        output_path=str(rid),
                        total=str(tx.total()),
                    )
                elif path.suffix.lower() == ".csv":
                    for idx, tx in enumerate(from_csv(path), start=1):
                        rid = render(tx, "pdf", out / f"{path.stem}-{idx}.pdf")
                        store.save_transaction(
                            payload=tx.to_dict(),
                            template="default",
                            output_path=str(rid),
                            total=str(tx.total()),
                        )
                else:
                    continue
                store.log("watch_processed", path.name)
                print(f"  ok: {path.name}")
            except Exception as e:  # don't let one bad file kill the watcher
                store.log("watch_error", f"{path.name}: {e}")
                print(f"  FAIL: {path.name}: {e}")
            finally:
                shutil.move(str(path), str(archive / path.name))