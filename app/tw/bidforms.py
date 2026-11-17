"""The forms a bid is made of, laid out the way Ethiopian bidding documents lay them out.

Sources (see docs/bid-forms.md for the full map):
- PPA Standard Bidding Document for Goods, National Competitive Bidding (ppa.gov.et, 2024):
  Section 4 Bidding Forms: A Bid Submission Sheet, B Price Schedule, C Bidder Certification of
  Compliance (general information, financial standing, experience, personnel, equipment, bank),
  D Joint venture data, E Bid Security, F Manufacturer's Authorization; Section 6 Technical
  Specification Compliance Sheet.
- PPA SBD for Works and Turnkey (ppa.gov.et, Jan 2026), and the works bidding documents of the
  Ministry of Water and Energy and the Ministry of Health (2025-26): Letter of Bid, Bill of
  Quantities, Bid Security, Power of Attorney, qualification forms (experience, turnover,
  financial resources, current commitments, litigation), key personnel and equipment schedules,
  work methods, site organisation and work schedule.
- Request for Proposals for consultancy (TECH-1 to TECH-6, FIN-1 to FIN-4).

Each bid has a kind (works, goods, services, consultancy) and one or two envelopes. A form is a
letter (standard wording with {tokens} filled from the company file and the bid), a table, a
text section, or a view of the company file. Letters can be edited to match the buyer's own
wording; anything the bid doesn't know yet prints as [TO CONFIRM: ...]."""
import datetime as dt
import re

KINDS = {"works": "Works", "goods": "Goods", "services": "Services", "consultancy": "Consultancy"}
SECURITY_TYPES = {"cpo": "a CPO (certified payment order)", "guarantee": "an unconditional bank guarantee",
                  "declaration": "a bid-securing declaration", "none": ""}
SECURITY_LABEL = {"cpo": "CPO", "guarantee": "Bank guarantee", "declaration": "Bid-securing declaration",
                  "none": "Not required"}
VAT_RATE = 0.15

WORKS_WORDS = r"construct|building|road|bridge|rehabilitat|renovat|maintenance of (?:the )?build|civil work|" \
              r"water (?:supply|well)|borehole|drilling|asphalt|culvert|fence|fencing|plumbing work|finishing work|" \
              r"\bg\+\d|\bworks?\b"
CONSULT_WORDS = r"consult|feasibility|design and supervision|supervision of|baseline|evaluation|assessment|study\b|" \
                r"survey\b|audit services|research|training needs|expression of interest|\beoi\b|\brfp\b"
SERVICE_WORDS = r"cleaning|security guard|security service|catering|printing|event|transport service|insurance|" \
                r"maintenance service|rental|hire of|hotel|advert|courier|travel agency|janitorial|service\b"


def guess_kind(t):
    """Works, goods, services or consultancy, from the board category and the title."""
    text = f"{t.title or ''} {t.notice or ''}"[:600].lower()
    cat = t.category or ""
    if cat == "construction":
        return "works"
    if cat == "consultancy" or re.search(CONSULT_WORDS, (t.title or "").lower()):
        return "consultancy"
    if re.search(WORKS_WORDS, (t.title or "").lower()):
        return "works"
    if cat in ("services", "rental") or re.search(SERVICE_WORDS, (t.title or "").lower()):
        return "services"
    if re.search(r"construction of|building construction", text):
        return "works"
    return "goods"


