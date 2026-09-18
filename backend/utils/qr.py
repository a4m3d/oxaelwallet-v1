"""QR code generation for receiving addresses. Public data only."""
import io
import qrcode
from qrcode.constants import ERROR_CORRECT_M


def make_qr_png(data: str) -> bytes:
    """Render `data` (a public address or payment URI) to a PNG byte string."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0d0d0f", back_color="#e6f4ff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
