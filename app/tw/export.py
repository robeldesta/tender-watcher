"""The bid as a Word document (.docx) or a PDF, to download at any time and finish offline.

Both are built from the same list of blocks, so they match the printed bid. With prices left
blank (the firm keeps its prices off the platform), the price schedule keeps its items and
quantities with empty rate and amount columns, and the letters show a line to write the price on."""
import io
import re

from . import bidforms

BLANK = "______________________"
NUMERIC = {"value_etb", "remaining_etb", "amount_etb"}


def _num(v):
    v = re.sub(r"[^\d.]", "", str(v or ""))
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _money(v):
    return f"{v:,.2f}"


def build(x, t, p, today, blank_prices=False):
    """A list of pages; each page is a list of blocks (kind, ...)."""
    d, det = x["d"], x["d"]["details"]
    values = dict(x["values"])
    total, vat = x["total"], x["vat"]
    if blank_prices:
        for k in ("total_figures", "total_words", "price_before_vat"):
            values[k] = BLANK
        values["vat_note"] = "VAT: " + BLANK
    two = det.get("envelopes") == "two"
    date = bidforms.long(today)
    place = det.get("sign_place") or "Addis Ababa"
    pages = []

    def head(title, to=True):
        out = [("letterhead",), ("meta", f"Place and date: {place}, {date}",
                                  f"Procurement reference: {t.reference}" if t.reference else ""), ("h1", title)]
        if to:
            out.append(("to", t.buyer, t.place or ""))
        return out

    def paras(text):
        return [("p", para.strip().replace("\n", " ")) for para in (text or "").split("\n\n") if para.strip()]

    def lines(text):
        return [("lines", para.strip()) for para in (text or "").split("\n\n") if para.strip()]

    live = [f for f in x["forms"] if f["on"] and f["type"] != "details"]
    parts = [("T", "TECHNICAL BID"), ("F", "FINANCIAL BID")] if two else [("", "")]
    for part, name in parts:
        if two:
            pages.append([("label", t.buyer, t.place or "", name, t.title, t.reference or "",
                           values.get("closing") or "the bid opening time",
                           f"From: {p.get('legal_name', '')}" + (f", {p['address']}" if p.get("address") else "")
                           + (f", Tel. {p['phone']}" if p.get("phone") else ""))])
        for f in live:
            if two and f["part"] != part:
                continue
            pg = []
            typ = f["type"]
            if typ == "letter":
                text = bidforms.render(f["text"], values)
                pg += head(f["title"]) + paras(text)
                if f["sign"]:
                    pg.append(("sign",))
            elif typ == "text":
                pg += head(f["title"], False) + [("note", f"For: {t.title}")]
                pg += lines(f["text"]) if (f.get("text") or "").strip() else [("p", "[TO CONFIRM: write this section]")]
                if f["sign"]:
                    pg.append(("sign",))
            elif typ == "table":
                cols = [c for c in f["cols"] if c != "no"]
                rows = [[str(r.get("no") or i + 1)] + [
                    (f"{_num(r.get(c)):,.0f}" if c in NUMERIC and _num(r.get(c)) is not None else str(r.get(c, "")))
                    for c in cols] for i, r in enumerate(f["rows"])] or [[""] * (len(cols) + 1) for _ in range(4)]
                foot = []
                if f["key"] == "turnover":
                    vals = [_num(r.get("amount_etb")) for r in f["rows"] if _num(r.get("amount_etb")) is not None]
                    if vals:
                        foot = [["", "Average annual turnover", f"{sum(vals) / len(vals):,.0f}"]]
                pg += head(f["title"], False)
                pg.append(("table", ["No."] + [bidforms.COL[c] for c in cols], rows,
                           {i + 1 for i, c in enumerate(cols) if c in NUMERIC}, foot))
                if f["hint"]:
                    pg.append(("note", f["hint"]))
                if f["sign"]:
                    pg.append(("sign",))
            elif typ == "price":
                goods = d.get("kind") == "goods"
                hdr = ["No.", "Description"] + (["Country of origin"] if goods else []) + \
                      ["Unit", "Qty", "Rate" if d.get("kind") == "works" else "Unit price", "Amount"]
                rows = []
                for i, r in enumerate(d.get("financial") or []):
                    q, pr = _num(r.get("qty")), _num(r.get("price"))
                    rate = "" if blank_prices else (_money(pr) if pr is not None else r.get("price", ""))
                    amt = "" if blank_prices or q is None or pr is None else _money(q * pr)
                    rows.append([r.get("no") or str(i + 1), r.get("item", "")] + ([r.get("origin", "")] if goods else [])
                                + [r.get("unit", ""), r.get("qty", ""), rate, amt])
                if not rows:
                    rows = [[""] * len(hdr) for _ in range(8)]
                span = len(hdr) - 1
                if blank_prices:
                    foot = [["Total before VAT"] + [""] * (span - 1) + [""], ["VAT 15%"] + [""] * (span - 1) + [""],
                            ["Total bid price"] + [""] * (span - 1) + [""]]
                else:
                    foot = [["Total" + (" before VAT" if vat else "")] + [""] * (span - 1) + [_money(total)]]
                    if vat:
                        foot += [["VAT 15%"] + [""] * (span - 1) + [_money(vat)],
                                 ["Total bid price including VAT"] + [""] * (span - 1) + [_money(total + vat)]]
                pg += head(f["title"], False) + [("note", f"For: {t.title} · Currency: Ethiopian Birr")]
                pg.append(("table", hdr, rows, {len(hdr) - 3, len(hdr) - 2, len(hdr) - 1}, foot))
                pg.append(("p", "Total bid price in words: " + (BLANK + BLANK if blank_prices or not total
                                                                 else values.get("total_words") or "")))
                pg.append(("sign",))
            elif typ == "security":
                if det.get("security_type") == "declaration":
                    pg += head("Bid-Securing Declaration") + paras(values.get("securing_declaration") or
                                                                    bidforms.render(bidforms.SECURING_DECLARATION, values))
                    pg.append(("sign",))
                else:
                    pg += head(f["title"], False)
                    rows = [["Form", bidforms.SECURITY_LABEL.get(det.get("security_type"), "")],
                            ["Amount", det.get("security_amount", "")], ["Issued by", det.get("security_bank", "")],
                            ["Number", det.get("security_number", "")],
                            ["Date issued", bidforms.long(det["security_date"]) if det.get("security_date") else ""],
                            ["In favour of", t.buyer]]
                    if det.get("security_type") == "guarantee" and x.get("security_until"):
                        rows.append(["Valid until at least", bidforms.long(x["security_until"])])
                    pg.append(("kv", rows))
                    pg.append(("note", "The original is attached to this page."))
            elif typ == "papers":
                pg += head(f["title"], False) + [("p", "The following documents are attached to this bid, in this order:")]
                rows = []
                for r in x["env"]["items"]:
                    if r.get("from") != "file":
                        continue
                    pp = x["held"].get(r["key"])
                    rows.append([str(len(rows) + 1), r["label"], pp.number if pp else "", pp.issuer if pp else "",
                                 bidforms.long(pp.expires) if pp and pp.expires else ""])
                if det.get("security_type") in ("cpo", "guarantee"):
                    rows.append([str(len(rows) + 1), f"Bid security ({bidforms.SECURITY_LABEL[det['security_type']]}), original",
                                 det.get("security_number", ""), det.get("security_bank", ""), ""])
                pg.append(("table", ["No.", "Document", "Number", "Issued by", "Valid until"], rows, set(), []))
                pg.append(("sign",))
            elif typ == "profile":
                pg += head(f["title"], False)
                rep = (p.get("signatory_name") or "") + (", " + p["signatory_title"] if p.get("signatory_title") else "")
                pg.append(("kv", [[a, str(b or "")] for a, b in [
                    ("Bidder's legal name", p.get("legal_name")), ("Legal address", p.get("address")), ("P.O. Box", p.get("po_box")),
                    ("Telephone", p.get("phone")), ("Email", p.get("email")), ("TIN", p.get("tin")),
                    ("VAT registration number", p.get("vat_number")), ("Year of establishment", p.get("year_established")),
                    ("Grade or licence class", p.get("grade")), ("Permanent staff", p.get("staff_count")),
                    ("Authorised representative", rep), ("Bank and branch", p.get("bank_name")),
                    ("Bank account number", p.get("bank_account"))]]))
                if p.get("licences"):
                    pg.append(("h2", "Licences and certificates"))
                    pg.append(("table", ["Licence or certificate", "Number", "Issued by", "Valid until"],
                               [[lc.get("name", ""), lc.get("number", ""), lc.get("issuer", ""),
                                 bidforms.long(lc["expires"]) if lc.get("expires") else ""] for lc in p["licences"]], set(), []))
                pg.append(("sign",))
            if pg:
                pages.append(pg)
    return pages


