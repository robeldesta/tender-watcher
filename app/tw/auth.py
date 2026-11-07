import base64
import hashlib
import hmac
import os
import re


def hash_password(pw):
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_password(pw, stored):
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(got, base64.b64decode(h))
    except Exception:
        return False


def norm_phone(s):
    """+251 91 123 4567, 0911234567, 911234567 -> 0911234567."""
    d = re.sub(r"\D", "", s or "")
    if d.startswith("251"):
        d = "0" + d[3:]
    elif len(d) == 9:
        d = "0" + d
    return d if re.fullmatch(r"0[79]\d{8}", d) else ""
