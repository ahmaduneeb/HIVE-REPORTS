"""Tkinter GUI: main window for picking a printer, firing a test print,
and launching the other `receipt-gen` subcommands (generate / serve / watch / demo).

Layout:
    +--------------------------------------------------+
    | Printer: [dropdown]    [Refresh printers]        |
    | Network/serial target (optional): [host:port]   |
    | Baud: [9600]                                     |
    +--------------------------------------------------+
    | Software: [dropdown]   [Run]                     |
    |   - Generate PDF/PNG/JSON/Thermal from file      |
    |   - Start REST API (serve)                       |
    |   - Start Watcher (watch)                        |
    |   - Run Demo                                     |
    +--------------------------------------------------+
    | [Test print] [Open output folder]                |
    +--------------------------------------------------+
    | Log:                                            |
    |   (scrollable text panel)                        |
    +--------------------------------------------------+

Runs the actual subcommands by invoking the existing ``cmd_*`` functions
in ``cli.py`` (no duplicated logic). Long-running commands run in a worker
thread so the UI stays responsive; output streams back via a thread-safe
queue polled by Tk's ``after`` loop.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable

from .calc import D, LineItem, Transaction
from .parse import render
from .store import Store
from .templates import list_registered_templates, resolve_template
from .thermal import print_to_printer, print_to_winspool

# ---- printer enumeration ---------------------------------------------------
try:
    import win32print  # type: ignore
except ImportError:
    win32print = None  # GUI still works on non-Windows for generate/demo.


def list_windows_printers() -> list[str]:
    """Return a list of installed Windows printer display names.

    Falls back to an empty list on non-Windows or when pywin32 isn't installed.
    """
    if win32print is None:
        return []
    try:
        return [p["pPrinterName"] for p in win32print.EnumPrinters(2, None, 2)]
    except Exception as e:  # never let the GUI crash on enumeration
        print(f"[gui] EnumPrinters failed: {e}", file=sys.stderr)
        return []


def default_windows_printer() -> str | None:
    """Return the Windows default printer name, or None if unavailable."""
    if win32print is None:
        return None
    try:
        return win32print.GetDefaultPrinter()
    except Exception:
        return None


# ---- software actions ------------------------------------------------------
# Each action is a ``(label, factory)`` where the factory builds an
# ``argparse.Namespace`` consumable by the existing ``cmd_*`` in cli.py.
# For serve/watch we spawn the real console script in a subprocess so the
# GUI and the long-running service don't fight over the same interpreter.

def _make_generate_args(input_path: str, template_name: str | None) -> argparse.Namespace:
    return argparse.Namespace(
        input=input_path,
        output=str(Path(input_path).with_suffix(".out.pdf")),
        format="pdf",
        template_file=None,
        template_name=template_name,
        db="hive.db",
    )


def _make_print_args(
    input_path: str,
    printer_name: str | None,
    host: str | None,
    port: int,
    baud: int,
    receipt_id: str | None,
    template_name: str | None,
) -> argparse.Namespace:
    return argparse.Namespace(
        input=input_path,
        host=host,
        port=port,
        baud=baud,
        printer_name=printer_name,
        receipt_id=receipt_id,
        template_file=None,
        template_name=template_name,
        db="hive.db",
    )


def _make_templates_list_args() -> argparse.Namespace:
    return argparse.Namespace(db="hive.db")


def _make_templates_set_args(name: str, file_path: str) -> argparse.Namespace:
    return argparse.Namespace(name=name, file=file_path, db="hive.db")


# Long-running actions that need a real subprocess (not the GUI's interpreter).
def _spawn_long_running(self, argv: list[str]) -> None:
    if self._subproc is not None and self._subproc.poll() is None:
        messagebox.showinfo("Already running", "A long-running service is already active.")
        return
    try:
        proc, out_q = _spawn_subprocess(argv)
        self._subproc = proc
        self._subproc_q = out_q
    except Exception as e:
        self._log(f"[gui] failed to spawn: {e}")

def _drain_log_queue(self) -> None:
    # Move anything posted by workers into the log widget.
    try:
        while True:
            line = self._log_q.get_nowait()
            self._append_log(line)
    except queue.Empty:
        pass

    # Drain any active subprocess output too.
    if self._subproc_q is not None:
        try:
            while True:
                line = self._subproc_q.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass

        # Detect exit so we don't keep a stale handle around.
        if self._subproc is not None and self._subproc.poll() is not None:
            self._log(f"[gui] subprocess exited with code {self._subproc.returncode}")
            self._subproc = None
            self._subproc_q = None

    self.root.after(self.POLL_MS, self._drain_log_queue)


# ---- main application ------------------------------------------------------
class HiveReportsApp:
    """Tk root + all widgets + worker-thread plumbing."""

    POLL_MS = 100  # how often to drain the worker queue

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Hive Reports — Printer Console")
        self.root.geometry("780x600")
        self.root.minsize(620, 480)

        # Streams output from worker threads back into the log widget.
        self._log_q: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._subproc: subprocess.Popen | None = None
        self._subproc_q: queue.Queue[str] | None = None

        self._build_widgets()
        self._refresh_printers()

        # Start the queue-poller loop.
        self.root.after(self.POLL_MS, self._drain_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- widget construction ----------------------------------------------
    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # === Printer section =============================================
        printer_frame = ttk.LabelFrame(self.root, text="Printer")
        printer_frame.pack(fill="x", **pad)

        ttk.Label(printer_frame, text="Windows printer:").grid(
            row=0, column=0, sticky="w", padx=6, pady=4
        )
        self.printer_var = tk.StringVar()
        self.printer_combo = ttk.Combobox(
            printer_frame, textvariable=self.printer_var, width=48, state="readonly"
        )
        self.printer_combo.grid(row=0, column=1, sticky="we", padx=6, pady=4)

        ttk.Button(printer_frame, text="Refresh", command=self._refresh_printers).grid(
            row=0, column=2, padx=6, pady=4
        )

        ttk.Label(printer_frame, text="Network/serial target (optional):").grid(
            row=1, column=0, sticky="w", padx=6, pady=2
        )
        self.host_var = tk.StringVar()
        ttk.Entry(printer_frame, textvariable=self.host_var, width=50).grid(
            row=1, column=1, sticky="we", padx=6, pady=2
        )

        ttk.Label(printer_frame, text="Baud:").grid(row=2, column=0, sticky="w", padx=6, pady=2)
        self.baud_var = tk.IntVar(value=9600)
        ttk.Spinbox(
            printer_frame, from_=1200, to=115200, increment=1200,
            textvariable=self.baud_var, width=10,
        ).grid(row=2, column=1, sticky="w", padx=6, pady=2)

        printer_frame.columnconfigure(1, weight=1)

        # === Software section =============================================
        sw_frame = ttk.LabelFrame(self.root, text="Software")
        sw_frame.pack(fill="x", **pad)

        ttk.Label(sw_frame, text="Action:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.sw_var = tk.StringVar(value=self._software_options()[0])
        self.sw_combo = ttk.Combobox(
            sw_frame,
            textvariable=self.sw_var,
            values=self._software_options(),
            state="readonly",
            width=48,
        )
        self.sw_combo.grid(row=0, column=1, sticky="we", padx=6, pady=4)
        self.sw_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_software_change())

        ttk.Label(sw_frame, text="Input file:").grid(row=1, column=0, sticky="w", padx=6, pady=2)
        self.input_var = tk.StringVar()
        input_row = ttk.Frame(sw_frame)
        input_row.grid(row=1, column=1, sticky="we", padx=6, pady=2)
        ttk.Entry(input_row, textvariable=self.input_var).pack(side="left", fill="x", expand=True)
        ttk.Button(input_row, text="Browse...", command=self._pick_input).pack(side="left", padx=4)

        ttk.Label(sw_frame, text="Template:").grid(row=2, column=0, sticky="w", padx=6, pady=2)
        self.template_var = tk.StringVar(value="")
        self.template_combo = ttk.Combobox(
            sw_frame,
            textvariable=self.template_var,
            width=46,
            values=self._template_choices(),
        )
        self.template_combo.grid(row=2, column=1, sticky="we", padx=6, pady=2)

        ttk.Button(sw_frame, text="Run", command=self._run_software).grid(
            row=3, column=1, sticky="e", padx=6, pady=4
        )
        sw_frame.columnconfigure(1, weight=1)

        # === Action bar ===================================================
        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill="x", **pad)

        ttk.Button(
            action_frame, text="Test print", command=self._run_test_print
        ).pack(side="left", padx=4)

        ttk.Button(
            action_frame, text="Open output folder", command=self._open_output
        ).pack(side="left", padx=4)

        ttk.Button(
            action_frame, text="Clear log", command=self._clear_log
        ).pack(side="right", padx=4)

        # === Log panel ====================================================
        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_widget = scrolledtext.ScrolledText(
            log_frame, wrap="word", height=12, state="disabled", font=("Consolas", 9)
        )
        self.log_widget.pack(fill="both", expand=True, padx=4, pady=4)

    def _software_options(self) -> list[str]:
        return [
            "Generate PDF",
            "Generate PNG",
            "Generate JSON",
            "Generate Thermal (file)",
            "Start REST API (serve)",
            "Start Watcher (watch)",
            "Run Demo",
            "Templates: list",
            "Templates: set <name> --file ...",
        ]

    def _template_choices(self) -> list[str]:
        # Registered + DB-saved + a blank "" entry for "no template".
        try:
            reg = list_registered_templates()
            saved = Store("hive.db").list_templates()
        except Exception:
            reg, saved = [], []
        all_names = sorted(set(reg) | set(saved))
        return [""] + all_names

    # -- actions ----------------------------------------------------------
    def _refresh_printers(self) -> None:
        names = list_windows_printers()
        self.printer_combo["values"] = names
        if names:
            current = self.printer_var.get()
            default = default_windows_printer()
            # Prefer: current selection (if still valid) > system default > first.
            if current and current in names:
                pass
            elif default and default in names:
                self.printer_var.set(default)
            else:
                self.printer_var.set(names[0])
            self._log(f"[gui] {len(names)} printer(s) found")
        else:
            self.printer_var.set("")
            self._log("[gui] no printers found (win32print unavailable?)")

    def _on_software_change(self) -> None:
        # Templates: set requires a separate "name" entry; reset the input row.
        if self.sw_var.get().startswith("Templates: set"):
            self.input_var.set("")

    def _pick_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose input file",
            filetypes=[("JSON / CSV", "*.json *.csv"), ("All files", "*.*")],
        )
        if path:
            self.input_var.set(path)

    def _open_output(self) -> None:
        out = Path("out")
        out.mkdir(exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(out)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(out)])
            else:
                subprocess.Popen(["xdg-open", str(out)])
        except Exception as e:
            self._log(f"[gui] could not open output folder: {e}")

    def _clear_log(self) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

    # -- test print -------------------------------------------------------
    def _run_test_print(self) -> None:
        """Build a tiny in-memory transaction and run cmd_print with it.

        Avoids needing an input file and gives the user a quick sanity check
        that the selected printer actually receives ESC/POS bytes.
        """
        from .cli import cmd_print

        tx = Transaction(
            items=[
                LineItem("Test Item", qty=D("1"), price=D("0.01"), tax_rate=D("0")),
            ],
            discount=D("0"),
            currency="USD",
            notes="Hive Reports test print.",
        )
        # Build a JSON file in-memory via a temp path the user can ignore.
        tmp = Path("out/test-print.json")
        tmp.parent.mkdir(exist_ok=True)
        tmp.write_text(json.dumps({
            "items": [{"name": "Test Item", "qty": "1", "price": "0.01", "tax_rate": "0"}],
            "currency": "USD",
            "notes": "Hive Reports test print.",
        }))

        printer_name = self.printer_var.get().strip() or None
        host = self.host_var.get().strip() or None

        if not printer_name and not host:
            messagebox.showwarning(
                "No printer selected",
                "Pick a Windows printer from the dropdown, or enter a "
                "network/serial target, then try again.",
            )
            return

        args = _make_print_args(
            input_path=str(tmp),
            printer_name=printer_name,
            host=host,
            port=9100,
            baud=int(self.baud_var.get()),
            receipt_id=None,
            template_name=self.template_var.get().strip() or None,
        )

        self._log(f"[gui] test print -> printer={printer_name!r} host={host!r}")
        self._run_in_thread(lambda: cmd_print(args))

    # -- software runner --------------------------------------------------
    def _run_software(self) -> None:
        choice = self.sw_var.get()
        input_path = self.input_var.get().strip()
        tpl_name = self.template_var.get().strip() or None
        printer_name = self.printer_var.get().strip() or None
        host = self.host_var.get().strip() or None

        # --- Generate sub-actions ---
        if choice.startswith("Generate"):
            if not input_path:
                messagebox.showwarning("No input", "Pick a JSON or CSV input file first.")
                return
            if not Path(input_path).exists():
                messagebox.showerror("Missing file", f"{input_path} does not exist.")
                return
            from .cli import cmd_generate
            fmt_map = {
                "Generate PDF": "pdf",
                "Generate PNG": "png",
                "Generate JSON": "json",
                "Generate Thermal (file)": "thermal",
            }
            fmt = fmt_map[choice]
            out_path = str(Path(input_path).with_name(
                Path(input_path).stem + f".out.{fmt if fmt != 'thermal' else 'txt'}"
            ))
            args = argparse.Namespace(
                input=input_path,
                output=out_path,
                format=fmt,
                template_file=None,
                template_name=tpl_name,
                db="hive.db",
            )
            self._log(f"[gui] generate {fmt}: {input_path} -> {out_path}")
            self._run_in_thread(lambda: cmd_generate(args))
            return

        # --- Serve (REST API) — long-running, must spawn a subprocess ---
        if choice == "Start REST API (serve)":
            self._log("[gui] starting REST API on 127.0.0.1:8765 (subprocess)...")
            self._spawn_long_running(["serve", "--host", "127.0.0.1", "--port", "8765"])
            return

        if choice == "Start Watcher (watch)":
            self._log("[gui] starting watcher on ./input (subprocess)...")
            Path("input").mkdir(exist_ok=True)
            self._spawn_long_running(["watch"])
            return

        if choice == "Run Demo":
            from .cli import cmd_demo
            self._log("[gui] running demo self-check...")
            self._run_in_thread(lambda: cmd_demo(argparse.Namespace()))
            return

        if choice == "Templates: list":
            from .cli import cmd_templates_list
            self._run_in_thread(lambda: cmd_templates_list(_make_templates_list_args()))
            return

        if choice.startswith("Templates: set"):
            # Ask for the name + file via dialogs.
            name = _simple_prompt(self.root, "Template name", "Name to save under:")
            if not name:
                return
            file_path = filedialog.askopenfilename(
                title="Template JSON file",
                filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            )
            if not file_path:
                return
            from .cli import cmd_templates_set
            self._run_in_thread(lambda: cmd_templates_set(_make_templates_set_args(name, file_path)))
            return

    # -- worker plumbing --------------------------------------------------
    def _run_in_thread(self, fn: Callable[[], int]) -> None:
        def _wrapper():
            class QueueWriter:
                def __init__(self, q: queue.Queue[str]):
                    self.q = q
                def write(self, s: str):
                    if s:
                        self.q.put(s)
                def flush(self):
                    pass

            qw = QueueWriter(self._log_q)
            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = qw, qw
            try:
                rc = fn()
                self._log_q.put(f"\n[gui] done (rc={rc})\n")
            except Exception as e:
                self._log_q.put(f"\n[gui] ERROR: {e}\n")
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr

        t = threading.Thread(target=_wrapper, daemon=True)
        t.start()
        self._worker = t

    def _spawn_long_running(self, argv: list[str]) -> None:
        if self._subproc is not None and self._subproc.poll() is None:
            messagebox.showinfo("Already running", "A long-running service is already active.")
            return
        try:
            proc, _reader = _spawn_subprocess(argv)
            self._subproc = proc
        except Exception as e:
            self._log(f"[gui] failed to spawn: {e}")

    def _drain_log_queue(self) -> None:
        # Move anything posted by workers into the log widget.
        try:
            while True:
                line = self._log_q.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass
        self.root.after(self.POLL_MS, self._drain_log_queue)

    def _log(self, msg: str) -> None:
        self._append_log(msg + "\n")

    def _append_log(self, msg: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", msg)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _on_close(self) -> None:
        if self._subproc is not None and self._subproc.poll() is None:
            try:
                self._subproc.terminate()
            except Exception:
                pass
        self.root.destroy()


def _simple_prompt(parent: tk.Tk, title: str, prompt: str) -> str | None:
    """Tiny modal single-field prompt (tkinter.simpledialog without the import noise)."""
    from tkinter import simpledialog  # local to avoid pulling at module import time
    return simpledialog.askstring(title, prompt, parent=parent)


def main() -> int:
    root = tk.Tk()
    HiveReportsApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
