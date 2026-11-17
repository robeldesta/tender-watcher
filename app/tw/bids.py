"""The bid workspace: a company's bid profile, and drafting a bid for one tender with the
bid-pack engine in src/tenderwatch (bid_draft.py). Facts in tables come from the profile;
Claude writes the checklist and the prose, and anything it can't know is marked [TO CONFIRM]."""
import datetime as dt
import json
import os
import re
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for _p in (os.environ.get("TENDERWATCH_SRC", ""), os.path.join(_here, "..", "..", "src"),
           os.path.join(_here, "..", "..", "..", "src")):
    if _p and os.path.isdir(os.path.join(_p, "tenderwatch")):
        sys.path.insert(0, os.path.abspath(_p))
        break

from tenderwatch import bid_draft, bid_profile  # noqa: E402
from tenderwatch.models import Tender as EngineTender  # noqa: E402

from . import bidforms  # noqa: E402

STATUS_LABEL = {"met": "Met", "gap": "Gap", "check": "Check"}
MAX_DOC_CHARS = bid_draft.MAX_TENDER_CHARS
MAX_TEXT = 20000
MAX_ROWS = 60
MAX_PRICE_ROWS = 400  # a works bill of quantities runs to hundreds of items
ROWS = {  # profile tables: field name -> columns
    "licences": ["name", "number", "issuer", "expires"],
    "projects": ["name", "client", "value_etb", "year_completed", "region", "role", "scope"],
    "key_personnel": ["role", "name", "qualification", "years_experience", "similar_years"],
    "equipment_items": ["item", "qty", "ownership", "year_made"],
}
TEXT_FIELDS = ["legal_name", "grade", "address", "phone", "email", "tin", "vat_number", "signatory_name", "signatory_title",
               "year_established", "staff_count", "regions", "work_types", "equipment", "turnover", "notes",
               "po_box", "bank_name", "bank_account"]


def load_profile(c):
    try:
        d = json.loads(c.profile or "{}")
    except ValueError:
        d = {}
    d.setdefault("legal_name", c.name)
    d.setdefault("tin", c.tin or "")
    for k in ROWS:
        d.setdefault(k, [])
    return d


def profile_from_form(form, c):
    """The profile form as a dict; empty rows are dropped, text is capped."""
    d = {k: str(form.get(k, "")).strip()[:2000] for k in TEXT_FIELDS}
    d["vat_number"] = re.sub(r"[^0-9A-Za-z/-]", "", d["vat_number"])[:30]
    d["vat_registered"] = bool(form.get("vat_registered")) or bool(d["vat_number"])
    d["legal_name"] = d["legal_name"] or c.name
    for k, cols in ROWS.items():
        cols_vals = {col: form.getlist(f"{k}.{col}") for col in cols}
        n = max((len(v) for v in cols_vals.values()), default=0)
        rows = []
        for i in range(min(n, MAX_ROWS)):
            row = {col: (cols_vals[col][i] if i < len(cols_vals[col]) else "").strip()[:500] for col in cols}
            if any(row.values()):
                rows.append(row)
        d[k] = rows
    return d


def completeness(d):
    """How much of the profile a bid can use, 0 to 100, and what's missing."""
    checks = [("Legal name", d.get("legal_name")), ("TIN", d.get("tin")),
              ("VAT number", d.get("vat_number") or not d.get("vat_registered")), ("Address", d.get("address")),
              ("Signatory", d.get("signatory_name")), ("Licences", d.get("licences")),
              ("Past projects", d.get("projects")), ("Key staff", d.get("key_personnel")),
              ("What you do", d.get("work_types"))]
    missing = [n for n, v in checks if not v]
    return (len(checks) - len(missing)) * 100 // len(checks), missing


def _num(v, cast=float):
    v = re.sub(r"[^\d.]", "", str(v or ""))
    try:
        return cast(float(v)) if v else None
    except ValueError:
        return None


def _date(v):
    try:
        return dt.date.fromisoformat(str(v).strip()) if v else None
    except ValueError:
        return None


