"""The bid desk: a company's file of papers with their expiry dates, and for each tender the
envelope of papers it asks for, checked against that file. Nothing here calls Claude: the list
of papers comes from the tender's own fields and the words of its notice."""
import datetime as dt
import os
import re
import uuid

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select

from . import bids
from .db import Bid, Paper, Tender

MAX_FILE = 15_000_000
MAX_PAPERS = 200
SOON_DAYS = 30
# key: (label, every bid asks for it, usually has an expiry date)
KINDS = {
    "trade_licence": ("Trade licence", True, True),
    "registration": ("Commercial registration", False, False),
    "tin": ("TIN certificate", True, False),
    "vat": ("VAT registration certificate", False, False),
    "tax_clearance": ("Tax clearance certificate", True, True),
    "competence": ("Competence or professional licence", False, True),
    "supplier_list": ("Supplier list registration", False, False),
    "audited_accounts": ("Audited accounts", False, False),
    "bank_letter": ("Bank letter or statement", False, False),
    "experience": ("Experience and completion letters", False, False),
    "other": ("Other paper", False, False),
}
# words in a notice that mean the buyer asks for a paper beyond the three every bid needs
ASKS = [("vat", r"\bvat\b|value added tax"),
        ("competence", r"competenc|professional licen[cs]e|certificate of competency|\bgrade\b|\bgc[- ]?\d|\bbc[- ]?\d"),
        ("supplier_list", r"suppliers? list|supplier registration|registered (?:in|on|with) (?:the )?(?:ppa|public procurement)"),
        ("audited_accounts", r"audited|audit report|financial statements?"),
        ("bank_letter", r"bank statement|financial standing|line of credit|bank letter"),
        ("experience", r"similar (?:work|project|experience|contract)|experience letters?|completion certificate|good performance")]
TYPES = {b"%PDF": ".pdf", b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png"}
MEDIA = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".png": "image/png"}


def label(p):
    return p.name or KINDS.get(p.kind, KINDS["other"])[0]


def state(p, today, until=""):
    """ok, soon (expires within a month), late (expires before `until`, a tender's closing date) or expired."""
    if not p.expires:
        return "ok"
    if p.expires < today:
        return "expired"
    if until and p.expires < until:
        return "late"
    if (dt.date.fromisoformat(p.expires) - dt.date.fromisoformat(today)).days <= SOON_DAYS:
        return "soon"
    return "ok"


def best(papers, kind, today):
    """The paper of this kind that lasts longest and has not expired, or None."""
    live = [p for p in papers if p.kind == kind and state(p, today) != "expired"]
    return max(live, key=lambda p: p.expires or "9999-12-31", default=None)


def needed_kinds(profile):
    return [k for k, v in KINDS.items() if v[1]] + (["vat"] if profile.get("vat_registered") else [])


def file_summary(papers, profile, today):
    """The company file sorted by what can hurt: missing, expired, expiring soon, in order."""
    held = {p.kind for p in papers}  # an expired paper is listed as expired, not also as missing
    missing = [{"kind": k, "label": KINDS[k][0]} for k in needed_kinds(profile) if k not in held]
    groups = {"missing": missing, "expired": [], "soon": [], "ok": []}
    for p in sorted(papers, key=lambda p: (p.expires or "9999", p.id)):
        groups[state(p, today)].append(p)
    groups["valid"] = len(groups["ok"]) + len(groups["soon"])
    groups["total"] = groups["valid"] + len(groups["expired"]) + len(missing)
    return groups


def requirements(t):
    """What goes in the envelope for this tender: papers from the company file, the two parts
    written in Bid Room, and the bid security the bank issues."""
    text = " ".join([t.title or "", t.notice or "", t.grade or ""]).lower()
    kinds = [k for k, v in KINDS.items() if v[1]]
    kinds += [k for k, rx in ASKS if re.search(rx, text) or (k == "competence" and t.category == "construction")]
    out = [{"key": k, "label": KINDS[k][0], "from": "file"} for k in dict.fromkeys(kinds)]
    out += [{"key": "technical", "label": "Technical offer", "from": "bidroom"},
            {"key": "price", "label": "Price schedule", "from": "bidroom"}]
    if (t.bid_security or "").strip():
        out.append({"key": "security", "label": "Bid security", "note": t.bid_security.strip()[:60], "from": "you"})
    return out


