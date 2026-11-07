#!/usr/bin/env python3
"""Daily refresh of the Tender Watcher board from Modern Tenders.

Reads the public listing pages and the public teaser of each tender page
(robots.txt allows /tenders/*; the gated part of a page is never requested
or bypassed). Keeps construction works from the construction listings, plus ICT and
electrical/power tenders, merges into tenders.json and
writes it atomically.

Usage: python3 refresh.py [--out /opt/tenders/site/tenders.json] [--dry-run]
"""
import argparse
import datetime as dt
import fcntl
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASE = "https://moderntenders.com"
UA = "TenderWatcher/0.1 (+https://tenders.et)"
DELAY = 2.0  # seconds between requests, minimum
TZ = ZoneInfo("Africa/Addis_Ababa")
STALE_DAYS = 21

# listing path -> (grade implied by that listing ("" = none), board category,
# max pages to read). Construction listings are filtered to real works; other
# listings take their category as given. Listings are newest first, so the
# busy construction listings only need page 1 on a daily run.
LISTINGS = [
    ("/tenders/industry/construction", "", "construction", 1),
    ("/tenders/competency/general-contractor-grade-3", "GC-3", "construction", 1),
    ("/tenders/competency/general-contractor-grade-5", "GC-5", "construction", 1),
    ("/tenders/competency/building-contractor-grade-5", "BC-5", "construction", 1),
    ("/tenders/industry/ict-technology", "", "it", 5),
    ("/tenders/industry/electrical-power", "", "electrical", 5),
    ("/tenders/industry/consultancy-services", "", "consultancy", 5),
    ("/tenders/industry/hospitality-tourism", "", "services", 3),
    ("/tenders/industry/printing-publishing", "", "services", 3),
    ("/tenders/industry/miscellaneous", "", "other", 3),
    ("/tenders/industry/spare-parts-vehicles", "", "vehicles", 3),
    ("/tenders/industry/industrial-machinery", "", "vehicles", 3),
]

# Construction works signals (English + Amharic).
WORKS = re.compile(
    r"construct|building works?|civil works?|rehabilitat|renovat|maintenance|repair|"
    r"upgrad|expansion|road|bridge|culvert|drainage|fence|fencing|pavement|asphalt|"
    r"cobble|water supply|borehole|irrigation|dam\b|latrine|toilet|block|classroom|"
    r"contractor|\bGC\b|\bBC\b|\bRC\b|grade\s*\d|"
    r"ግንባታ|ግንባ|ጥገና|እድሳት|ዕድሳት|ማስፋፊያ|ኮብል|ድልድይ|መንገድ|አጥር|ሕንፃ|ህንፃ|ሥራ\s*ተቋራጭ|ስራ\s*ተቋራጭ|ijaarsa|haaromsa|suphaa",
    re.I,
)
# Never construction works: materials, auctions/sales, rentals, consultancy, purchases.
HARD_EXCLUDE = re.compile(
    r"materials?\b|supply of|auction|for sale|\bsale\b|\bsell|disposal|"
    r"\brent\b|rental|lease|leasing|for hire|hiring of|consult|design and supervision|"
    r"supervision service|"
    r"ኪራይ|ሊዝ|ለመከራየት|ማከራየት|ሽያጭ|ለመሸጥ|ሐራጅ|ሀራጅ|አማካሪ|ግዢ|ግዥ|አቅርቦት",
    re.I,
)
# Usually not works, unless the text clearly says construction or repair work.
SOFT_EXCLUDE = re.compile(
    r"procurement of (?!.*(construct|maintenance|repair|works))|goods|furniture|"
    r"equipment|vehicle|stationer|feasibility|study\b|audit|training|ዕቃ|እቃ",
    re.I,
)
RESCUE = re.compile(r"construction of|construction works?|civil works?|maintenance works?|repair works?|ግንባታ|ጥገና|ለማሰራት|ለማሠራት", re.I)
NGO = re.compile(
    r"\bNGO\b|international|foundation|\bcare\b|save the children|world vision|"
    r"m[ée]decins|msf|oxfam|unicef|undp|unhcr|\bwfp\b|\bfao\b|\bwho\b|\bunfpa\b|"
    r"mercy corps|plan international|\birc\b|\bnrc\b|islamic relief|caritas|"
    r"catholic relief|\bcrs\b|charit|association|church|mission|relief|aid\b",
    re.I,
)
DONOR = re.compile(r"world bank|african development bank|\bAfDB\b|\bUSAID\b|\bGIZ\b|\bEU\b|european union|embassy", re.I)
GOV = re.compile(
    r"ministry|bureau|office|administration|authority|agency|commission|"
    r"municipality|city|woreda|zone|regional|federal|university|college|school|"
    r"hospital|utility|corporation|enterprise|ethiopian|"
    r"ሚኒስቴር|ቢሮ|ጽ/ቤት|ጽሕፈት|ጽህፈት|አስተዳደር|ባለስልጣን|ኤጀንሲ|ኮሚሽን|ማዘጋጃ|ከተማ|ወረዳ|ዞን|"
    r"ክልል|ፌዴራል|ዩኒቨርሲቲ|ኮሌጅ|ትምህርት|ሆስፒታል|ኮርፖሬሽን|ድርጅት|ኢትዮጵያ|"
    r"waajjir|waajir|aanaa|bulchiinsa|godina|mana murtii|magaalaa|biiroo|mootummaa",
    re.I,
)
GRADE_RE = [
    (re.compile(r"\b(GC|BC|RC|WWC|ECC)\s*[-/]?\s*(\d{1,2})\b", re.I), None),
    (re.compile(r"general\s+contractors?\D{0,30}?grade\s*(\d{1,2})", re.I), "GC"),
    (re.compile(r"building\s+contractors?\D{0,30}?grade\s*(\d{1,2})", re.I), "BC"),
    (re.compile(r"road\s+contractors?\D{0,30}?grade\s*(\d{1,2})", re.I), "RC"),
]
CODE_RE = re.compile(r"/tenders/([a-z0-9]{10})-")
CHECK_DAYS = 183  # a closing date further out than this is almost always a typo in the source


