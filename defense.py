# defense.py
from __future__ import annotations
import os, re
from typing import Optional, Set, Dict
from flask import Flask, request, abort, g

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
except Exception:  # pragma: no cover
    Limiter = None
    def get_remote_address():  # type: ignore
        return (request.headers.get("X-Forwarded-For","").split(",")[0].strip()
                or request.remote_addr or "0.0.0.0")

SQLI_PATTERNS: Set[re.Pattern] = {
    re.compile(r"(?i)\bunion\b.*\bselect\b"),
    re.compile(r"(?i)\bdrop\s+table\b"),
    re.compile(r"(?i)\binsert\s+into\b"),
    re.compile(r"(?i)\bor\s+1\s*=\s*1\b"),
    re.compile(r"(?i)\bupdate\s+.*\bset\b"),
    re.compile(r"(?i)\bdelete\s+from\b"),
}
PATH_PATTERNS: Set[re.Pattern] = { re.compile(r"\.\./"), re.compile(r"%2e%2e/") }
UA_BLOCKLIST: Set[str] = { "sqlmap","nikto","wpscan","acunetix","nessus","owasp","curl/7." }

def _looks_malicious() -> Optional[str]:
    cl = request.content_length or 0
    if cl > 2_000_000: return "req_too_large"
    ua = (request.headers.get("User-Agent") or "").lower()
    for bad in UA_BLOCKLIST:
        if bad in ua: return "ua_blocked"
    qs = request.query_string.decode("utf-8","ignore")
    path = request.path or ""
    for p in SQLI_PATTERNS:
        if p.search(qs): return "sqli_qs"
    for p in PATH_PATTERNS:
        if p.search(path) or p.search(qs): return "path_traversal"
    if request.method in
