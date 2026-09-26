# parser.py
# Parse the admin paste format → dict.

import re
from datetime import datetime

class ParseError(ValueError):
    pass

_KV = re.compile(r"\s*\|\s*")
_KVPAIR = re.compile(r"^([A-Za-z]+)\s*=\s*(.*)$")
_EP = re.compile(r"^(?P<email>[^:\s|]+):(?P<pw>[^|\s]+)$")


def parse_line(line: str) -> dict:
    line = line.strip()
    if not line or line.startswith("#"):
        raise ParseError("empty/comment")

    parts = _KV.split(line)
    head = parts[0].strip()
    m = _EP.match(head)
    if not m:
        raise ParseError(f"bad email:pass head: {head!r}")

    out = {
        "email": m.group("email"),
        "password": m.group("pw"),
        "ovpn_user": None, "ovpn_pass": None,
        "plan": None, "expire_date": None, "days_left": 0,
        "auto_renew": 0, "status": "ACTIVE",
        "license": None, "pptp": None, "country": "UNKNOWN",
    }

    for p in parts[1:]:
        mm = _KVPAIR.match(p.strip())
        if not mm:
            continue
        k, v = mm.group(1).lower(), mm.group(2).strip()
        if k == "ovpnuser":  out["ovpn_user"] = v
        elif k == "ovpnpass": out["ovpn_pass"] = v
        elif k == "plan":
            vv = v.lower().replace(" ", "")
            out["plan"] = {"1month":"1mo","1mo":"1mo",
                           "3month":"3mo","3mo":"3mo",
                           "6month":"6mo","6mo":"6mo",
                           "1year":"1yr","1yr":"1yr","12mo":"1yr"}.get(vv, vv)
        elif k == "expire":
            try:
                datetime.strptime(v, "%Y-%m-%d")
                out["expire_date"] = v
            except ValueError:
                raise ParseError(f"bad expire {v!r}")
        elif k == "days":
            out["days_left"] = int(v) if v.isdigit() else 0
        elif k == "autorenew":
            out["auto_renew"] = 1 if v.lower() in ("true","1","yes") else 0
        elif k == "status":
            out["status"] = v.upper()
        elif k == "license": out["license"] = v
        elif k == "pptp":    out["pptp"] = v
        elif k == "country": out["country"] = v.upper()

    if not (out["ovpn_user"] and out["ovpn_pass"]):
        raise ParseError("missing OVPNUser/OVPNPass")
    if not out["expire_date"]:
        raise ParseError("missing Expire")
    if out["plan"] not in ("1mo","3mo","6mo","1yr"):
        raise ParseError(f"unknown plan {out['plan']!r}")
    return out


def bulk_parse(blob: str) -> tuple[list[dict], list[str]]:
    good, bad = [], []
    for raw in blob.splitlines():
        try:
            good.append(parse_line(raw))
        except ParseError as e:
            bad.append(f"{e} :: {raw[:80]}")
    return good, bad