# -- standard wording. Tokens in {braces}; see tokens() for what each one is.
BID_SHEET = {
    "works": """We, the undersigned, declare that:

(a) We have examined and have no reservations to the bidding document, including the addenda issued.

(b) We offer to execute the works "{title}" in conformity with the bidding document, and to complete them within {completion} of the commencement date.

(c) The total price of our bid, excluding the discounts offered in item (d), is {total_figures} ({total_words}), {vat_note}.

(d) The discounts offered and the method of their application are: {discount}.

(e) Our bid shall be valid for {validity_days} days from the deadline for bid submission, shall remain binding upon us and may be accepted at any time before that period expires.

(f) {security_clause}

(g) If our bid is accepted, we commit to furnish a performance security of {performance_security} of the contract price, in accordance with the bidding document.

(h) We, including any subcontractors or suppliers for any part of the contract, are eligible under the Federal Government Procurement and Property Administration Proclamation and its directives, have no conflict of interest, and are not suspended or debarred by the Public Procurement Authority or any public body.

(i) We are not insolvent, in receivership, bankrupt or being wound up, and we have met our obligations to pay taxes and social security contributions. Our TIN is {tin}.

(j) We have not offered and will not offer any inducement or reward to any public official in connection with this bid, and we accept the fraud and corruption provisions of the bidding document.

(k) We are submitting one bid only, in our own name, and are not a member of a joint venture or a subcontractor in any other bid for this procurement.

(l) We understand that this bid, together with your written acceptance, shall form a binding contract between us until a formal contract is prepared and signed.

(m) We understand that you are not bound to accept the lowest evaluated bid or any other bid you may receive.""",
    "goods": """We, the undersigned, declare that:

(a) We have examined and fully accept, without reservation, the bidding document, including the addenda issued.

(b) We offer to supply, in conformity with the bidding document and the delivery schedule in the Schedule of Requirements, the following goods and related services: "{title}", delivered within {completion}.

(c) The warranty period for the goods offered is {warranty}.

(d) The total price of our bid, excluding the discounts offered in item (e), is {total_figures} ({total_words}), {vat_note}.

(e) The discounts offered and the method of their application are: {discount}.

(f) Our bid shall be valid for {validity_days} days from the deadline for bid submission, shall remain binding upon us and may be accepted at any time before that period expires.

(g) {security_clause}

(h) If our bid is accepted, we commit to furnish a performance security of {performance_security} of the contract price, in accordance with the bidding document.

(i) We are eligible under the Federal Government Procurement and Property Administration Proclamation and its directives, are registered on the suppliers list of the Public Procurement Authority, have no conflict of interest, and are not suspended or debarred by the Authority or any public body.

(j) We are not insolvent, in receivership, bankrupt or being wound up, and we have met our obligations to pay taxes and social security contributions. Our TIN is {tin}.

(k) We have not offered and will not offer any inducement or reward to any public official in connection with this bid, and we accept the fraud and corruption provisions of the bidding document.

(l) We are submitting one bid only, in our own name, and are not a member of a joint venture or a subcontractor in any other bid for this procurement.

(m) We understand that this bid, together with your written acceptance, shall form a binding contract between us until a formal contract is prepared and signed.

(n) We understand that you are not bound to accept the lowest evaluated bid or any other bid you may receive.""",
}
BID_SHEET["services"] = BID_SHEET["goods"].replace(
    'supply, in conformity with the bidding document and the delivery schedule in the Schedule of Requirements, '
    'the following goods and related services: "{title}", delivered within {completion}',
    'provide the following services, in conformity with the bidding document and the activity schedule: '
    '"{title}", over a period of {completion}').replace(
    "(c) The warranty period for the goods offered is {warranty}.\n\n", "").replace("(d) The total", "(c) The total") \
    .replace("item (e)", "item (d)").replace("(e) The discounts", "(d) The discounts").replace("(f) Our bid shall", "(e) Our bid shall") \
    .replace("(g) Our bid is", "(f) Our bid is").replace("(h) If our", "(g) If our").replace("(i) We are eligible", "(h) We are eligible") \
    .replace("(j) We are not", "(i) We are not").replace("(k) We have not", "(j) We have not") \
    .replace("(l) We are submitting", "(k) We are submitting").replace("(m) We understand that this", "(l) We understand that this") \
    .replace("(n) We understand that you", "(m) We understand that you")

TECH_SHEET = """We, the undersigned, submit our technical bid for "{title}" (reference {reference}), in accordance with your bidding document.

Our technical bid, with the documents listed in it, is in this envelope. Our financial bid, including the Bid Submission Sheet and the priced {price_doc}, is submitted in a separate sealed envelope marked "Financial bid".

We confirm that we have examined the bidding document and its addenda and accept it without reservation, that our bid is valid for {validity_days} days from the deadline for bid submission, and that {security_lower}"""

CONSULT_TECH = """We, the undersigned, offer to provide the consulting services for "{title}" in accordance with your Request for Proposals (reference {reference}).

We are hereby submitting our proposal, which includes this technical proposal and a financial proposal sealed in a separate envelope.

We declare that all the information and statements in this proposal are true, and we accept that any misrepresentation may lead to our disqualification. Our proposal is binding upon us for {validity_days} days from the proposal submission deadline, subject to the modifications that may result from contract negotiations.

We have no conflict of interest, we are not suspended or debarred, and we accept the fraud and corruption provisions of the Request for Proposals. We undertake, if our proposal is accepted, to begin the services within {completion} of contract signature and to keep the key experts named in our proposal available.

We understand that you are not bound to accept any proposal you receive."""