def check_reasons(closes, time_, today):
    """Why a closing date/time looks wrong; empty when it looks fine. Dates are never changed."""
    why = []
    if closes:
        try:
            days = (dt.date.fromisoformat(closes) - today).days
        except ValueError:
            days = None
        if days is None or days > CHECK_DAYS:
            why.append("closing date more than 6 months away")
    if time_ and not ("06:00" <= time_ <= "18:00"):
        why.append("closing time outside 06:00-18:00 (Ethiopian clock?)")
    return why


def flag_checks(tenders, today):
    for t in tenders:
        why = check_reasons(t.get("closes", ""), t.get("time", ""), today)
        t.pop("check", None)
        t.pop("check_why", None)
        if why:
            t["check"], t["check_why"] = True, "; ".join(why)


_last = [0.0]


def fetch(path_or_url):
    url = path_or_url if path_or_url.startswith("http") else BASE + path_or_url
    parts = urllib.parse.urlsplit(url)
    url = urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, urllib.parse.quote(parts.path, safe="/-_.~%"), parts.query, "")
    )
    wait = DELAY - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    finally:
        _last[0] = time.monotonic()


WB_API = ("https://search.worldbank.org/api/v2/procnotices?format=json&rows=200"
          "&project_ctry_name_exact=Ethiopia&srt=submission_date&order=desc")
WB_GROUPS = {"CW": "construction", "GO": "goods", "CS": "consultancy", "NC": "services"}
WB_SKIP = {"contract award", "general procurement notice"}


def worldbank(payload, today, existing_ids):
    """World Bank-financed tenders in Ethiopia, with the full notice text kept on our own page."""
    out = []
    for n in payload.get("procnotices", []):
        if (n.get("notice_type") or "").strip().lower() in WB_SKIP or not n.get("id"):
            continue
        if "individual" in (n.get("procurement_method_name") or "").lower():
            continue  # hiring one person, not a tender a firm can bid on
        tid = "wb-" + n["id"]
        closes = (n.get("submission_deadline_date") or "")[:10]
        title = (n.get("bid_description") or n.get("project_name") or "").strip()
        if tid in existing_ids or not title or not closes or closes < today.isoformat():
            continue
        place = (n.get("contact_address") or "").replace("\n", ", ").strip(", ")
        notice = text(n.get("notice_text") or "")
        category = WB_GROUPS.get(n.get("procurement_group") or "", "other")
        out.append({
            "id": tid,
            "buyer": (n.get("contact_organization") or n.get("project_name") or "World Bank-financed project").strip(),
            "title": title[:300],
            "category": category,
            "region": "",
            "place": place[:80] or "Ethiopia",
            "grade": find_grade(title + " " + notice) if category == "construction" else "",
            "closes": closes,
            "time": "" if (n.get("submission_deadline_time") or "")[:5] in ("", "00:00") else n["submission_deadline_time"][:5],
            "type": "donor",
            "firstSeen": today.isoformat(),
            "url": f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{n['id']}",
            "source": "World Bank procurement notices",
            "reference": n.get("bid_reference_no") or "",
            "project": n.get("project_name") or "",
            "method": n.get("procurement_method_name") or "",
            "contact": ", ".join(x for x in (n.get("contact_name"), n.get("contact_email"), n.get("contact_phone_no")) if x),
            "notice": notice[:8000],
        })
    return out