def _list(v):
    return [x.strip() for x in re.split(r"[,\n]", v or "") if x.strip()]


def engine_profile(d):
    """The stored profile as the engine's BidProfile."""
    turnover = {}
    for line in (d.get("turnover") or "").splitlines():
        m = re.match(r"\s*(\d{4})\s*[:=-]\s*(.+)", line)
        if m and _num(m.group(2)) is not None:
            turnover[int(m.group(1))] = _num(m.group(2))
    return bid_profile.BidProfile(
        legal_name=d.get("legal_name") or "", grade=d.get("grade") or "", address=d.get("address") or "",
        phone=d.get("phone") or "", email=d.get("email") or "", tin=d.get("tin") or None,
        vat_registered=bool(d.get("vat_registered")), vat_number=d.get("vat_number") or "", year_established=_num(d.get("year_established"), int),
        signatory_name=d.get("signatory_name") or "", signatory_title=d.get("signatory_title") or "",
        regions=_list(d.get("regions")), work_types=_list(d.get("work_types")),
        staff_count=_num(d.get("staff_count"), int), annual_turnover_etb=turnover,
        licences=[bid_profile.Licence(name=x["name"] or "Licence", number=x.get("number") or None,
                                      issuer=x.get("issuer") or None, expires=_date(x.get("expires")))
                  for x in d.get("licences", [])],
        projects=[bid_profile.PastProject(id=f"p{i + 1}", name=x.get("name") or "Project", client=x.get("client") or "",
                                          value_etb=_num(x.get("value_etb")), year_completed=_num(x.get("year_completed"), int),
                                          region=x.get("region") or None, role=x.get("role") or "Main contractor",
                                          scope=x.get("scope") or "")
                  for i, x in enumerate(d.get("projects", []))],
        key_personnel=[bid_profile.KeyPerson(role=x.get("role") or "", name=x.get("name") or "",
                                             qualification=x.get("qualification") or "",
                                             years_experience=_num(x.get("years_experience"), int))
                       for x in d.get("key_personnel", [])],
        equipment=_list(d.get("equipment")), notes=d.get("notes") or "")


def engine_tender(t):
    lines = [t.notice or "", f"Reference: {t.reference}" if t.reference else "",
             f"Bid security: {t.bid_security}" if t.bid_security else "",
             f"Document fee: {t.document_fee}" if t.document_fee else "",
             f"Required grade: {t.grade}" if t.grade else "", f"Place: {t.place}" if t.place else "",
             f"Closing time: {t.time}" if t.time else ""]
    return EngineTender(source=t.source or "tenders.sonador.et", url=t.url or "", title=t.title, buyer=t.buyer,
                        raw_text="\n".join(x for x in lines if x), deadline=_date(t.closes),
                        region=t.region or None, sector=t.category)


def blank():
    return {"checklist": [], "cover_letter": "", "profile_section": "", "technical": "", "financial": [],
            "open_questions": [], "warnings": [], "projects": [], "doc_text": "", "notes": "", "drafted_at": "",
            "have": [],  # have: envelope items the firm ticked itself, such as the bid security
            "kind": "", "details": {}, "forms": {}}  # the bid's forms: see bidforms.py


def ensure(d, t):
    """Fill the bid's kind and details from the tender the first time the bid is opened."""
    if d.get("kind") not in bidforms.KINDS:
        d["kind"] = bidforms.guess_kind(t)
    det = bidforms.details_blank(t, d["kind"])
    det.update({k: v for k, v in (d.get("details") or {}).items() if v not in (None,)})
    d["details"] = det
    return d


def load(bid):
    d = blank()
    try:
        d.update(json.loads(bid.data or "{}"))
    except ValueError:
        pass
    return d


