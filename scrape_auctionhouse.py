"""Auction House UK property scraper.

This module fetches all property pages referenced in the site's sitemap
and extracts key details for properties that appear to still be available
for sale. It writes the results to CSV for downstream analysis.

Example:
    python scrape_auctionhouse.py --output properties.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Iterable, List, Optional, Set
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


DEFAULT_SITEMAP_URL = "https://www.auctionhouse.co.uk/sitemap.xml"
DEFAULT_DELAY_SEC = 0.75
DEFAULT_USER_AGENT = "AucTok-scraper/0.1 (+https://example.com/contact)"


@dataclass
class PropertyRecord:
    """Normalized representation of a single property page."""

    url: str
    title: Optional[str]
    address: Optional[str]
    guide_price: Optional[str]
    status: Optional[str]
    auction_date: Optional[str]
    lot_number: Optional[str]

    def to_row(self) -> dict:
        return asdict(self)


def build_session(user_agent: str = DEFAULT_USER_AGENT) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept-Language": "en-GB,en;q=0.9",
        }
    )
    return session


def fetch_text(
    session: requests.Session,
    url: str,
    *,
    retries: int = 3,
    backoff: float = 1.5,
    timeout: int = 30,
) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # requests can raise many subclasses
            last_error = exc
            if attempt >= retries:
                break
            sleep_for = backoff ** (attempt - 1)
            logging.warning("Error fetching %s (attempt %s/%s): %s", url, attempt, retries, exc)
            time.sleep(sleep_for)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def looks_like_property_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    return "/property/" in path or "/properties/" in path


def iter_sitemap_property_urls(
    session: requests.Session,
    root_sitemap: str = DEFAULT_SITEMAP_URL,
    *,
    max_nested: int = 100,
) -> List[str]:
    """Return property detail URLs referenced anywhere in the sitemap graph."""

    to_visit = [root_sitemap]
    visited: Set[str] = set()
    property_urls: Set[str] = set()

    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)

        logging.info("Fetching sitemap: %s", current)
        body = fetch_text(session, current)
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            logging.warning("Could not parse sitemap %s: %s", current, exc)
            continue

        tag = root.tag.lower()
        if tag.endswith("sitemapindex"):
            for loc in root.findall(".//{*}loc"):
                loc_text = (loc.text or "").strip()
                if not loc_text or loc_text in visited:
                    continue
                if len(visited) + len(to_visit) >= max_nested:
                    logging.warning("Skipping sitemap %s; max depth %s reached", loc_text, max_nested)
                    continue
                if loc_text.endswith(".xml"):
                    to_visit.append(loc_text)
        else:
            for loc in root.findall(".//{*}loc"):
                loc_text = (loc.text or "").strip()
                if not loc_text:
                    continue
                if looks_like_property_url(loc_text):
                    property_urls.add(loc_text)

    return sorted(property_urls)


def _first_text(elem: Optional[BeautifulSoup], default: Optional[str] = None) -> Optional[str]:
    if not elem:
        return default
    text = elem.get_text(" ", strip=True)
    return text or default


def extract_title(soup: BeautifulSoup) -> Optional[str]:
    return _first_text(soup.find("h1")) or _first_text(soup.title)


def extract_address(soup: BeautifulSoup) -> Optional[str]:
    # Prefer explicit address containers
    for cls in ["address", "property-address", "lot-address", "address-block"]:
        node = soup.find(class_=re.compile(cls, re.IGNORECASE))
        text = _first_text(node)
        if text:
            return text

    # Fallback: look for a postal pattern within common tags
    for tag_name in ["p", "div", "span", "li"]:
        for node in soup.find_all(tag_name):
            text = _first_text(node)
            if not text:
                continue
            if re.search(r"\b[A-Z]{1,2}\d[\dA-Z]?\s*\d[A-Z]{2}\b", text):  # UK postcode
                return text
    return None


def extract_guide_price(text_blob: str) -> Optional[str]:
    match = re.search(r"Guide\s*Price\s*[:\-]?\s*(£?[\d,]+(?:\.\d{2})?(?:\s*to\s*£?[\d,]+(?:\.\d{2})?)?)",
                      text_blob,
                      re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def extract_auction_date(text_blob: str) -> Optional[str]:
    match = re.search(r"Auction\s*Date\s*[:\-]?\s*([^\n]+)", text_blob, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"Bidding\s+closes\s*[:\-]?\s*([^\n]+)", text_blob, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def extract_lot_number(text_blob: str) -> Optional[str]:
    match = re.search(r"Lot\s*(\d+)", text_blob, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def extract_status(soup: BeautifulSoup, text_blob: str) -> Optional[str]:
    for cls in ["status", "availability", "flag", "badge"]:
        node = soup.find(class_=re.compile(cls, re.IGNORECASE))
        text = _first_text(node)
        if text:
            return text

    match = re.search(r"(available|for\s+sale|bidding\s+open|unsold|sold\s+prior|sold|withdrawn|postponed)",
                      text_blob,
                      re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def parse_property_page(html: str, url: str) -> PropertyRecord:
    soup = BeautifulSoup(html, "html.parser")
    text_blob = "\n".join(soup.stripped_strings)

    return PropertyRecord(
        url=url,
        title=extract_title(soup),
        address=extract_address(soup),
        guide_price=extract_guide_price(text_blob),
        status=extract_status(soup, text_blob),
        auction_date=extract_auction_date(text_blob),
        lot_number=extract_lot_number(text_blob),
    )


def is_for_sale(record: PropertyRecord) -> bool:
    status = (record.status or "").lower()
    if status:
        if any(flag in status for flag in ["sold", "exchanged", "withdrawn", "completed", "contracted"]):
            return False
    if record.guide_price:
        return True
    return not status  # assume available if status is unknown


def write_csv(path: str, records: Iterable[PropertyRecord]) -> None:
    rows = list(records)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["url", "title", "address", "guide_price", "status", "auction_date", "lot_number"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_row())


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    session = build_session(args.user_agent)

    try:
        property_urls = iter_sitemap_property_urls(session, args.sitemap)
    except Exception as exc:
        logging.error("Could not read sitemap: %s", exc)
        return 1

    if args.limit:
        property_urls = property_urls[: args.limit]

    logging.info("Discovered %s property URLs", len(property_urls))

    records: List[PropertyRecord] = []
    for idx, url in enumerate(property_urls, start=1):
        logging.info("[%s/%s] Fetching property %s", idx, len(property_urls), url)
        try:
            html = fetch_text(session, url)
            record = parse_property_page(html, url)
            if args.include_sold or is_for_sale(record):
                records.append(record)
        except Exception as exc:
            logging.warning("Skipping %s due to error: %s", url, exc)
            continue
        time.sleep(args.delay)

    write_csv(args.output, records)
    logging.info("Wrote %s records to %s", len(records), args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape Auction House UK property details")
    parser.add_argument("--output", default="auctionhouse_properties.csv", help="CSV destination path")
    parser.add_argument("--sitemap", default=DEFAULT_SITEMAP_URL, help="Root sitemap URL")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SEC, help="Delay between requests (seconds)")
    parser.add_argument("--limit", type=int, help="Maximum number of properties to fetch (for testing)")
    parser.add_argument("--include-sold", action="store_true", help="Include properties flagged as sold/withdrawn")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="User-Agent header for HTTP requests")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
