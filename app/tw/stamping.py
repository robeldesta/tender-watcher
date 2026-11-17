"""Stamp and number every page of a bid. Ethiopian buyers often ask for every page of a bid to
carry the company stamp, and bids run to hundreds of pages; doing it by hand takes a day.

stamp_pdf() takes the firm's PDF (the forms printed from Bid Room, a full bid assembled
elsewhere, or both), optionally adds the scans from the company file at the end, and puts the
stamp image, "Page N of M" and a short footer on every page."""
import io

from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

MAX_PAGES = 2000
MM = 72 / 25.4


class StampError(ValueError):
    pass


def clean_stamp(data):
    """A photo or scan of the stamp as a small PNG with the white paper made see-through, so it
    sits on text like a real stamp. Raises StampError if the picture can't be read."""
    from PIL import Image, ImageChops
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:
        raise StampError("That picture could not be read. Choose a PNG or JPG of your stamp.") from e
    im = im.convert("RGBA")
    im.thumbnail((700, 700))
    paper = im.convert("L").point(lambda v: 0 if v > 215 else 255)  # light pixels are the paper
    im.putalpha(ImageChops.multiply(im.getchannel("A"), paper))
    box = im.getbbox()  # trim the empty margin around the stamp
    if box:
        im = im.crop(box)
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def image_pdf(data):
    """One A4 page holding an image (a JPG or PNG scan from the company file), as PDF bytes."""
    img = ImageReader(io.BytesIO(data))
    w, h = img.getSize()
    pw, ph = 210 * MM, 297 * MM
    margin = 12 * MM
    scale = min((pw - 2 * margin) / w, (ph - 2 * margin - 14 * MM) / h)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pw, ph))
    c.drawImage(img, (pw - w * scale) / 2, ph - margin - h * scale, w * scale, h * scale, preserveAspectRatio=True)
    c.showPage()
    c.save()
    return buf.getvalue()


def _overlay(w, h, stamp, n, total, footer, stamp_mm, numbers):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    if stamp is not None:
        s = stamp_mm * MM
        c.drawImage(stamp, w - s - 8 * MM, 8 * MM, s, s, mask="auto", preserveAspectRatio=True)
    if numbers:
        c.setFont("Helvetica", 8)
        c.setFillGray(0.35)
        c.drawCentredString(w / 2, 6 * MM, f"Page {n} of {total}")
        if footer:
            c.drawString(10 * MM, 6 * MM, footer[:90])
    c.showPage()
    c.save()
    return PdfReader(io.BytesIO(buf.getvalue())).pages[0]


def stamp_pdf(pdfs, stamp_png=None, footer="", stamp_mm=26, numbers=True, skip_first=False):
    """pdfs: list of PDF bytes, joined in order. Returns (bytes, page count)."""
    writer = PdfWriter()
    for data in pdfs:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                reader.decrypt("")
            for page in reader.pages:
                writer.add_page(page)
        except Exception as e:  # a broken or protected PDF: say which, keep the detail in the log
            raise StampError("One of the PDFs could not be read. Save it again as PDF and retry.") from e
        if len(writer.pages) > MAX_PAGES:
            raise StampError(f"That is more than {MAX_PAGES} pages. Split the bid and stamp each part.")
    if not writer.pages:
        raise StampError("The PDF has no pages.")
    stamp = ImageReader(io.BytesIO(stamp_png)) if stamp_png else None
    total = len(writer.pages)
    for i, page in enumerate(writer.pages):
        if skip_first and i == 0:
            continue
        box = page.mediabox
        w, h = float(box.width), float(box.height)
        rot = (page.get("/Rotate") or 0) % 360
        if rot:
            page.transfer_rotation_to_content()
            box = page.mediabox
            w, h = float(box.width), float(box.height)
        ov = _overlay(w, h, stamp, i + 1, total, footer, stamp_mm, numbers)
        page.merge_transformed_page(ov, Transformation().translate(float(box.left), float(box.bottom)))
    writer.compress_identical_objects()
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), total
