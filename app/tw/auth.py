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


def norm_login(s):
    """What someone types to sign in: a mobile number, or a username given to them by us (like "jobale")."""
    s = (s or "").strip()
    u = s.lower()
    return norm_phone(s) or (u if re.fullmatch(r"[a-z][a-z0-9._-]{2,19}", u) else "")


# -- two-step sign-in: time-based codes from an authenticator app (RFC 6238), plus one-time backup codes
import secrets
import struct
import time


def new_totp_secret():
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _hotp(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000)


def totp_now(secret, at=None):
    return _hotp(secret, int((at or time.time()) // 30))


def totp_step(secret, code, last_step=0, at=None):
    """The time step a 6-digit code belongs to (one step either side allowed for clock drift), or 0.
    Steps at or before last_step are refused, so a code works once."""
    code = re.sub(r"\D", "", code or "")
    if len(code) != 6:
        return 0
    now = int((at or time.time()) // 30)
    for step in (now - 1, now, now + 1):
        if step > last_step and hmac.compare_digest(_hotp(secret, step), code):
            return step
    return 0


def otpauth_uri(secret, account, issuer="Tender Watcher"):
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def new_backup_codes(n=8):
    """Plain codes to show once, and their hashes to store."""
    codes = ["-".join(secrets.token_hex(2) for _ in range(3)) for _ in range(n)]
    return codes, [hashlib.sha256(c.encode()).hexdigest() for c in codes]


def use_backup_code(code, hashes):
    """The hash list without the used code, or None when the code doesn't match."""
    c = re.sub(r"[^0-9a-f]", "", (code or "").lower())
    c = "-".join(c[i:i + 4] for i in range(0, 12, 4)) if len(c) == 12 else ""
    h = hashlib.sha256(c.encode()).hexdigest() if c else ""
    for x in hashes:
        if h and hmac.compare_digest(x, h):
            return [y for y in hashes if y != x]
    return None
