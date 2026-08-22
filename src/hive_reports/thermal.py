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
ALIGN_RIGHT = ESC + b"a" + b"\x02"
BOLD_ON = ESC + b"E" + b"\x01"
BOLD_OFF = ESC + b"E" + b"\x00"
INVERT_ON = GS + b"B" + b"\x01"
INVERT_OFF = GS + b"B" + b"\x00"
DOUBLE_HEIGHT_ON = ESC + b"!" + b"\x10"
DOUBLE_WIDTH_ON = ESC + b"!" + b"\x20"
DOUBLE_SIZE_ON = ESC + b"!" + b"\x30"
NORMAL_SIZE = ESC + b"!" + b"\x00"
LINE_HEIGHT = ESC + b"3" + b"\x28"  # 38 dots
DEFAULT_LINE_HEIGHT = ESC + b"2"

# 80mm thermal printers wrap at ~42 chars; 58mm at ~32 chars
LINE_WIDTH = 42

PathLike = Union[str, Path]


def qr_code_bytes(data: str) -> bytes:
    """Generate native ESC/POS QR code command bytes for 80mm/58mm thermal printers."""
    content = data.encode("utf-8")
    length = len(content) + 3
    pL = length % 256
    pH = length // 256

    commands = [
        ALIGN_CENTER,
        # Set QR model (Model 2)
        GS + b"(k" + b"\x04\x00\x31\x41\x32\x00",
        # Set QR size (Module size 6)
        GS + b"(k" + b"\x03\x00\x31\x43\x06",
        # Set Error Correction Level (Level M = 48)
        GS + b"(k" + b"\x03\x00\x31\x45\x30",
        # Store QR data
        GS + b"(k" + bytes([pL, pH]) + b"\x31\x50\x30" + content,
        # Print QR code symbol
        GS + b"(k" + b"\x03\x00\x31\x51\x30",
        b"\n",
    ]
    return b"".join(commands)