# -- Word
def to_docx(pages, p, stamp=None, footer=""):
    from docx import Document
    from docx.enum.section import WD_ORIENT  # noqa: F401
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.left_margin = sec.right_margin = Mm(18)
    sec.top_margin, sec.bottom_margin = Mm(15), Mm(18)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    st.paragraph_format.space_after = Pt(5)
    navy = RGBColor(0x0B, 0x2F, 0x65)
    grey = RGBColor(0x5D, 0x6B, 0x82)

    def field(run, code):
        for kind, text in (("begin", None), (None, code), ("end", None)):
            if kind:
                el = OxmlElement("w:fldChar")
                el.set(qn("w:fldCharType"), kind)
            else:
                el = OxmlElement("w:instrText")
                el.set(qn("xml:space"), "preserve")
                el.text = text
            run._r.append(el)

    # footer on every page: company and reference, page N of M, and the stamp if the firm has one
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    r = fp.add_run(footer + "    Page ")
    r.font.size, r.font.color.rgb = Pt(8), grey
    field(fp.add_run(), "PAGE")
    r = fp.add_run(" of ")
    r.font.size, r.font.color.rgb = Pt(8), grey
    field(fp.add_run(), "NUMPAGES")
    if stamp:
        fp.add_run("    ").add_picture(io.BytesIO(stamp), width=Mm(20))

    def shade(cell):
        tc = cell._tc.get_or_add_tcPr()
        sh = OxmlElement("w:shd")
        sh.set(qn("w:val"), "clear")
        sh.set(qn("w:fill"), "F3F5F8")
        tc.append(sh)

    def table(hdr, rows, numeric=(), foot=()):
        tb = doc.add_table(rows=1, cols=len(hdr))
        tb.style = "Table Grid"
        tb.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, h in enumerate(hdr):
            c = tb.rows[0].cells[i]
            c.text = ""
            run = c.paragraphs[0].add_run(h)
            run.bold, run.font.size = True, Pt(9)
            shade(c)
        tr = tb.rows[0]._tr.get_or_add_trPr()  # header repeats on each page
        el = OxmlElement("w:tblHeader")
        el.set(qn("w:val"), "true")
        tr.append(el)
        for row in list(rows) + list(foot):
            cells = tb.add_row().cells
            for i, v in enumerate(row):
                c = cells[i]
                c.text = ""
                run = c.paragraphs[0].add_run(str(v))
                run.font.size = Pt(9)
                if row in foot:
                    run.bold = True
                if i in numeric or (row in foot and i == len(row) - 1):
                    c.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
        doc.add_paragraph()

    first = True
    for pg in pages:
        if not first:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        first = False
        for b in pg:
            k = b[0]
            if k == "letterhead":
                para = doc.add_paragraph()
                run = para.add_run(p.get("legal_name", ""))
                run.bold, run.font.size, run.font.color.rgb = True, Pt(15), navy
                meta = " · ".join(x for x in [p.get("address"), p.get("po_box") and f"P.O. Box {p['po_box']}",
                                              p.get("phone") and f"Tel. {p['phone']}", p.get("email"),
                                              p.get("tin") and f"TIN {p['tin']}",
                                              p.get("vat_number") and f"VAT reg. no. {p['vat_number']}"] if x)
                para2 = doc.add_paragraph()
                r2 = para2.add_run(meta)
                r2.font.size, r2.font.color.rgb = Pt(8.5), grey
                pPr = para2._p.get_or_add_pPr()
                bd = OxmlElement("w:pBdr")
                bot = OxmlElement("w:bottom")
                for a, v in (("w:val", "single"), ("w:sz", "12"), ("w:space", "4"), ("w:color", "0B2F65")):
                    bot.set(qn(a), v)
                bd.append(bot)
                pPr.append(bd)
            elif k == "meta":
                para = doc.add_paragraph()
                r = para.add_run(b[1] + ("        " + b[2] if b[2] else ""))
                r.font.size, r.font.color.rgb = Pt(9), grey
            elif k == "h1":
                para = doc.add_paragraph()
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r = para.add_run(b[1].upper())
                r.bold, r.font.size = True, Pt(12.5)
            elif k == "h2":
                r = doc.add_paragraph().add_run(b[1])
                r.bold, r.font.color.rgb = True, navy
            elif k == "to":
                para = doc.add_paragraph()
                para.add_run("To: ").font.color.rgb = grey
                para.add_run(b[1]).bold = True
                if b[2]:
                    para.add_run("\n" + b[2])
            elif k == "p":
                doc.add_paragraph(b[1])
            elif k == "lines":
                ls = b[1].split("\n")
                para = doc.add_paragraph()
                for i, line in enumerate(ls):
                    run = para.add_run(line)
                    if i == 0 and line[:1].isdigit():
                        run.bold = True
                    if i < len(ls) - 1:
                        run.add_break()
            elif k == "note":
                r = doc.add_paragraph().add_run(b[1])
                r.font.size, r.font.color.rgb, r.italic = Pt(9), grey, True
            elif k == "table":
                table(b[1], b[2], b[3], b[4])
            elif k == "kv":
                tb = doc.add_table(rows=0, cols=2)
                tb.style = "Table Grid"
                for a, v in b[1]:
                    cells = tb.add_row().cells
                    cells[0].text, cells[1].text = a, v
                    shade(cells[0])
                    cells[0].width, cells[1].width = Mm(62), Mm(112)
                doc.add_paragraph()
            elif k == "sign":
                tb = doc.add_table(rows=1, cols=2)
                left, right = tb.rows[0].cells
                left.width, right.width = Mm(120), Mm(54)
                left.text = ""
                for i, (a, v) in enumerate([("Name", p.get("signatory_name", "")), ("In the capacity of", p.get("signatory_title", "")),
                                            ("Signed", ""), ("Duly authorised to sign for and on behalf of", p.get("legal_name", "")),
                                            ("Date", "")]):
                    para = left.paragraphs[0] if i == 0 else left.add_paragraph()
                    ra = para.add_run(a + ": ")
                    ra.font.color.rgb, ra.font.size = grey, Pt(9.5)
                    para.add_run(v or "______________________________")
                right.text = ""
                if stamp:
                    right.paragraphs[0].add_run().add_picture(io.BytesIO(stamp), width=Mm(34))
                else:
                    rr = right.paragraphs[0].add_run("Company stamp")
                    rr.font.color.rgb, rr.font.size = grey, Pt(9)
                right.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif k == "label":
                _, buyer, bplace, name, title, ref, closing, frm = b
                for text, size, bold in [(f"To: {buyer}", 14, True), (bplace, 12, False), ("", 12, False), (name, 26, True),
                                         ("", 12, False), (title, 13, False), (f"Procurement reference: {ref}" if ref else "", 13, True),
                                         ("ORIGINAL / COPY ____", 13, False), (f"Do not open before {closing}", 13, True),
                                         ("", 12, False), (frm, 10, False)]:
                    para = doc.add_paragraph()
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run = para.add_run(text)
                    run.bold, run.font.size = bold, Pt(size)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# -- PDF