def _words(s):
    return set(re.findall(r"\w{3,}", (s or "").lower()))


def drop_copies(tenders):
    """When our own source (buyer website, collector, World Bank) has a tender that Modern Tenders
    also listed, keep ours and drop the Modern Tenders copy."""
    own = [t for t in tenders if not t.get("id", "").startswith("mt-")]
    keep = []
    for t in tenders:
        if t.get("id", "").startswith("mt-") and t.get("closes"):
            tw = _words(t.get("title")) | _words(t.get("buyer"))
            dup = any(o.get("closes") == t["closes"] and tw and
                      len(tw & (_words(o.get("title")) | _words(o.get("buyer")))) / len(tw) >= 0.6 for o in own)
            if dup:
                continue
        keep.append(t)
    return keep


def text(fragment):
    s = re.sub(r"<svg.*?</svg>", "", fragment, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def first(pattern, h):
    m = re.search(pattern, h, re.S)
    return text(m.group(1)) if m else ""


def listing_codes(page):
    """Return {code: canonical url} from a listing page's ItemList JSON-LD."""
    out = {}
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if d.get("@type") != "ItemList":
            continue
        for it in d.get("itemListElement", []):
            u = it.get("url", "")
            c = CODE_RE.search(u)
            if c and u.startswith(BASE + "/tenders/"):
                out[c.group(1)] = u
    return out


def find_grade(s):
    found = []
    for rx, prefix in GRADE_RE:
        for m in rx.finditer(s):
            if prefix:
                found.append((prefix, int(m.group(1))))
            else:
                found.append((m.group(1).upper(), int(m.group(2))))
    if not found:
        return ""
    p, n = min(found, key=lambda x: x[1])
    return f"{p}-{n}"


def guess_type(buyer, author):
    if DONOR.search(buyer):
        return "donor"
    if NGO.search(buyer):
        return "ngo"
    if author == "Government Portal" or GOV.search(buyer):
        return "gov"
    return "company"


def parse_detail(page, url):
    kv = dict(
        (text(a), text(b))
        for a, b in re.findall(
            r'<span class="td-kv-label">(.*?)</span>\s*<span class="td-kv-value">(.*?)</span>', page, re.S
        )
    )
    author = ""
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if d.get("@type") == "Article":
            author = (d.get("author") or {}).get("name", "")
            keywords = d.get("keywords", "")
            break
    else:
        keywords = ""
    title = first(r'<h1 class="td-title">(.*?)</h1>', page)
    buyer = first(r'<div class="td-org">(.*?)</div>', page)
    location = first(r'<div class="td-location">(.*?)</div>', page).lstrip("📍").strip()
    summary = first(r'<p class="td-short-desc">(.*?)</p>', page)
    region = kv.get("Region", "")
    place = location
    if region and place.endswith(region):
        place = place[: -len(region)].rstrip(" ,")
    closes, ctime = "", ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}:\d{2}))?", kv.get("Closing On", ""))
    if m:
        closes, ctime = m.group(1), m.group(2) or ""
        if ctime == "00:00":
            ctime = ""
    return {
        "title": title,
        "buyer": buyer,
        "region": region,
        "place": place or region,
        "closes": closes,
        "time": ctime,
        "category": kv.get("Category", ""),
        "summary": summary,
        "keywords": keywords,
        "author": author,
        "url": url,
    }