CONSULT_FIN = """We, the undersigned, offer to provide the consulting services for "{title}" (reference {reference}) in accordance with your Request for Proposals and our technical proposal.

Our attached financial proposal is for the sum of {total_figures} ({total_words}), {vat_note}.

Our financial proposal shall be binding upon us, subject to the modifications resulting from contract negotiations, for {validity_days} days from the proposal submission deadline.

No commissions or gratuities have been or are to be paid by us to agents or any other party relating to this proposal and contract execution.

We understand that you are not bound to accept any proposal you receive."""

POWER_OF_ATTORNEY = """We, {legal_name}, a company registered in Ethiopia with TIN {tin}, of {address}, hereby authorise {signatory_name}, {signatory_title}, to sign on our behalf the bid for "{title}" (reference {reference}) issued by {buyer}, together with all forms, schedules, correspondence and the contract arising from it, and to represent and bind the company in all matters relating to this procurement.

This authority is valid until the contract is signed or the bid validity period expires."""

SECURING_DECLARATION = """We, the undersigned, declare that:

We understand that, according to your conditions, bids must be supported by a bid-securing declaration.

We accept that we will automatically be suspended from being eligible for bidding in any contract with you, and with any public body, for the period of time determined by the Public Procurement Authority, if we are in breach of our obligations under the bid conditions because we:

(a) have withdrawn our bid during the period of bid validity specified in the Bid Submission Sheet; or

(b) having been notified of the acceptance of our bid during the period of bid validity, (i) fail or refuse to sign the contract, or (ii) fail or refuse to furnish the performance security, in accordance with the bidding document.

We understand this declaration shall expire if we are not the successful bidder, upon the earlier of our receipt of your notification of the name of the successful bidder, or twenty-eight days after the expiration of our bid."""

LITIGATION = """We have no pending litigation or arbitration with any employer or client.

In the last five years, none of our contracts has been terminated for non-performance, and no bid or performance security of ours has been called."""

MANUFACTURER = """We, [name of manufacturer], who are established and reputable manufacturers of [type of goods], having factories at [address of factory], do hereby authorise {legal_name} to submit a bid in response to "{title}" (reference {reference}) issued by {buyer}, the purpose of which is to provide the following goods, manufactured by us: [name and description of goods], and to subsequently negotiate and sign the contract.

We hereby extend our full guarantee and warranty, in accordance with the General Conditions of Contract, with respect to the goods offered by the above firm.

(To be written on the manufacturer's letterhead and signed by the manufacturer.)"""

OUTLINES = {
    "method_works": """1. Understanding of the works
[Scope in your words: what will be built, where, and the main quantities.]

2. Mobilisation and site set-up
Site clearing, setting out, temporary site office, store and fencing, water and power, and the staff and equipment brought to site in the first two weeks.

3. Construction method, by stage
3.1 Excavation and earthworks
3.2 Substructure: foundations, ground beams, hardcore and floor slab
3.3 Superstructure: columns, beams and slabs (formwork, reinforcement, concrete mix and curing)
3.4 Masonry and walling
3.5 Roofing
3.6 Electrical and sanitary installations
3.7 Finishing works: plastering, flooring, painting, doors and windows
3.8 External works and site clean-up

4. Materials and sourcing
Where cement, reinforcement bars, aggregates and finishing materials come from, and how they are stored and tested.

5. Quality control
Material tests, concrete cube tests, inspection and approval by the supervisor before each stage.

6. Health, safety and environment
Personal protective equipment, first aid, site safety rules, waste disposal, dust and noise control.

7. Testing, handover and defects liability""",
    "site_org": """1. Site organisation chart
Project manager, site engineer, office engineer, surveyor, foremen by trade, store keeper, and safety officer, and who reports to whom.

2. Head office support
Who at head office follows the project, approves purchases and pays the site.

3. Working hours and shifts

4. Communication with the employer and the supervisor
Weekly site meetings, monthly progress reports, site diary.""",
    "method_services": """1. Understanding of the assignment
2. How we will deliver the services
3. Staffing and supervision
4. Equipment and materials we will use
5. Work plan and schedule
6. Quality control and reporting""",
    "methodology": """1. Understanding of the objectives and scope
2. Technical approach and methodology
3. Work plan: activities, outputs and deliverables
4. Organisation and staffing
5. Quality assurance and reporting""",
    "tor_comments": """We have reviewed the Terms of Reference and propose the following:

1. [Comment or improvement]
2. [Comment or improvement]""",
    "warranty": """1. Warranty period and what it covers
2. After-sales service: where, how fast we respond, and who to call
3. Spare parts availability
4. Installation, testing and training""",
}

WORKS_PROGRAMME = ["Mobilisation and site set-up", "Site clearing and setting out", "Excavation and earthworks",
                   "Substructure (foundations)", "Superstructure (frame and slabs)", "Masonry and walling", "Roofing",
                   "Electrical and sanitary installations", "Finishing works", "External works",
                   "Testing, cleaning and handover"]
