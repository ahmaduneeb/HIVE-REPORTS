r"""ESC/POS thermal receipt renderer.

Produces raw bytes that 80mm thermal receipt printers understand (Epson ESC/POS
dialect, also works on Star/Mechanical/etc. with minor variant support).

Two deployment styles:
  * ``receipt-gen print --host HOST``  — stream bytes to a network printer
    (TCP port 9100) or a USB/COM device path (Windows ``\\\\.\\USB002`` or
    ``COM3``; macOS/Linux ``/dev/usb/...``).
  * ``receipt-gen generate --format thermal -o receipt.txt`` — write the
    ESC/POS byte stream to a file.

The module only depends on the stdlib. The optional ``python-escpos`` and
``pyserial`` packages are NOT required for USB device paths on Windows.
"""
from __future__ import annotations

import re
import socket
import sys
from pathlib import Path
from typing import Union

from .calc import Transaction, money

# -- ESC/POS command bytes --------------------------------------------------
ESC = b"\x1B"
GS = b"\x1D"
INIT = ESC + b"@"  # reset printer
FEED = ESC + b"d"  # feed n lines (ESC d n)
CUT = GS + b"V" + b"\x00"  # full cut
ALIGN_LEFT = ESC + b"a" + b"\x00"
ALIGN_CENTER = ESC + b"a" + b"\x01"
BOLD_ON = ESC + b"!" + b"\x10"
BOLD_OFF = ESC + b"!" + b"\x00"
LINE_HEIGHT = ESC + b"3" + b"\x28"  # 38 dots
DEFAULT_LINE_HEIGHT = ESC + b"2"

# 80mm thermal printers wrap at ~40-42 chars; keep one constant for both
# truncation checks AND padding so they never disagree.
LINE_WIDTH = 40

PathLike = Union[str, Path]


def build_receipt(tx: Transaction, receipt_id: str = "", template: dict | None = None) -> bytes:
    """Build an ESC/POS byte stream for ``tx``.

    Returns bytes ready to send to a thermal printer (port 9100 / raw mode).
    """
    t = template or {}
    company = t.get("company", "Hive Reports Inc.")
    address = t.get("address", "")

    lines: list[bytes] = [INIT, LINE_HEIGHT]

    # Header
    lines.append(ALIGN_CENTER)
    lines.append(BOLD_ON)
    lines.append(company.encode("utf-8") + b"\n")
    if address:
        lines.append(address.encode("utf-8") + b"\n")
    if receipt_id:
        lines.append(f"Receipt: {receipt_id}\n".encode("utf-8"))
    lines.append(BOLD_OFF)
    lines.append(ALIGN_LEFT)
    lines.append(b"\n")

    # Items
    for it in tx.items:
        unit = money(it.line_total() / it.qty) if it.qty else money(it.qty)
        # Name + qty on left, price on right, via spaces to fill line width.
        left = f"{it.name} ({it.qty})"
        right = f"{unit} x {it.qty}"
        # Truncate ``left`` if it would overflow LINE_WIDTH. Clamp the slice
        # length at 0 so a pathologically long ``right`` can't make it go
        # negative (which would slice from the END of the string via Python's
        # negative-index wraparound).
        if len(left) + len(right) > LINE_WIDTH:
            left = left[: max(LINE_WIDTH - len(right), 0)]
        gap = LINE_WIDTH - len(left) - len(right)
        if gap < 1:
            gap = 1
        lines.append(f"{left}{' ' * gap}{right}\n".encode("utf-8"))

    lines.append(b"-" * LINE_WIDTH + b"\n")

    # Summary
    def _row(label: str, value: str) -> bytes:
        gap = LINE_WIDTH - len(label) - len(value)
        if gap < 1:
            gap = 1
        return f"{label}{' ' * gap}{value}\n".encode("utf-8")

    lines.append(_row("SUBTOTAL", f"{tx.currency} {tx.subtotal()}"))
    if tx.discount:
        lines.append(_row("DISCOUNT", f"-{tx.currency} {money(tx.discount)}"))
    lines.append(_row("TAX", f"{tx.currency} {tx.tax_total()}"))
    lines.append(BOLD_ON)
    lines.append(_row("TOTAL", f"{tx.currency} {tx.total()}"))
    lines.append(BOLD_OFF)
    lines.append(b"\n")

    if tx.notes:
        lines.append(f"  {tx.notes}\n\n".encode("utf-8"))

    # Footer
    footer = t.get("footer", "Thank you for your business.")
    lines.append(ALIGN_CENTER)
    lines.append(f"{footer}\n".encode("utf-8"))

    # Feed + cut
    lines.append(FEED + b"\x05")  # 5 blank lines
    lines.append(CUT)
    lines.append(DEFAULT_LINE_HEIGHT)

    return b"".join(lines)


def render_thermal(
    tx: Transaction,
    out_path: str | Path,
    template: dict | None = None,
    receipt_id: str = "",
) -> Path:
    """Render a Transaction to a file containing the ESC/POS byte stream."""
    data = build_receipt(tx, receipt_id or "", template)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