def envelope(t, papers, today, bid_data=None):
    """Each item the tender asks for with where it stands: in, late (expires before closing),
    missing (not in the company file), todo (written in Bid Room or brought by the firm)."""
    have = set((bid_data or {}).get("have") or [])
    items = []
    for r in requirements(t):
        r = dict(r)
        if r["from"] == "file":
            p = best(papers, r["key"], today)
            r["state"] = "missing" if not p else "late" if state(p, today, t.closes or "") == "late" else "in"
            if p and r["state"] == "late":
                r["note"] = "Expires before the closing date"
        elif r["key"] == "technical":
            r["state"] = "in" if (bid_data or {}).get("technical", "").strip() else "todo"
        elif r["key"] == "price":
            r["state"] = "in" if (bid_data or {}).get("financial") else "todo"
        else:
            r["state"] = "in" if r["key"] in have else "todo"
        items.append(r)
    done = sum(1 for r in items if r["state"] == "in")
    blockers = [r for r in items if r["state"] in ("missing", "late")]
    return {"items": items, "done": done, "total": len(items), "blockers": blockers,
            "short": [r for r in items if r["state"] != "in"]}


def days_left(t, today):
    try:
        return (dt.date.fromisoformat(t.closes) - dt.date.fromisoformat(today)).days if t.closes else None
    except ValueError:
        return None