FIN_DATA = ["Total assets", "Total liabilities", "Net worth", "Current assets", "Current liabilities", "Working capital",
            "Total revenue", "Profit before tax", "Available line of credit or cash"]


def F(key, title, type, part="T", sign=True, ref="", hint="", cols=None, outline="", field="", optional=False):
    return {"key": key, "title": title, "type": type, "part": part, "sign": sign, "ref": ref, "hint": hint,
            "cols": cols or [], "outline": outline, "field": field, "optional": optional}


COL = {"no": "No.", "name": "Name of contract", "client": "Employer or client", "value_etb": "Value, ETB",
       "year_completed": "Year completed", "role": "Role", "scope": "Nature of the work", "year": "Year",
       "amount_etb": "Turnover, ETB", "position": "Position", "person": "Name", "qualification": "Qualification",
       "years": "Total years", "similar_years": "Years in similar work", "item": "Equipment type and capacity",
       "qty": "Number", "ownership": "Owned, leased or to buy", "year_made": "Year made", "remaining_etb": "Remaining value, ETB",
       "completion": "Expected completion", "activity": "Activity", "start": "Start (week)", "weeks": "Duration (weeks)",
       "work": "Part of the works", "sub": "Subcontractor", "share": "Share, %", "line": "Item",
       "y1": "Year before last", "y2": "Last year", "y3": "This year", "requirement": "Minimum requirement",
       "offered": "Specification offered", "remark": "Compliance remark", "place": "Place of delivery",
       "days": "Delivery (days)", "task": "Tasks assigned", "months": "Months"}

