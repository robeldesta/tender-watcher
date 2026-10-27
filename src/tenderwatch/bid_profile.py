"""A firm's bid profile: the facts a bid pack is built from.

Filled in from the 15-minute onboarding call (grade, licences, regions, past
projects) plus the details every Ethiopian bid cover page asks for. Everything
here comes from the firm, so it is trusted. Facts that go into tables
(contract values, dates, licence numbers) are rendered straight from this data
and never pass through the model.
"""
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml


@dataclass
class Licence:
    name: str                      # e.g. "MoUD General Contractor (GC) Grade 3"
    number: str | None = None
    issuer: str | None = None
    expires: date | None = None    # renewal date; checked against the bid deadline


@dataclass
class PastProject:
    id: str                        # short handle, e.g. "p1"; the model refers to projects by id
    name: str
    client: str
    value_etb: float | None = None
    year_completed: int | None = None  # None = ongoing
    region: str | None = None
    role: str = "Main contractor"  # or "Subcontractor", "JV partner (60%)"
    scope: str = ""
    completion_certificate: bool = False


@dataclass
class KeyPerson:
    role: str                      # e.g. "Site engineer"
    name: str
    qualification: str = ""
    years_experience: int | None = None


@dataclass
class BidProfile:
    legal_name: str
    grade: str                     # e.g. "GC Grade 3"
    address: str = ""
    phone: str = ""
    email: str = ""
    tin: str | None = None
    vat_registered: bool = False
    year_established: int | None = None
    signatory_name: str = ""
    signatory_title: str = ""
    regions: list[str] = field(default_factory=list)
    work_types: list[str] = field(default_factory=list)  # e.g. roads, buildings, water supply
    staff_count: int | None = None
    annual_turnover_etb: dict[int, float] = field(default_factory=dict)  # year -> ETB
    licences: list[Licence] = field(default_factory=list)
    projects: list[PastProject] = field(default_factory=list)
    key_personnel: list[KeyPerson] = field(default_factory=list)
    equipment: list[str] = field(default_factory=list)
    notes: str = ""                # anything else from the call worth telling the model


def _date(v) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def from_dict(d: dict) -> BidProfile:
    d = dict(d)
    d["licences"] = [Licence(**{**x, "expires": _date(x.get("expires"))}) for x in d.get("licences", [])]
    d["projects"] = [PastProject(**{**x, "id": str(x["id"])}) for x in d.get("projects", [])]
    d["key_personnel"] = [KeyPerson(**x) for x in d.get("key_personnel", [])]
    d["annual_turnover_etb"] = {int(k): float(v) for k, v in (d.get("annual_turnover_etb") or {}).items()}
    p = BidProfile(**d)
    ids = [x.id for x in p.projects]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{p.legal_name}: past project ids must be unique, got {ids}")
    return p


def load_profiles(path: str | Path) -> dict[str, BidProfile]:
    """YAML file with a top-level `firms:` list -> {legal_name: BidProfile}."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    return {p.legal_name: p for p in map(from_dict, data.get("firms", []))}


def etb(v: float | None) -> str:
    return "–" if v is None else f"ETB {v:,.0f}"


def _cell(s) -> str:
    return str(s).replace("|", "/").replace("\n", " ")


def experience_table(projects: list[PastProject]) -> str:
    """Markdown table in the column order most Ethiopian bidding documents use."""
    rows = ["| # | Project | Client | Role | Value | Completed | Region | Completion certificate |",
            "|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(projects, 1):
        rows.append("| " + " | ".join(_cell(x) for x in [
            i, p.name, p.client, p.role, etb(p.value_etb),
            p.year_completed or "Ongoing", p.region or "–",
            "Yes" if p.completion_certificate else "To attach",
        ]) + " |")
    return "\n".join(rows)


def turnover_table(turnover: dict[int, float]) -> str:
    if not turnover:
        return "_No turnover figures in the profile. Add audited figures before submitting._"
    rows = ["| Year | Annual turnover |", "|---|---|"]
    rows += [f"| {y} | {etb(v)} |" for y, v in sorted(turnover.items(), reverse=True)]
    if len(turnover) > 1:
        avg = sum(turnover.values()) / len(turnover)
        rows.append(f"| Average ({len(turnover)} years) | {etb(avg)} |")
    return "\n".join(rows)


def personnel_table(people: list[KeyPerson]) -> str:
    if not people:
        return "_No key personnel in the profile._"
    rows = ["| Role | Name | Qualification | Years of experience |", "|---|---|---|---|"]
    rows += ["| " + " | ".join(_cell(x) for x in [k.role, k.name, k.qualification or "–",
                                                   k.years_experience if k.years_experience is not None else "–"]) + " |"
             for k in people]
    return "\n".join(rows)


def licence_table(licences: list[Licence]) -> str:
    if not licences:
        return "_No licences in the profile._"
    rows = ["| Licence | Number | Issued by | Valid until |", "|---|---|---|---|"]
    rows += ["| " + " | ".join(_cell(x) for x in [l.name, l.number or "–", l.issuer or "–",
                                                   l.expires.isoformat() if l.expires else "–"]) + " |"
             for l in licences]
    return "\n".join(rows)


def facts(p: BidProfile) -> str:
    """The profile as plain text for the model prompt."""
    lines = [
        f"Legal name: {p.legal_name}",
        f"Contractor grade: {p.grade}",
        f"Established: {p.year_established or 'not stated'}",
        f"Address: {p.address or 'not stated'}",
        f"TIN: {p.tin or 'not stated'}; VAT registered: {'yes' if p.vat_registered else 'no'}",
        f"Authorised signatory: {p.signatory_name or 'not stated'}, {p.signatory_title or ''}".rstrip(", "),
        f"Regions worked in: {', '.join(p.regions) or 'not stated'}",
        f"Types of work: {', '.join(p.work_types) or 'not stated'}",
        f"Permanent staff: {p.staff_count if p.staff_count is not None else 'not stated'}",
        "Annual turnover: " + ("; ".join(f"{y}: {etb(v)}" for y, v in sorted(p.annual_turnover_etb.items()))
                               or "not stated"),
        "Licences: " + ("; ".join(
            f"{l.name} (no. {l.number or '?'}, valid until {l.expires or '?'})" for l in p.licences) or "none listed"),
        "Key personnel: " + ("; ".join(
            f"{k.role}: {k.name}, {k.qualification}, {k.years_experience or '?'} yrs" for k in p.key_personnel)
                             or "none listed"),
        "Equipment: " + ("; ".join(p.equipment) or "none listed"),
        "Past projects:",
    ]
    for x in p.projects:
        lines.append(f"  [{x.id}] {x.name}; client {x.client}; {etb(x.value_etb)}; "
                     f"{'completed ' + str(x.year_completed) if x.year_completed else 'ongoing'}; "
                     f"{x.region or 'region n/a'}; {x.role}; scope: {x.scope or 'n/a'}; "
                     f"completion certificate: {'yes' if x.completion_certificate else 'no'}")
    if p.notes:
        lines.append(f"Other notes: {p.notes}")
    return "\n".join(lines)
