"""
CSE client — fetch latest annual report metadata + download PDF to MinIO.

The CSE financials endpoint is a public POST taking only `symbol`. The cookie
jar from a browser cURL is session noise and not required. We send a normal
user-agent and the form field.

Response shape (relevant part):
    infoAnnualData: [ {id, path, manualDate, uploadedDate, fileText, ...}, ... ]
    index [0] is the LATEST annual report (newest first).

Download URL = CDN_BASE + path, e.g.
    https://cdn.cse.lk/cmt/upload_report_file/628_1772705611455.pdf
"""
import io
import re
import time
import hashlib
from datetime import datetime, timezone

import requests

FINANCIALS_URL = "https://www.cse.lk/api/financials"
CDN_BASE = "https://cdn.cse.lk/"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
REQUEST_TIMEOUT = 60


class CSEError(Exception):
    pass


def fetch_financials(symbol: str, retries: int = 3, backoff: float = 2.0) -> dict:
    """POST the financials endpoint for a symbol. Retries with backoff."""
    headers = {"accept": "application/json, text/plain, */*",
               "content-type": "application/x-www-form-urlencoded",
               "user-agent": USER_AGENT,
               "origin": "https://www.cse.lk",
               "referer": f"https://www.cse.lk/company-profile?symbol={symbol}"}
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(FINANCIALS_URL, data={"symbol": symbol},
                              headers=headers, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise CSEError(f"fetch_financials failed for {symbol}: {last}")


def _year_from_manual_date(ms: int | None) -> int | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def _year_from_text(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"\b(20[12]\d)\b", text)
    return int(m.group(1)) if m else None


def all_annual_reports(financials: dict) -> list[dict]:
    """Extract EVERY annual report descriptor (full history) for catalog discovery.
    Returns a list newest-first, each with year + download url. Skips entries
    with no usable path or year."""
    out = []
    for item in (financials.get("infoAnnualData") or []):
        path = item.get("path")
        if not path:
            continue
        year = _year_from_manual_date(item.get("manualDate")) \
            or _year_from_text(item.get("fileText"))
        if not year:
            continue
        out.append({"cse_report_id": item.get("id"),
                    "cse_path": path,
                    "fiscal_year": year,
                    "file_text": item.get("fileText"),
                    "pdf_url": CDN_BASE + path.lstrip("/")})
    return out


def latest_annual_report(financials: dict) -> dict | None:
    """Extract the latest annual report descriptor, or None if the company
    has no annual reports listed."""
    annuals = financials.get("infoAnnualData") or []
    if not annuals:
        return None
    top = annuals[0]                          # index 0 == latest
    # fiscal year: prefer manualDate (reporting period), fall back to fileText
    year = _year_from_manual_date(top.get("manualDate")) \
        or _year_from_text(top.get("fileText"))
    path = top.get("path")
    if not path:
        return None
    return {"cse_report_id": top.get("id"),
            "cse_path": path,
            "fiscal_year": year,
            "file_text": top.get("fileText"),
            "pdf_url": CDN_BASE + path.lstrip("/")}


def download_pdf(pdf_url: str, retries: int = 3, backoff: float = 2.0) -> bytes:
    """Download a PDF; returns raw bytes. Retries with backoff."""
    headers = {"user-agent": USER_AGENT,
               "accept": "application/pdf,*/*"}
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(pdf_url, headers=headers, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            content = r.content
            # sanity: PDFs start with %PDF
            if not content[:4] == b"%PDF":
                raise CSEError(f"not a PDF (starts with {content[:8]!r})")
            return content
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise CSEError(f"download_pdf failed for {pdf_url}: {last}")


def sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