FORMS = {f["key"]: f for f in [
    F("details", "Bid details", "details", part="", sign=False,
      hint="These figures go into every form: the price, validity, completion period and bid security."),
    F("cover_letter", "Covering letter", "text", part="T", field="cover_letter", optional=True,
      hint="A short letter on your letterhead that goes on top of the envelope's documents.", ref="Usual practice"),
    F("tech_sheet", "Technical bid submission letter", "letter", part="T",
      ref="Two-envelope bids", hint="Opens the technical envelope when technical and financial bids are separate."),
    F("consult_tech", "Technical proposal submission form", "letter", part="T", ref="RFP form TECH-1"),
    F("bid_sheet", "Bid Submission Sheet", "letter", part="F", ref="PPA SBD Section 4, form A (Letter of Bid)",
      hint="The buyer's own form wins. If their wording differs, paste it here and keep the {tokens} you need."),
    F("consult_fin", "Financial proposal submission form", "letter", part="F", ref="RFP form FIN-1"),
    F("power_of_attorney", "Power of attorney", "letter", part="T", ref="PPA SBD ITB: documents comprising the bid",
      hint="Needed when someone other than the owner or General Manager signs. Usually authenticated at DARS (Documents Authentication and Registration Service)."),
    F("bidder_info", "Bidder information", "profile", part="T", ref="PPA SBD Section 4, form C.1 and C.9",
      hint="From your company profile. Edit it there and every bid updates."),
    F("bid_security", "Bid security", "security", part="T", sign=False, ref="PPA SBD Section 4, form E",
      hint="The bank issues the CPO or guarantee. Put the original in the envelope; a copy is not accepted."),
    F("papers", "Eligibility documents", "papers", part="T", sign=False, ref="PPA SBD ITB: documents comprising the bid",
      hint="Copies from your company file, checked against the closing date."),
    F("experience", "Similar experience", "table", part="T", ref="PPA SBD form C.3; works form EXP-4.2",
      cols=["name", "client", "value_etb", "year_completed", "role", "scope"],
      hint="Attach completion or good-performance letters for each contract listed."),
    F("assignments", "Firm's experience: similar assignments", "table", part="T", ref="RFP form TECH-2",
      cols=["name", "client", "value_etb", "year_completed", "role", "scope"]),
    F("turnover", "Average annual turnover", "table", part="T", ref="PPA SBD form C.2; works form FIN-3.2",
      cols=["year", "amount_etb"], hint="Usually the last three or five years, from audited accounts."),
    F("financial_data", "Financial situation", "table", part="T", ref="PPA SBD form C.2; works form FIN-3.1",
      cols=["line", "y1", "y2", "y3"], optional=True, hint="From your audited balance sheets. Attach the audit reports."),
    F("commitments", "Current contract commitments", "table", part="T", ref="Works qualification form CCC",
      cols=["name", "client", "value_etb", "remaining_etb", "completion"], optional=True,
      hint="Works you have running now. Write 'None' in the first row if you have none."),
    F("litigation", "Litigation and contract performance", "letter", part="T", ref="Works form CON-2", optional=True),
    F("key_staff", "Key personnel", "table", part="T", ref="PPA SBD form C.4; works Key Personnel Schedule",
      cols=["position", "person", "qualification", "years", "similar_years"],
      hint="Attach a signed CV, degree and professional licence copy for each person."),
    F("team", "Team composition and tasks", "table", part="T", ref="RFP form TECH-5",
      cols=["person", "position", "task", "months"], hint="Attach each expert's signed CV (TECH-6)."),
    F("equipment", "Equipment", "table", part="T", ref="PPA SBD form C.6; works Key Equipment Schedule",
      cols=["item", "qty", "ownership", "year_made"],
      hint="Attach ownership papers (libre) or rental agreements for each item."),
    F("site_org", "Site organisation", "text", part="T", outline="site_org", ref="Works technical proposal"),
    F("method", "Method statement", "text", part="T", field="technical", outline="method_works",
      ref="Works technical proposal: work methods", hint="How you will build it. Bid Room drafts this from the tender and your profile."),
    F("method_services", "Methodology and work plan", "text", part="T", field="technical", outline="method_services",
      ref="PPA SBD technical offer"),
    F("methodology", "Approach, methodology and work plan", "text", part="T", field="technical", outline="methodology",
      ref="RFP form TECH-4"),
    F("technical_offer", "Technical offer", "text", part="T", field="technical", outline="warranty",
      ref="PPA SBD ITB: technical offer", hint="Brand and model offered, delivery, warranty, after-sales service."),
    F("tor_comments", "Comments on the Terms of Reference", "text", part="T", outline="tor_comments",
      ref="RFP form TECH-3", optional=True),
    F("compliance", "Technical specification compliance sheet", "table", part="T", ref="PPA SBD Section 6, form B",
      cols=["no", "requirement", "offered", "remark"],
      hint="Copy each line of the buyer's specification, and say what you offer against it."),
    F("delivery", "Delivery schedule", "table", part="T", ref="PPA SBD Section 6: schedule of requirements",
      cols=["line", "qty", "place", "days"], optional=True),
    F("manufacturer", "Manufacturer's authorisation", "letter", part="T", sign=False, ref="PPA SBD Section 4, form F",
      optional=True, hint="Only when the buyer asks for it. The manufacturer signs it on their letterhead."),
    F("work_programme", "Work programme", "table", part="T", ref="Works technical proposal: work schedule",
      cols=["activity", "start", "weeks"], hint="Weeks from the commencement date. Attach a bar chart if the buyer asks for one."),
    F("subcontractors", "Proposed subcontractors", "table", part="T", ref="Works ITB: subcontracting",
      cols=["work", "sub", "share"], optional=True),
    F("company_profile", "Company profile", "text", part="T", field="profile_section", optional=True,
      ref="Usual practice", hint="Who you are and why you suit this contract."),
    F("boq", "Bill of quantities", "price", part="F", ref="Works Bill of Quantities (Item, Description, Unit, Quantity, Rate, Amount)",
      hint="Copy the buyer's BoQ item by item. Rates exclude VAT; VAT is added in the summary."),
    F("price_schedule", "Price schedule", "price", part="F", ref="PPA SBD Section 4, form B",
      hint="One line per item in the buyer's list of goods. Unit prices exclude VAT; VAT is added in the summary."),
    F("activity_schedule", "Price schedule", "price", part="F", ref="PPA SBD Section 4, form B (activity schedule)"),
    F("cost_summary", "Summary of costs", "price", part="F", ref="RFP forms FIN-2 to FIN-4",
      hint="Remuneration per expert (person-months by rate) and reimbursable expenses, one per line."),
]}

SETS = {
    "works": ["details", "cover_letter", "tech_sheet", "power_of_attorney", "bidder_info", "bid_security", "papers",
              "experience", "turnover", "financial_data", "commitments", "litigation", "key_staff", "equipment",
              "site_org", "method", "work_programme", "subcontractors", "company_profile", "bid_sheet", "boq"],
    "goods": ["details", "cover_letter", "tech_sheet", "power_of_attorney", "bidder_info", "bid_security", "papers",
              "experience", "turnover", "financial_data", "compliance", "technical_offer", "delivery", "manufacturer",
              "company_profile", "bid_sheet", "price_schedule"],
    "services": ["details", "cover_letter", "tech_sheet", "power_of_attorney", "bidder_info", "bid_security", "papers",
                 "experience", "turnover", "key_staff", "equipment", "method_services", "company_profile",
                 "bid_sheet", "activity_schedule"],
    "consultancy": ["details", "consult_tech", "power_of_attorney", "bidder_info", "papers", "assignments", "turnover",
                    "tor_comments", "methodology", "team", "company_profile", "consult_fin", "cost_summary"],
}
# off unless the firm switches them on
DEFAULT_OFF = {"cover_letter", "financial_data", "commitments", "subcontractors", "manufacturer", "delivery",
               "company_profile", "tor_comments"}