def build_receipt(
    tx: Transaction,
    receipt_id: str = "",
    template: dict | None = None,
) -> bytes:
    """Build an ESC/POS byte stream styled like advanced POS invoices.

    Includes support for:
      - Large centered headers & sub-headers
      - Table layout with Item Name, Qty, Price, Tax/GST %, and Amount columns
      - Inverted / highlight banners (e.g. CASH SALE INVOICE / NET PAYABLE)
      - Standard QR code generation for POS/FBR verification
    """
    t = template or {}
    company = t.get("company", "MAKKI OIL STORE")
    address = t.get("address", "872-D, Faisal Town Near Akbar Chowk, Peco Road")
    phones = t.get("phone", "CELL: 042-35176872, 0300-0656620")
    ntn = t.get("ntn", "NTN: 6603558-6")

    lines: list[bytes] = [INIT, LINE_HEIGHT]

    # --- Header ---
    lines.append(ALIGN_CENTER)
    lines.append(DOUBLE_SIZE_ON + BOLD_ON)
    lines.append(f"{company}\n".encode("cp437", errors="replace"))
    lines.append(NORMAL_SIZE + BOLD_OFF)

    if address:
        lines.append(f"{address}\n".encode("cp437", errors="replace"))
    if phones:
        lines.append(f"{phones}\n".encode("cp437", errors="replace"))
    if ntn:
        lines.append(f"{ntn}\n".encode("cp437", errors="replace"))

    lines.append(b"=" * LINE_WIDTH + b"\n")

    # Inverted Title Banner
    lines.append(INVERT_ON + BOLD_ON)
    banner = " CASH SALE INVOICE "
    lines.append(f"{banner.center(LINE_WIDTH)}\n".encode("cp437", errors="replace"))
    lines.append(INVERT_OFF + BOLD_OFF)

    lines.append(b"-" * LINE_WIDTH + b"\n")
    lines.append(ALIGN_LEFT)

    # Info meta section
    date_str = t.get("date_str", "2026-08-22 14:00")
    rec_num = receipt_id or t.get("invoice_num", "446277")
    lines.append(f"Invoice #: {rec_num:<14} Date: {date_str}\n".encode("cp437", errors="replace"))
    if t.get("vehicle") or t.get("plate"):
        v_info = f"{t.get('vehicle', '')} ({t.get('plate', '')})".strip()
        lines.append(f"Vehicle:   {v_info}\n".encode("cp437", errors="replace"))
    if t.get("reading"):
        lines.append(f"Reading #: {t.get('reading')}\n".encode("cp437", errors="replace"))

    lines.append(b"=" * LINE_WIDTH + b"\n")

    # --- Table Header ---
    lines.append(BOLD_ON)
    lines.append(f"{'Item Name':<42}\n".encode("cp437", errors="replace"))
    lines.append(f"  {'Qty':>6}  {'Price':>8}  {'GST %':>8}  {'Amount':>10}\n".encode("cp437", errors="replace"))
    lines.append(BOLD_OFF)
    lines.append(b"-" * LINE_WIDTH + b"\n")

    # --- Table Rows ---
    for it in tx.items:
        # Item name line
        lines.append(BOLD_ON)
        lines.append(f"{it.name[:42]:<42}\n".encode("cp437", errors="replace"))
        lines.append(BOLD_OFF)
        # Detail row: Qty, Price, GST Rate, Amount
        price_str = f"{it.price:.2f}"
        qty_str = f"{it.qty:g}"
        gst_str = f"{it.tax_rate * 100:.0f}%" if hasattr(it, "tax_rate") else "18%"
        amt_str = f"{it.line_total():.2f}"
        lines.append(f"  {qty_str:>6}  {price_str:>8}  {gst_str:>8}  {amt_str:>10}\n".encode("cp437", errors="replace"))

    lines.append(b"=" * LINE_WIDTH + b"\n")

    # --- Totals Section ---
    def _tot_row(label: str, value: str, is_bold: bool = False, invert: bool = False) -> bytes:
        gap = LINE_WIDTH - len(label) - len(value)
        if gap < 1:
            gap = 1
        line_str = f"{label}{' ' * gap}{value}\n"
        b_data = line_str.encode("cp437", errors="replace")
        prefix = b""
        suffix = b""
        if is_bold:
            prefix += BOLD_ON
            suffix += BOLD_OFF
        if invert:
            prefix += INVERT_ON
            suffix += INVERT_OFF
        return prefix + b_data + suffix

    lines.append(_tot_row("Gross Total:", f"{tx.subtotal():.2f}"))
    if tx.tax_total():
        lines.append(_tot_row("Total GST (18%):", f"{tx.tax_total():.2f}"))
    if tx.discount:
        lines.append(_tot_row("Discount:", f"-{tx.discount:.2f}"))
    lines.append(_tot_row("FBR POS Fee:", "1.00"))

    lines.append(b"-" * LINE_WIDTH + b"\n")
    lines.append(_tot_row("Net Payable:", f"{tx.currency} {tx.total():.2f}", is_bold=True, invert=True))
    lines.append(b"=" * LINE_WIDTH + b"\n")

    # --- QR Code & Footer ---
    lines.append(ALIGN_CENTER)
    qr_payload = t.get("qr_payload") or f"INV:{rec_num}|TOTAL:{tx.total()}|FBR:154588F"
    lines.append(qr_code_bytes(qr_payload))

    footer = t.get("footer", "PLEASE VISIT AGAIN / YOUR FIRST CHOICE TO BUY. THANKS")
    lines.append(f"{footer}\n".encode("cp437", errors="replace"))
    lines.append(BOLD_ON)
    lines.append(b"SOFTWARE DEVELOPED BY - SOFTHIVE\n")
    lines.append(b"bizcare.pk | 03270708566\n")
    lines.append(BOLD_OFF)

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
    h = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(h, 1, ("receipt", None, "RAW"))
        win32print.StartPagePrinter(h)
        win32print.WritePrinter(h, data)
        win32print.EndPagePrinter(h)
        win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)
    return len(data)