def is_works(d):
    hay = " ".join([d["title"], d["summary"], d["keywords"]])
    head = " ".join([d["title"], d["summary"]])
    if HARD_EXCLUDE.search(head):
        return False
    if SOFT_EXCLUDE.search(head) and not RESCUE.search(head):
        return False
    return bool(WORKS.search(hay))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/opt/tenders/site/tenders.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = dt.datetime.now(TZ).date()
    today_s = today.isoformat()

    try:
        with open(args.out, encoding="utf-8") as f:
            data = json.load(f)
        existing = data.get("tenders", [])
    except (OSError, ValueError):
        existing = []

    known_codes = {}
    for t in existing:
        c = CODE_RE.search(t.get("url", ""))
        if c:
            known_codes[c.group(1)] = t

    seen = {}  # code -> (url, set of implied grades, set of categories)
    for path, grade, cat, max_pages in LISTINGS:
        codes, last = {}, 1
        for n in range(1, max_pages + 1):
            if n > last:
                break  # past the last page (the site repeats it)
            try:
                page = fetch(path if n == 1 else f"{path}?page={n}")
            except Exception as e:  # keep going with the other listings
                print(f"listing failed {path} page {n}: {e}", file=sys.stderr)
                break
            if n == 1:
                nums = [int(x) for x in re.findall(re.escape(path) + r"\?page=(\d+)", page)]
                last = max(nums, default=1)
            codes.update(listing_codes(page))
            # Later pages repeat page 1's JSON-LD, so also read the card links,
            # which carry ?from=<listing> (e.g. ?from=industry/ict-technology).
            frm = path[len("/tenders/"):]
            for code, slug in re.findall(r'href="/tenders/([a-z0-9]{10})-([^"?]*)\?from=' + re.escape(frm) + r'"', page):
                codes.setdefault(code, f"{BASE}/tenders/{code}-{html.unescape(slug)}")
        print(f"{path}: {len(codes)} tenders ({min(last, max_pages)} pages)")
        for code, url in codes.items():
            u, g, cats = seen.setdefault(code, (url, set(), set()))
            if grade:
                g.add(grade)
            cats.add(cat)

    added, skipped, failed = 0, 0, 0
    fresh = []
    for code, (url, grades, cats) in seen.items():
        if code in known_codes:
            continue  # already on the board; keep its data and firstSeen
        try:
            d = parse_detail(fetch(url), url)
        except Exception as e:
            failed += 1
            print(f"detail failed {url}: {e}", file=sys.stderr)
            continue
        if not d["title"]:
            skipped += 1
            continue
        if "construction" in cats and is_works(d):
            category, grade = "construction", find_grade(" ".join([d["title"], d["summary"], d["keywords"]]))
            if not grade and grades:
                grade = min(grades, key=lambda x: int(x.split("-")[1]))
        elif cats - {"construction"}:
            category, grade = sorted(cats - {"construction"})[0], ""
        else:
            skipped += 1  # construction listing, but not works
            continue
        fresh.append({
            "id": "mt-" + code,
            "buyer": d["buyer"],
            "title": d["title"],
            "region": d["region"],
            "place": d["place"],
            "grade": grade,
            "category": category,
            "closes": d["closes"],
            "time": d["time"],
            "type": guess_type(d["buyer"] or d["title"], d["author"]),
            "firstSeen": today_s,
            "url": url,
            "source": "Modern Tenders",
        })
        added += 1

    try:
        wb = worldbank(json.loads(fetch(WB_API)), today, {t.get("id") for t in existing})
        print(f"World Bank: {len(wb)} new open notices")
        fresh += wb
        added += len(wb)
    except Exception as e:
        print(f"World Bank failed: {e}", file=sys.stderr)

    def keep(t):
        c = t.get("closes", "")
        if c:
            return c >= today_s
        try:
            first_seen = dt.date.fromisoformat(t.get("firstSeen", ""))
        except ValueError:
            return True
        return (today - first_seen).days <= STALE_DAYS

    merged = [t for t in existing + fresh if keep(t)]
    gone = [t for t in existing if not keep(t)]
    dropped = len(existing) + len(fresh) - len(merged)
    merged.sort(key=lambda t: (t.get("closes") or "9999-99-99", t.get("time") or "", t.get("buyer", "")))
    out = {"updated": today_s, "tenders": merged}

    print(f"{today_s}: new {added}, not works {skipped}, failed {failed}, dropped {dropped}, total {len(merged)}")
    if args.dry_run:
        json.dump(fresh, sys.stdout, ensure_ascii=False, indent=1)
        return

    # The entry bot writes the same file; take the shared lock and pick up
    # anything it added while we were fetching.
    lock = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(args.out))), "tenders.lock")
    with open(lock, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            with open(args.out, encoding="utf-8") as f:
                now = json.load(f).get("tenders", [])
        except (OSError, ValueError):
            now = []
        have = {t.get("id") for t in merged}
        merged += [t for t in now if t.get("id") not in have and keep(t)]
        merged.sort(key=lambda t: (t.get("closes") or "9999-99-99", t.get("time") or "", t.get("buyer", "")))
        merged[:] = drop_copies(merged)
        flag_checks(merged, today)
        write_atomic(args.out, out)
        archive(os.path.join(os.path.dirname(os.path.abspath(args.out)), "archive.json"), gone)


def archive(path, gone):
    """Keep closed tenders in archive.json, for the buyer directory's history."""
    if not gone:
        return
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f).get("tenders", [])
    except (OSError, ValueError):
        old = []
    have = {t.get("id") for t in old}
    old += [t for t in gone if t.get("id") not in have]
    write_atomic(path, {"tenders": old})


def write_atomic(path, out):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tenders.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write("\n")
        with open(tmp, encoding="utf-8") as f:
            json.load(f)  # sanity check before replacing
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


if __name__ == "__main__":
    main()