def details_blank(t, kind):
    sec = (t.bid_security or "").strip()
    return {"validity_days": guess_validity(t), "completion": "", "warranty": "", "discount": "",
            "security_type": guess_security(t, kind), "security_amount": security_amount(sec), "security_bank": "",
            "security_number": "", "security_date": "", "performance_security": "10%", "envelopes": guess_envelopes(t, kind),
            "sign_place": "Addis Ababa", "vat": "add"}  # vat: add (prices exclude VAT), incl, none


def guess_validity(t):
    m = re.search(r"valid(?:ity)?[^.\d]{0,40}?(\d{2,3})\s*(?:calendar\s*)?days", t.notice or "", re.I)
    return m.group(1) if m else "90"


def guess_security(t, kind):
    s = f"{t.bid_security or ''} {t.notice or ''}".lower()
    if kind == "consultancy" and not (t.bid_security or "").strip():
        return "none"
    if "declaration" in s:
        return "declaration"
    if "guarantee" in s and "cpo" not in s:
        return "guarantee"
    return "cpo" if (t.bid_security or "").strip() or "cpo" in s else "none"


def security_amount(s):
    m = re.search(r"(?:etb|birr|br\.?)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:etb|birr|br\b)", s or "", re.I)
    if m:
        return f"ETB {m.group(1) or m.group(2)}"
    m = re.search(r"\d+(?:\.\d+)?\s*%", s or "")
    return m.group(0).replace(" ", "") + " of the bid price" if m else (s or "")[:60]


def guess_envelopes(t, kind):
    s = (t.notice or "").lower()
    if kind == "consultancy" or re.search(r"two[- ]envelope|separate(?:ly)? (?:sealed )?envelope|technical and financial (?:proposals?|offers?|bids?) (?:shall|should|must) be", s):
        return "two"
    return "one"


def form_list(d):
    """The forms of this bid, in print order, each with its state in the bid."""
    kind = d.get("kind") or "goods"
    det = d.get("details") or {}
    two = det.get("envelopes") == "two"
    out = []
    for k in SETS.get(kind, SETS["goods"]):
        f = FORMS[k]
        if k == "tech_sheet" and not two:
            continue
        st = (d.get("forms") or {}).get(k) or {}
        default = k not in DEFAULT_OFF or bool(f["field"] and (d.get(f["field"]) or "").strip())  # a written section prints
        on = st.get("on", default)
        if k == "bid_security" and det.get("security_type") == "none":
            on = False
        out.append(dict(f, on=on))
    return out


def tokens(t, p, d, total, today, papers=None):
    """Values for the {tokens} in letters. Empty ones come back as None and print as [TO CONFIRM]."""
    det = d.get("details") or {}
    vat_mode = det.get("vat", "add")
    grand = total * (1 + VAT_RATE) if vat_mode == "add" and p.get("vat_registered") else total
    if not total:
        figs = words = None
    else:
        figs, words = f"ETB {grand:,.2f}", amount_words(grand)
    if vat_mode == "add" and p.get("vat_registered"):
        vat_note = f"including VAT of 15% (ETB {total * VAT_RATE:,.2f} on a price of ETB {total:,.2f} before VAT)"
    elif vat_mode == "incl":
        vat_note = "including VAT"
    else:
        vat_note = "VAT not applicable" if not p.get("vat_registered") else "excluding VAT"
    stype = det.get("security_type") or "none"
    bank = (det.get("security_bank") or "").strip()
    num = (det.get("security_number") or "").strip()
    issuer = (f", issued by {bank}" if bank else "") + (f", number {num}" if num else "")
    v = {
        "buyer": t.buyer, "buyer_address": t.place, "reference": t.reference or None, "title": t.title,
        "date": long(today), "closing": long(t.closes) + (f" at {t.time}" if t.time else "") if t.closes else None,
        "legal_name": p.get("legal_name"), "address": p.get("address"), "phone": p.get("phone"),
        "email": p.get("email"), "tin": p.get("tin"), "vat_number": p.get("vat_number"),
        "signatory_name": p.get("signatory_name"), "signatory_title": p.get("signatory_title"),
        "grade": p.get("grade"), "total_figures": figs, "total_words": words,
        "price_before_vat": f"ETB {total:,.2f}" if total else None, "vat_note": vat_note,
        "validity_days": det.get("validity_days"), "completion": det.get("completion"),
        "warranty": det.get("warranty"), "discount": det.get("discount") or "none",
        "security_amount": det.get("security_amount"), "security_clause": security_clause(stype, det.get("security_amount"), issuer),
        "performance_security": det.get("performance_security"),
        "price_doc": {"works": "Bill of Quantities", "consultancy": "cost summary"}.get(d.get("kind"), "price schedule"),
    }
    v["security_lower"] = v["security_clause"][0].lower() + v["security_clause"][1:] if v["security_clause"] else ""
    return v