def clean_save(d, incoming):
    """Merge what the browser sends on autosave into the stored bid. Only known fields, capped."""
    out = dict(d)
    for k in ("cover_letter", "profile_section", "technical", "notes"):
        if k in incoming:
            out[k] = str(incoming[k])[:MAX_TEXT]
    if "doc_text" in incoming:
        out["doc_text"] = str(incoming["doc_text"])[:MAX_DOC_CHARS]
    if isinstance(incoming.get("done"), list):
        done = {int(i) for i in incoming["done"] if str(i).isdigit()}
        for i, item in enumerate(out["checklist"]):
            item["done"] = i in done
    if isinstance(incoming.get("financial"), list):
        rows = []
        for r in incoming["financial"][:MAX_PRICE_ROWS]:
            if not isinstance(r, dict):
                continue
            row = {"item": str(r.get("item", ""))[:300], "unit": str(r.get("unit", ""))[:40],
                   "qty": str(r.get("qty", ""))[:20], "price": str(r.get("price", ""))[:20]}
            if any(row.values()):
                if str(r.get("no", "")).strip():
                    row["no"] = str(r["no"])[:20]
                if str(r.get("origin", "")).strip():
                    row["origin"] = str(r["origin"])[:60]
                rows.append(row)
        out["financial"] = rows
    if incoming.get("kind") in bidforms.KINDS:
        out["kind"] = incoming["kind"]
    if isinstance(incoming.get("details"), dict):
        out["details"] = {**(out.get("details") or {}), **bidforms.clean_details(incoming["details"])}
    if isinstance(incoming.get("forms"), dict):
        forms = {k: dict(v) for k, v in (out.get("forms") or {}).items()}
        for k, v in bidforms.clean_forms(incoming["forms"], out.get("kind")).items():
            if "text" in v and bidforms.FORMS[k]["type"] == "letter" and \
                    _same(v["text"], bidforms.standard_text(k, out.get("kind"))):
                v["text"] = ""  # the standard wording: kept out of storage so improvements reach old bids
            forms.setdefault(k, {}).update(v)
        out["forms"] = forms
    if isinstance(incoming.get("details"), dict):  # a bid security with its number counts as in the envelope
        det = out["details"]
        if det.get("security_type") == "declaration" or (det.get("security_type") in ("cpo", "guarantee") and det.get("security_number")):
            out["have"] = sorted(set(out.get("have") or []) | {"security"})
    return out


def _same(a, b):
    return re.sub(r"\s+", " ", a or "").strip() == re.sub(r"\s+", " ", b or "").strip()


def total(d):
    t = 0.0
    for r in d.get("financial", []):
        q, p = _num(r.get("qty")), _num(r.get("price"))
        if q is not None and p is not None:
            t += q * p
    return t


def run_draft(client, t, profile_dict, d, today=None, own_wording=""):
    """One Claude call through the bid-pack engine. Returns the bid data with the draft merged in;
    the firm's own edits to sections they already wrote are kept. own_wording: text from the
    firm's past bids (library.own_wording) for the draft to reuse."""
    pack = bid_draft.draft(engine_tender(t), engine_profile(profile_dict), client,
                           extra_text=d.get("doc_text", ""), today=today, own_wording=own_wording)
    out = dict(d)
    old_done = {x["requirement"]: x.get("done") for x in d.get("checklist", [])}
    out["checklist"] = [{"requirement": r.requirement, "category": r.category, "notice_says": r.notice_says,
                         "status": r.status, "action": r.action, "done": bool(old_done.get(r.requirement))}
                        for r in pack.requirements]
    for k, paras in (("cover_letter", pack.cover_letter), ("profile_section", pack.profile_section),
                     ("technical", pack.technical_offer)):
        if not (d.get(k) or "").strip():
            out[k] = "\n\n".join(paras)
    out["open_questions"] = pack.open_questions
    out["warnings"] = pack.warnings
    out["projects"] = [x.id for x in pack.relevant_projects]
    out["drafted_at"] = (today or dt.date.today()).isoformat()
    out["used_past"] = bool(own_wording.strip())
    return out


def to_confirm(d, extra=()):
    text = " ".join([d.get("cover_letter", ""), d.get("profile_section", ""), d.get("technical", ""), *extra])
    return list(dict.fromkeys(m.strip() for m in re.findall(r"\[TO CONFIRM:([^\]]*)\]", text)))
