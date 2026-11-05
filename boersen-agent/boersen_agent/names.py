"""Display names: turn broker names like 'SIEMENS AG NAMENS-AKTIEN O.N.' into 'Siemens AG'."""
from __future__ import annotations

import re

_TAIL = re.compile(
    r"\s+(NAMENS-?AKTIEN|INHABER-?AKTIEN|VINK\.?|REGISTERED\s+SHARES?O?|RG\.?\s*SH\.?|INH\.?-?ANT\.?|"
    r"FUNDS?|O\.\s?N\.?|SF\s+\d|DL\s+\d|EO\s+\d|CL\.?\s?[A-C]\b).*$",
    re.IGNORECASE)
_KEEP_UPPER = {"AG", "SE", "SA", "NV", "PLC", "ETF", "MSCI", "BRD", "USA", "UK", "EU", "AI", "ADR", "UEA", "LPX",
               "BS", "KGAA", "SPA", "ASA", "AB", "UCITS", "ESG", "IT"}


def nice_name(name: str | None) -> str:
    if not name:
        return ""
    s = re.sub(r"\s{2,}", " ", name.strip())
    s = re.sub(r"(?<=[A-Z])\.(?=(REGISTERED|RG\.))", ". ", s)    # 'CORP.REGISTERED' -> 'CORP. REGISTERED'
    s = _TAIL.sub("", s).strip(" ,-")
    if s.isupper() or sum(c.isupper() for c in s) > 0.7 * sum(c.isalpha() for c in s):
        out = []
        for tok in s.split(" "):
            core = tok.strip(".,()")
            if core.isalpha() and len(core) >= 3 and core.upper() not in _KEEP_UPPER:
                tok = tok.replace(core, core.capitalize())
            out.append(tok)
        s = " ".join(out)
    return s or name