def security_clause(stype, amount, issuer):
    if stype == "declaration":
        return "Our bid is accompanied by a signed bid-securing declaration."
    if stype in ("cpo", "guarantee"):
        amt = (amount or "").strip() or "[TO CONFIRM: bid security amount]"
        return f"Our bid is accompanied by a bid security of {amt}, in the form of {SECURITY_TYPES[stype]}{issuer}."
    return "No bid security is required for this procurement."


LABELS = {"buyer": "buyer", "reference": "procurement reference", "title": "tender title", "legal_name": "company name",
          "address": "company address", "tin": "TIN", "signatory_name": "name of the person signing",
          "signatory_title": "title of the person signing", "total_figures": "bid price (fill the price schedule)",
          "total_words": "bid price in words", "validity_days": "bid validity in days",
          "completion": "completion or delivery period", "warranty": "warranty period",
          "security_amount": "bid security amount", "performance_security": "performance security",
          "closing": "closing date", "vat_number": "VAT number", "phone": "phone", "email": "email"}


def render(text, values):
    """Fill {tokens}; a missing value becomes [TO CONFIRM: label] so the bid lists it."""
    def sub(m):
        k = m.group(1)
        if k not in values:
            return m.group(0)
        val = values[k]
        if val is None or str(val).strip() == "":
            return f"[TO CONFIRM: {LABELS.get(k, k.replace('_', ' '))}]"
        return str(val)
    return re.sub(r"\{([a-z_]+)\}", sub, text or "")


def standard_text(key, kind):
    return {"bid_sheet": BID_SHEET.get(kind, BID_SHEET["goods"]), "tech_sheet": TECH_SHEET, "consult_tech": CONSULT_TECH,
            "consult_fin": CONSULT_FIN, "power_of_attorney": POWER_OF_ATTORNEY, "litigation": LITIGATION,
            "manufacturer": MANUFACTURER, "securing_declaration": SECURING_DECLARATION}.get(key, "")


def letter_text(d, key):
    st = (d.get("forms") or {}).get(key) or {}
    return st.get("text") if (st.get("text") or "").strip() else standard_text(key, d.get("kind"))


def prefill_rows(key, p, d):
    """Rows for a table form, from the company profile (or a starting outline)."""
    chosen = set(d.get("projects") or [])
    projects = [x for i, x in enumerate(p.get("projects") or []) if not chosen or f"p{i + 1}" in chosen]
    if key in ("experience", "assignments"):
        return [{c: x.get(c, "") for c in FORMS[key]["cols"]} for x in projects]
    if key in ("key_staff", "team"):
        return [{"position": x.get("role", ""), "person": x.get("name", ""), "qualification": x.get("qualification", ""),
                 "years": x.get("years_experience", ""), "similar_years": x.get("similar_years", "")}
                for x in p.get("key_personnel") or []]
    if key == "equipment":
        rows = [{"item": x.get("item", ""), "qty": x.get("qty", ""), "ownership": x.get("ownership", ""),
                 "year_made": x.get("year_made", "")} for x in p.get("equipment_items") or []]
        if not rows:
            rows = [{"item": x, "qty": "", "ownership": "Owned", "year_made": ""}
                    for x in re.split(r"[,\n]", p.get("equipment") or "") if x.strip()]
        return rows
    if key == "turnover":
        rows = []
        for line in (p.get("turnover") or "").splitlines():
            m = re.match(r"\s*(\d{4})\s*(?:E\.?C\.?)?\s*[:=-]\s*(.+)", line)
            if m:
                rows.append({"year": m.group(1), "amount_etb": m.group(2).strip()})
        return rows
    if key == "financial_data":
        return [{"line": x, "y1": "", "y2": "", "y3": ""} for x in FIN_DATA]
    if key == "work_programme":
        return [{"activity": x, "start": "", "weeks": ""} for x in WORKS_PROGRAMME]
    if key == "compliance":
        return [{"no": str(i + 1), "requirement": r.get("item", ""), "offered": "", "remark": ""}
                for i, r in enumerate(d.get("financial") or [])]
    if key == "delivery":
        return [{"line": r.get("item", ""), "qty": r.get("qty", ""), "place": "", "days": ""} for r in d.get("financial") or []]
    return []


