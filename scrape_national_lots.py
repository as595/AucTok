#!/usr/bin/env python3
"""
Scrape property lots linked from https://www.auctionhouse.co.uk/national and
export their details to a CSV file.

The script navigates the National Weekly auction landing page, discovers lot
links pointing at `https://online.auctionhouse.co.uk/lot/details/...`, fetches
each lot page, extracts key details, and writes them to a combined CSV file.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

NATIONAL_URL = "https://www.auctionhouse.co.uk/national"
LOT_PATH_MARKER = "/lot/details/"
DEFAULT_DELAY = 0.75
DEFAULT_OUTPUT = "national_lots.csv"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


class FetchError(RuntimeError):
    """Raised when a remote page cannot be fetched successfully."""


def fetch_content(url: str, session: Optional[requests.Session] = None) -> str:
    """Retrieve a URL and return its text content.

    Raises:
        FetchError: if the response is not successful.
    """

    sess = session or requests.Session()
    try:
        resp = sess.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    except requests.RequestException as exc:  # pragma: no cover - network
        raise FetchError(f"Failed to fetch {url}: {exc}") from exc

    if not resp.ok:
        raise FetchError(f"Failed to fetch {url}: HTTP {resp.status_code}")
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def find_lot_links(html: str, base_url: str = NATIONAL_URL) -> List[str]:
    """Extract unique online lot URLs from the National landing page."""

    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if LOT_PATH_MARKER not in href:
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if "online.auctionhouse.co.uk" not in parsed.netloc:
            continue
        canonical = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if canonical not in seen:
            seen.add(canonical)
            links.append(canonical)
    return links


def _load_json_ld(soup: BeautifulSoup) -> List[dict]:
    payloads: List[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            parsed = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            payloads.extend([p for p in parsed if isinstance(p, dict)])
        elif isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def _flatten_address(address: dict) -> str:
    components = []
    for key in (
        "name",
        "streetAddress",
        "addressLocality",
        "addressRegion",
        "postalCode",
        "addressCountry",
    ):
        value = address.get(key)
        if value:
            components.append(str(value))
    return ", ".join(components)


def _extract_from_json_ld(payloads: List[dict]) -> Dict[str, str]:
    details: Dict[str, str] = {}
    candidates = [
        p
        for p in payloads
        if p.get("@type")
        in (
            "Product",
            "RealEstateListing",
            "Offer",
            "SingleFamilyResidence",
            "House",
        )
    ]
    primary = candidates[0] if candidates else None

    if primary:
        if primary.get("name"):
            details["title"] = str(primary["name"])
        if primary.get("sku"):
            details["lot_number"] = str(primary["sku"])
        if primary.get("productID") and "lot_number" not in details:
            details["lot_number"] = str(primary["productID"])
        offers = primary.get("offers")
        if isinstance(offers, dict):
            if offers.get("price"):
                details["guide_price"] = str(offers["price"])
            if offers.get("availability"):
                details["status"] = str(offers["availability"])
            if offers.get("validFrom"):
                details["auction_date"] = str(offers["validFrom"])
        if primary.get("availability") and "status" not in details:
            details["status"] = str(primary["availability"])
        if primary.get("releaseDate") and "auction_date" not in details:
            details["auction_date"] = str(primary["releaseDate"])

        address = primary.get("address")
        if isinstance(address, dict):
            details["address"] = _flatten_address(address)

    return details


def _regex_search(text: str, patterns: Iterable[str]) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_from_html(soup: BeautifulSoup) -> Dict[str, str]:
    details: Dict[str, str] = {}
    full_text = soup.get_text(" ", strip=True)

    details["lot_number"] = _regex_search(
        full_text,
        [r"Lot\s*#?\s*(\d+)", r"Lot Number[:\s]*([\w-]+)"]
    ) or ""

    details["guide_price"] = _regex_search(
        full_text,
        [
            r"Guide Price[:\s£]*([^|\n]+)",
            r"Price[:\s£]*([^|\n]+)",
        ],
    ) or ""

    details["auction_date"] = _regex_search(
        full_text,
        [
            r"Auction Date[:\s]*([A-Za-z0-9 ,:-]+)",
            r"Closes[:\s]*([A-Za-z0-9 ,:-]+)",
        ],
    ) or ""

    details["status"] = _regex_search(
        full_text,
        [r"Status[:\s]*([A-Za-z ]+)", r"Availability[:\s]*([A-Za-z ]+)"]
    ) or ""

    # Title and address fallbacks
    if not details.get("title"):
        heading = soup.find(["h1", "h2"])
        if heading:
            details["title"] = heading.get_text(" ", strip=True)
    if not details.get("address"):
        addr = soup.find("address") or soup.find(class_=re.compile("address", re.I))
        if addr:
            details["address"] = addr.get_text(" ", strip=True)

    return details


def parse_lot_page(html: str, url: str) -> Dict[str, str]:
    """Extract structured details from a lot detail page."""

    soup = BeautifulSoup(html, "html.parser")
    details: Dict[str, str] = {"url": url}

    json_ld = _load_json_ld(soup)
    details.update({k: v for k, v in _extract_from_json_ld(json_ld).items() if v})
    html_fallbacks = _extract_from_html(soup)
    for key, value in html_fallbacks.items():
        if value and not details.get(key):
            details[key] = value

    return details


def write_csv(rows: List[Dict[str, str]], output_path: str) -> None:
    fieldnames = [
        "lot_number",
        "title",
        "address",
        "guide_price",
        "status",
        "auction_date",
        "url",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def scrape_national_lots(
    output: str = DEFAULT_OUTPUT,
    delay: float = DEFAULT_DELAY,
    max_lots: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Discover and collect lot details from the National landing page."""

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    national_html = fetch_content(NATIONAL_URL, session=session)
    lot_urls = find_lot_links(national_html)
    if max_lots is not None:
        lot_urls = lot_urls[:max_lots]

    rows: List[Dict[str, str]] = []
    for idx, lot_url in enumerate(lot_urls, start=1):
        try:
            lot_html = fetch_content(lot_url, session=session)
        except FetchError as exc:  # pragma: no cover - network
            print(f"[warn] Skipping {lot_url}: {exc}", file=sys.stderr)
            continue

        details = parse_lot_page(lot_html, lot_url)
        rows.append(details)
        print(f"[{idx}/{len(lot_urls)}] Collected lot {details.get('lot_number', '?')} from {lot_url}")

        if delay:
            time.sleep(delay)

    write_csv(rows, output)
    print(f"Wrote {len(rows)} lot records to {output}")
    return rows


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape National Weekly auction lots into a CSV file."
    )
    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT,
        help=f"Path to the CSV output file (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="Seconds to wait between lot requests (default: %(default).2f)",
    )
    parser.add_argument(
        "--max-lots",
        type=int,
        default=None,
        help="Limit the number of lot pages processed (for smoke tests)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    try:
        scrape_national_lots(
            output=args.output,
            delay=args.delay,
            max_lots=args.max_lots,
        )
    except FetchError as exc:  # pragma: no cover - network
        print(f"Failed to scrape National Weekly lots: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
