#!/usr/bin/env python3
"""Daily refresh of the Tender Watcher board from Modern Tenders.

Reads the public listing pages and the public teaser of each tender page
(robots.txt allows /tenders/*; the gated part of a page is never requested
or bypassed). Keeps construction works only, merges into tenders.json and
writes it atomically.

Usage: python3 refresh.py [--out /opt/tenders/site/tenders.json] [--dry-run]
"""
import argparse
import datetime as dt
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

# listing path -> grade implied by that listing ("" = none)
LISTINGS = [
    ("/tenders/industry/construction", ""),
    ("/tenders/competency/general-contractor-grade-3", "GC-3"),
    ("/tenders/competency/general-contractor-grade-5", "GC-5"),
    ("/tenders/competency/building-contractor-grade-5", "BC-5"),
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

    seen = {}  # code -> (url, set of implied grades)
    for path, grade in LISTINGS:
        try:
            page = fetch(path)
        except Exception as e:  # keep going with the other listings
            print(f"listing failed {path}: {e}", file=sys.stderr)
            continue
        codes = listing_codes(page)
        print(f"{path}: {len(codes)} tenders")
        for code, url in codes.items():
            u, g = seen.setdefault(code, (url, set()))
            if grade:
                g.add(grade)

    added, skipped, failed = 0, 0, 0
    fresh = []
    for code, (url, grades) in seen.items():
        if code in known_codes:
            continue  # already on the board; keep its data and firstSeen
        try:
            d = parse_detail(fetch(url), url)
        except Exception as e:
            failed += 1
            print(f"detail failed {url}: {e}", file=sys.stderr)
            continue
        if not d["title"] or not is_works(d):
            skipped += 1
            continue
        grade = find_grade(" ".join([d["title"], d["summary"], d["keywords"]]))
        if not grade and grades:
            grade = min(grades, key=lambda x: int(x.split("-")[1]))
        fresh.append({
            "id": "mt-" + code,
            "buyer": d["buyer"],
            "title": d["title"],
            "region": d["region"],
            "place": d["place"],
            "grade": grade,
            "closes": d["closes"],
            "time": d["time"],
            "type": guess_type(d["buyer"] or d["title"], d["author"]),
            "firstSeen": today_s,
            "url": url,
            "source": "Modern Tenders",
        })
        added += 1

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
    dropped = len(existing) + len(fresh) - len(merged)
    merged.sort(key=lambda t: (t.get("closes") or "9999-99-99", t.get("time") or "", t.get("buyer", "")))
    out = {"updated": today_s, "tenders": merged}

    print(f"{today_s}: new {added}, not works {skipped}, failed {failed}, dropped {dropped}, total {len(merged)}")
    if args.dry_run:
        json.dump(fresh, sys.stdout, ensure_ascii=False, indent=1)
        return

    d = os.path.dirname(os.path.abspath(args.out))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tenders.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write("\n")
        with open(tmp, encoding="utf-8") as f:
            json.load(f)  # sanity check before replacing
        os.chmod(tmp, 0o644)
        os.replace(tmp, args.out)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


if __name__ == "__main__":
    main()