def table_rows(d, key, p):
    st = (d.get("forms") or {}).get(key) or {}
    if "rows" in st:
        return st["rows"]
    return prefill_rows(key, p, d)


def form_done(f, d, p, values, env):
    """True when a form has what it needs."""
    k, typ = f["key"], f["type"]
    if typ == "letter":
        return "[TO CONFIRM" not in render(letter_text(d, k), values)
    if typ == "text":
        text = (d.get(f["field"]) if f["field"] else ((d.get("forms") or {}).get(k) or {}).get("text")) or ""
        return bool(text.strip()) and text.strip() != OUTLINES.get(f["outline"], "").strip()  # an untouched outline isn't done
    if typ == "table":
        return any(sum(1 for v in r.values() if str(v).strip()) >= 2 for r in table_rows(d, k, p))
    if typ == "price":
        return bool(d.get("financial"))
    if typ == "security":
        det = d.get("details") or {}
        return bool(det.get("security_amount") and (det.get("security_type") == "declaration" or det.get("security_number")))
    if typ == "papers":
        return env is not None and not [r for r in env["items"] if r.get("from") == "file" and r["state"] != "in"]
    if typ == "profile":
        return bool(p.get("legal_name") and p.get("tin") and p.get("address") and p.get("signatory_name"))
    if typ == "details":
        det = d.get("details") or {}
        return bool(det.get("validity_days") and det.get("completion"))
    return False


def clean_forms(incoming, kind):
    """What the browser sends for forms: {key: {on, text, rows}}. Only known forms and columns, capped."""
    out = {}
    if not isinstance(incoming, dict):
        return out
    for k, st in list(incoming.items())[:60]:
        if k not in FORMS or not isinstance(st, dict):
            continue
        f, o = FORMS[k], {}
        if "on" in st:
            o["on"] = bool(st["on"])
        if "text" in st and f["type"] in ("letter", "text") and not f["field"]:
            o["text"] = str(st["text"])[:20000]
        if isinstance(st.get("rows"), list) and f["type"] == "table":
            rows = []
            for r in st["rows"][:80]:
                if isinstance(r, dict):
                    row = {c: str(r.get(c, ""))[:500] for c in f["cols"]}
                    if any(v.strip() for v in row.values()):
                        rows.append(row)
            o["rows"] = rows
        out[k] = o
    return out


DETAIL_KEYS = ("validity_days", "completion", "warranty", "discount", "security_type", "security_amount", "security_bank",
               "security_number", "security_date", "performance_security", "envelopes", "sign_place", "vat")


def clean_details(incoming):
    out = {}
    if not isinstance(incoming, dict):
        return out
    for k in DETAIL_KEYS:
        if k in incoming:
            out[k] = str(incoming[k])[:200].strip()
    if out.get("security_type") not in (None, *SECURITY_TYPES):
        out.pop("security_type")
    if out.get("envelopes") not in (None, "one", "two"):
        out.pop("envelopes")
    if out.get("vat") not in (None, "add", "incl", "none"):
        out.pop("vat")
    if "validity_days" in out:
        out["validity_days"] = re.sub(r"\D", "", out["validity_days"])[:3]
    return out


def security_valid_until(t, det):
    """Bid security must stay valid 28 days past bid validity (PPA SBD ITB)."""
    try:
        return (dt.date.fromisoformat(t.closes) + dt.timedelta(days=int(det.get("validity_days") or 0) + 28)).isoformat()
    except (TypeError, ValueError):
        return ""


def long(iso):
    try:
        d = dt.date.fromisoformat(str(iso))
    except ValueError:
        return str(iso or "")
    return f"{d.day} {d.strftime('%B %Y')}"


ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _words(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return ONES[n // 100] + " hundred" + (" " + _words(n % 100) if n % 100 else "")
    for size, name in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= size:
            return _words(n // size) + " " + name + (" " + _words(n % size) if n % size else "")
    return ""


def amount_words(x):
    """ETB 1,234,567.50 -> 'One million two hundred thirty-four thousand five hundred sixty-seven Birr and fifty cents'."""
    cents = round(x * 100)
    birr, cents = divmod(cents, 100)
    s = (_words(birr) or "zero") + " Birr"
    if cents:
        s += " and " + _words(cents) + " cents"
    return s[0].upper() + s[1:]
