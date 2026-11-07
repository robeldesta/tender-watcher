"""Reading notices with Claude and keeping the static board in step. Shares its prompt and
schemas with the Telegram entry bot (deploy/entry_bot.py), so both read notices the same way."""
import importlib.util
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_candidates = [os.environ.get("ENTRY_BOT_PY", ""), os.path.join(_here, "entry_bot.py"),
               os.path.join(_here, "..", "..", "deploy", "entry_bot.py")]
_path = next(p for p in _candidates if p and os.path.exists(p))
_spec = importlib.util.spec_from_file_location("entry_bot", _path)
eb = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("entry_bot", eb)
_spec.loader.exec_module(eb)

CATEGORIES = eb.CATEGORIES
TYPES = {"gov": "Government", "ngo": "NGO / UN", "donor": "Donor funded", "company": "Company / bank"}
REGIONS = ["Addis Ababa", "Afar", "Amhara", "Benishangul-Gumuz", "Central Ethiopia", "Dire Dawa", "Gambela",
           "Harari", "Oromia", "Sidama", "Somali", "South Ethiopia", "South West Ethiopia", "Tigray",
           "Several regions"]
ExtractError = eb.ExtractError
extract = eb.extract
to_entry = eb.to_entry
ethiopian_to_gregorian = eb.ethiopian_to_gregorian
norm = eb.norm


def board_entry(t, site_url):
    """A published tender as a row of the static board's tenders.json."""
    return {
        "id": t.id, "buyer": t.buyer, "title": t.title, "category": t.category, "region": t.region,
        "place": t.place, "grade": t.grade, "closes": t.closes, "time": t.time, "type": t.type,
        "firstSeen": (t.published_at or t.created_at).date().isoformat(),
        "url": f"/notices/{t.photo}" if t.photo else t.url, "source": t.source,
        "bid_security": t.bid_security, "document_fee": t.document_fee, "reference": t.reference,
        "notice": t.notice,
    }


def publish_to_board(path, t, site_url, photos_dir=""):
    """Copy a published tender (and its notice photo) to the static board, which nginx serves."""
    if not path:
        return
    if t.photo and photos_dir:
        import shutil
        dest = os.path.join(os.path.dirname(os.path.abspath(path)), "notices")
        os.makedirs(dest, exist_ok=True)
        src, dst = os.path.join(photos_dir, t.photo), os.path.join(dest, t.photo)
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copyfile(src, dst)
    eb.add_to_board(path, {k: v for k, v in board_entry(t, site_url).items() if v not in ("", None)})