def register(app, h):
    db, need, page, today, now = h.db, h.need, h.page, h.today, h.now
    folder = os.path.join(h.data_dir, "papers")

    def member(request, session):
        u = need(request, session, ("member",))
        if not u.company_id:
            raise HTTPException(403, "This page is for company accounts.")
        if not h.has_access(u):
            raise HTTPException(402, "Your company file is kept. Choose Win on your account page to keep using it.")
        return u

    def papers_of(session, company_id):
        return session.scalars(select(Paper).where(Paper.company_id == company_id).order_by(Paper.id)).all()

    h.papers_of = papers_of

    def own(session, u, pid):
        p = session.get(Paper, pid)
        if not p or p.company_id != u.company_id:
            raise HTTPException(404, "No such paper.")
        return p

    def blocked(session, u, papers):
        """For each kind missing from the file: the open bids it holds up, soonest first."""
        out = {}
        rows = session.scalars(select(Bid).where(Bid.company_id == u.company_id, Bid.state != "submitted")).all()
        for b in sorted(rows, key=lambda b: b.tender.closes or "9999"):
            d = days_left(b.tender, today())
            if d is not None and d < 0:
                continue
            for r in envelope(b.tender, papers, today(), bids.load(b))["blockers"]:
                out.setdefault(r["key"], []).append({"bid": b, "days": d})
        return out

    @app.get("/file", response_class=HTMLResponse)
    def company_file(request: Request, session=Depends(db)):
        u = member(request, session)
        papers = papers_of(session, u.company_id)
        prof = bids.load_profile(u.company)
        return page(request, "file.html", u, g=file_summary(papers, prof, today()), KINDS=KINDS, label=label,
                    blocked=blocked(session, u, papers), add=request.query_params.get("add", ""),
                    saved=request.query_params.get("saved"), error=request.query_params.get("error", ""))

    @app.post("/file")
    async def add_paper(request: Request, kind: str = Form(""), name: str = Form(""), number: str = Form(""),
                        issuer: str = Form(""), expires: str = Form(""), csrf: str = Form(""),
                        scan: UploadFile | None = File(None), session=Depends(db)):
        u = member(request, session)
        h.check_csrf(u, csrf)
        if kind not in KINDS:
            return RedirectResponse("/file?error=Choose+which+paper+this+is.", 303)
        expires = expires.strip()
        if expires:
            try:
                dt.date.fromisoformat(expires)
            except ValueError:
                return RedirectResponse(f"/file?add={kind}&error=Give+the+expiry+date+as+a+date.", 303)
        if len(papers_of(session, u.company_id)) >= MAX_PAPERS:
            return RedirectResponse("/file?error=Your+company+file+is+full.+Remove+old+papers+first.", 303)
        stored = ""
        if scan is not None and scan.filename:
            body = await scan.read(MAX_FILE + 1)
            ext = next((e for magic, e in TYPES.items() if body.startswith(magic)), "")
            if not ext:
                return RedirectResponse(f"/file?add={kind}&error=Upload+the+scan+as+a+PDF,+JPG+or+PNG.", 303)
            if len(body) > MAX_FILE:
                return RedirectResponse(f"/file?add={kind}&error=That+file+is+over+15+MB.", 303)
            sub = os.path.join(folder, str(u.company_id))
            os.makedirs(sub, exist_ok=True)
            stored = uuid.uuid4().hex + ext
            with open(os.path.join(sub, stored), "wb") as f:
                f.write(body)
        session.add(Paper(company_id=u.company_id, kind=kind, name=name.strip()[:160], number=number.strip()[:80],
                          issuer=issuer.strip()[:160], expires=expires, file=stored, created_by=u.id))
        session.commit()
        return RedirectResponse("/file?saved=1", 303)

    @app.post("/file/{pid}/delete")
    def delete_paper(request: Request, pid: int, csrf: str = Form(""), session=Depends(db)):
        u = member(request, session)
        h.check_csrf(u, csrf)
        p = own(session, u, pid)
        if p.file:
            try:
                os.remove(os.path.join(folder, str(u.company_id), p.file))
            except OSError:
                pass
        session.delete(p)
        session.commit()
        return RedirectResponse("/file?saved=1", 303)

    @app.get("/file/{pid}/scan")
    def paper_scan(request: Request, pid: int, session=Depends(db)):
        u = member(request, session)
        p = own(session, u, pid)
        ext = os.path.splitext(p.file)[1]
        path = os.path.join(folder, str(u.company_id), p.file)
        if not p.file or not re.fullmatch(r"[0-9a-f]{32}\.(pdf|jpg|png)", p.file) or not os.path.exists(path):
            raise HTTPException(404, "No scan was uploaded for this paper.")
        return FileResponse(path, media_type=MEDIA[ext], filename=f"{p.kind}{ext}",
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})

    @app.post("/bids/{bid_id}/have")
    def have(request: Request, bid_id: int, key: str = Form(""), on: str = Form(""), csrf: str = Form(""),
             session=Depends(db)):
        """The firm ticks something only it can bring, such as the bid security."""
        u = member(request, session)
        h.check_csrf(u, csrf)
        b = session.get(Bid, bid_id)
        if not b or b.company_id != u.company_id:
            raise HTTPException(404, "No such bid.")
        if key != "security":
            raise HTTPException(400, "That can't be ticked here.")
        d = bids.load(b)
        d["have"] = sorted(set(d.get("have") or []) - {key} | ({key} if on else set()))
        import json
        b.data, b.updated_at = json.dumps(d, ensure_ascii=False), now()
        session.commit()
        return RedirectResponse("/my", 303)

    def desk(session, u, matched):
        """Everything the bid desk shows for one company."""
        papers = papers_of(session, u.company_id)
        prof = bids.load_profile(u.company)
        t0 = today()
        open_bids = []
        rows = session.scalars(select(Bid).where(Bid.company_id == u.company_id, Bid.state != "submitted")).all()
        for b in rows:
            d = days_left(b.tender, t0)
            if d is not None and d < 0:
                continue
            open_bids.append({"bid": b, "t": b.tender, "days": d, "env": envelope(b.tender, papers, t0, bids.load(b))})
        open_bids.sort(key=lambda x: x["days"] if x["days"] is not None else 9999)
        started = {x["t"].id for x in open_bids}
        fresh = []
        for t in matched:
            if t.id in started:
                continue
            env = envelope(t, papers, t0)
            fresh.append({"t": t, "days": days_left(t, t0), "env": env,
                          "lack": [r for r in env["items"] if r["from"] == "file" and r["state"] != "in"]})
        fresh.sort(key=lambda x: (len(x["lack"]), x["days"] if x["days"] is not None else 9999))
        return {"bids": open_bids, "fresh": fresh, "g": file_summary(papers, prof, t0), "label": label}

    h.desk = desk
    h.envelope = envelope