def to_pdf(pages, p, stamp=None, footer="", stamp_every=False):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    from xml.sax.saxutils import escape

    navy, grey, rule = colors.HexColor("#0B2F65"), colors.HexColor("#5D6B82"), colors.HexColor("#9AA4B2")
    base = ParagraphStyle("b", fontName="Helvetica", fontSize=9.8, leading=13.5, spaceAfter=5)
    small = ParagraphStyle("s", parent=base, fontSize=8.5, leading=11, textColor=grey)
    cell = ParagraphStyle("c", parent=base, fontSize=8.5, leading=10.5, spaceAfter=0)
    cellr = ParagraphStyle("cr", parent=cell, alignment=TA_RIGHT)
    cellb = ParagraphStyle("cb", parent=cell, fontName="Helvetica-Bold")
    h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=12, alignment=TA_CENTER, spaceBefore=4, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", textColor=navy, spaceBefore=8)
    name = ParagraphStyle("n", parent=base, fontName="Helvetica-Bold", fontSize=14, textColor=navy, spaceAfter=2)
    center = ParagraphStyle("ce", parent=base, alignment=TA_CENTER, fontSize=13, leading=18)
    stamp_img = ImageReader(io.BytesIO(stamp)) if stamp else None
    W = A4[0] - 36 * mm

    def P(text, style=base):
        return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)

    class Numbered(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self.setFont("Helvetica", 7.5)
                self.setFillColor(grey)
                self.drawString(18 * mm, 9 * mm, footer[:100])
                self.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {self._pageNumber} of {n}")
                if stamp_every and stamp_img:
                    self.drawImage(stamp_img, A4[0] - 18 * mm - 24 * mm, 13 * mm, 22 * mm, 22 * mm, mask="auto",
                                   preserveAspectRatio=True)
                super().showPage()
            super().save()

    story = []
    for i, pg in enumerate(pages):
        if i:
            story.append(PageBreak())
        for b in pg:
            k = b[0]
            if k == "letterhead":
                meta = " · ".join(x for x in [p.get("address"), p.get("po_box") and f"P.O. Box {p['po_box']}",
                                              p.get("phone") and f"Tel. {p['phone']}", p.get("email"),
                                              p.get("tin") and f"TIN {p['tin']}",
                                              p.get("vat_number") and f"VAT reg. no. {p['vat_number']}"] if x)
                lh = Table([[P(p.get("legal_name"), name)], [P(meta, small)]], colWidths=[W])
                lh.setStyle(TableStyle([("LINEBELOW", (0, 1), (-1, 1), 1.5, navy), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                        ("BOTTOMPADDING", (0, 1), (-1, 1), 6)]))
                story += [lh, Spacer(1, 8)]
            elif k == "meta":
                t = Table([[P(b[1], small), P(b[2], ParagraphStyle("mr", parent=small, alignment=TA_RIGHT))]], colWidths=[W / 2, W / 2])
                t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
                story.append(t)
            elif k == "h1":
                story.append(P(b[1].upper(), h1))
            elif k == "h2":
                story.append(P(b[1], h2))
            elif k == "to":
                story.append(Paragraph(f'<font color="#5D6B82">To:</font> <b>{escape(b[1] or "")}</b>'
                                       + (f"<br/>{escape(b[2])}" if b[2] else ""), base))
            elif k == "p":
                story.append(P(b[1]))
            elif k == "lines":
                ls = b[1].split("\n")
                first = f"<b>{escape(ls[0])}</b>" if ls[0][:1].isdigit() else escape(ls[0])
                story.append(Paragraph("<br/>".join([first] + [escape(x) for x in ls[1:]]), base))
            elif k == "note":
                story.append(P(b[1], small))
            elif k in ("table", "kv"):
                if k == "kv":
                    data = [[P(a, cellb), P(v, cell)] for a, v in b[1]]
                    widths, hdr_rows, foot_n = [W * .36, W * .64], 0, 0
                else:
                    hdr, rows, numeric, foot = b[1], b[2], b[3], b[4]
                    data = [[P(h, cellb) for h in hdr]]
                    for r in list(rows) + list(foot):
                        data.append([P(v, cellr if (j in numeric or (r in foot and j == len(r) - 1)) else
                                       (cellb if r in foot else cell)) for j, v in enumerate(r)])
                    n = len(hdr)
                    first_w = 11 * mm
                    widths = [first_w] + [(W - first_w) / (n - 1)] * (n - 1) if hdr[0] == "No." and n > 1 else [W / n] * n
                    if hdr[:2] == ["No.", "Description"]:  # price schedules: a wide description
                        rest = (W - first_w - (W - first_w) * .40) / (n - 2)
                        widths = [first_w, (W - first_w) * .40] + [rest] * (n - 2)
                    hdr_rows, foot_n = 1, len(foot)
                tb = Table(data, colWidths=widths, repeatRows=hdr_rows)
                style = [("GRID", (0, 0), (-1, -1), .5, rule), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                         ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
                if hdr_rows:
                    style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F5F8")))
                if k == "kv":
                    style.append(("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F3F5F8")))
                if foot_n:
                    for j in range(len(data) - foot_n, len(data)):
                        style.append(("SPAN", (0, j), (-2, j)))
                tb.setStyle(TableStyle(style))
                story += [tb, Spacer(1, 6)]
            elif k == "sign":
                rows = [("Name", p.get("signatory_name")), ("In the capacity of", p.get("signatory_title")), ("Signed", ""),
                        ("Duly authorised to sign for and on behalf of", p.get("legal_name")), ("Date", "")]
                left = Table([[P(a, small), P(v or "", base)] for a, v in rows], colWidths=[48 * mm, W - 48 * mm - 42 * mm])
                left.setStyle(TableStyle([("LINEBELOW", (1, 0), (1, -1), .5, rule), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
                right = Image(io.BytesIO(stamp), width=34 * mm, height=34 * mm, kind="proportional") if stamp else P("Company stamp", small)
                box = Table([[left, right]], colWidths=[W - 42 * mm, 42 * mm])
                box.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("ALIGN", (1, 0), (1, 0), "CENTER"),
                                         ("BOX", (1, 0), (1, 0), .5, rule) if not stamp else ("LEFTPADDING", (0, 0), (0, 0), 0)]))
                story += [Spacer(1, 14), KeepTogether([box])]
            elif k == "label":
                _, buyer, bplace, nm, title, ref, closing, frm = b
                inner = [P(f"To: {buyer}", center), P(bplace, center), Spacer(1, 14),
                         P(nm, ParagraphStyle("big", parent=center, fontName="Helvetica-Bold", fontSize=24, leading=30)),
                         Spacer(1, 14), P(title, center), P(f"Procurement reference: {ref}" if ref else "", center),
                         P("ORIGINAL / COPY ____", center), P(f"Do not open before {closing}", ParagraphStyle("cb2", parent=center, fontName="Helvetica-Bold")),
                         Spacer(1, 10), P(frm, ParagraphStyle("cs", parent=center, fontSize=10))]
                lb = Table([[inner]], colWidths=[W])
                lb.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 2, colors.black), ("TOPPADDING", (0, 0), (-1, -1), 30),
                                        ("BOTTOMPADDING", (0, 0), (-1, -1), 30)]))
                story += [Spacer(1, 40), lb]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=15 * mm, bottomMargin=20 * mm,
                            title="Bid", author=p.get("legal_name", ""))
    doc.build(story, canvasmaker=Numbered)
    return buf.getvalue()
