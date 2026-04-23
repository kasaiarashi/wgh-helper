from __future__ import annotations

import io

import qrcode


def render_terminal(payload: str) -> str:
    """Render a QR code as ASCII (half-block) suitable for terminal output."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()