def parse_printer_target(target: str) -> tuple[str, int | None]:
    r"""Parse a printer target string into ``(host_or_device, port)``.

    Accepts:
      * ``"192.168.1.100"``              → network, port 9100 default
      * ``"192.168.1.100:9100"``         → network, explicit port
      * ``"\\\\.\\USB002"`` / ``"COM3"``  → USB/COM device path (port=None)
      * ``"/dev/usb/lp0"``               → Unix device path (port=None)
    """
    # Windows device path: \\.\USB002, \\.\COM1, \\.\pipe\..., etc.
    # Also bare "USB002" / "COM3" / "/dev/..." on their respective platforms.
    win_prefix = chr(92) + chr(92) + "." + chr(92)  # \\.\
    if target.startswith(win_prefix) or re.match(r"^USB\d+$", target, re.IGNORECASE):
        return target, None

    # Network host:port
    if re.match(r"^[^:/]+:\d+$", target):
        host, _, port = target.partition(":")
        return host, int(port)

    # Bare network host (IP address) — treat as network with default port
    if re.match(r"^[^\s:/]+$", target) and "." in target:
        return target, 9100

    # Everything else is a device path (COM1, COM3, /dev/...)
    return target, None


def print_to_printer(
    tx: Transaction,
    host: str,
    port: int = 9100,
    template: dict | None = None,
    receipt_id: str = "",
    timeout: float = 10.0,
    baudrate: int = 9600,
) -> int:
    r"""Send the ESC/POS byte stream to a thermal printer.

    *host* can be:

    * ``"192.168.1.50"`` → TCP (uses ``port``, default 9100)
    * ``"192.168.1.50:9100"`` → TCP with explicit port
    * ``"COM3"`` / ``"COM12"`` → Windows serial port (uses ``baudrate``)
    * ``"\\\\.\\USB002"`` / ``"\\\\.\\COM1"`` → Windows USB/COM device path
    * ``"/dev/usb/lp0"`` → Unix device path

    For network targets, ``port`` is the TCP port. For serial/COM targets,
    ``port`` is ignored and ``baudrate`` (default 9600) is used for the serial
    connection.

    Returns the number of bytes written. Raises on I/O error.
    """
    data = build_receipt(tx, receipt_id or "", template)

    # Use the helper to decide network vs device
    resolved_host, resolved_port = parse_printer_target(host)

    # If parse_printer_target returned a port, it's a network target
    if resolved_port is not None:
        return _send_tcp(resolved_host, resolved_port, data, timeout)

    # Otherwise treat host as a device path
    return _send_serial(resolved_host, data, timeout, baudrate=baudrate)


def _send_tcp(host: str, port: int, data: bytes, timeout: float) -> int:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(data)
    return len(data)


def _send_serial(device: str, data: bytes, timeout: float, baudrate: int = 9600) -> int:
    r"""Send raw bytes to a USB or serial printer port.

    On Windows, USB printers exposed as ``\\\\.\\USB002`` can be opened as a
    file in write-binary mode — no extra library needed.
    Genuine serial ports (``COM1``, ``COM3``, ``/dev/ttyUSB0``) require *pyserial*
    for baud-rate / parity configuration. The ``baudrate`` parameter (default 9600)
    is used when pyserial is available.
    """
    if sys.platform == "win32":
        # COM3 → serial (pyserial); \\.\USB002 or \\.\COM1 → raw file open
        win_prefix = chr(92) + chr(92) + "." + chr(92)  # \\.\
        is_usb_device = (
            device.startswith(win_prefix)
            or re.match(r"^USB\d+$", device, re.IGNORECASE) is not None
        )
        if is_usb_device:
            # USB device path (e.g. \\.\USB002) — open as a raw file
            with open(device, "wb") as f:
                f.write(data)
                f.flush()
            return len(data)

        # Bare "COM3" → use pyserial for baud-rate / parity config
        try:
            import serial as serial_mod  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "Serial/COM printer support requires pyserial: "
                "`pip install pyserial`"
            ) from e
        ser = serial_mod.Serial(port=device, baudrate=baudrate, timeout=timeout)
        try:
            ser.write(data)
            ser.flush()
        finally:
            ser.close()
        return len(data)

    # macOS / Linux: /dev/usb/... or /dev/ttyUSB0
    try:
        import serial as serial_mod  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "Serial printer support requires pyserial: `pip install pyserial`"
        ) from e
    ser = serial_mod.Serial(port=device, baudrate=baudrate, timeout=timeout)
    try:
        ser.write(data)
        ser.flush()
    finally:
        ser.close()
    return len(data)


def print_to_winspool(
    tx: Transaction,
    printer_name: str,
    template: dict | None = None,
    receipt_id: str = "",
) -> int:
    """Send ESC/POS bytes to a Windows printer via the winspool API.

    This is the correct path for USB printers that have a Windows driver
    installed (e.g. "Black Copper BC-85AC"). The driver must be configured
    for *RAW* data type so it passes ESC/POS bytes through unmodified.

    On non-Windows platforms, raises RuntimeError.
    """
    if sys.platform != "win32":
        raise RuntimeError("WinSpool printing is only available on Windows")

    import win32print  # type: ignore

    data = build_receipt(tx, receipt_id or "", template)
    attrs = {"pDatatype": "RAW", "pPrintProcessor": "winprint"}
    h = win32print.OpenPrinter(printer_name, attrs)
    try:
        win32print.StartDocPrinter(h, 1, ("receipt", None, "RAW"))
        win32print.StartPagePrinter(h)
        win32print.WritePrinter(h, data)
        win32print.EndPagePrinter(h)
        win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)
    return len(data)
