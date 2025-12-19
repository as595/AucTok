"""Integration checks for the Auction House scraper.

These tests fetch live data from auctionhouse.co.uk to confirm that
we enumerate all properties currently marketed in the National Weekly
auction. If the site is unreachable from the execution environment the
test will be skipped rather than fail.
"""

from __future__ import annotations

import unittest

from scrape_auctionhouse import (
    build_session,
    fetch_text,
    is_for_sale,
    iter_sitemap_property_urls,
    parse_property_page,
)

# According to the site's marketing page, there are currently 186 lots
# in the National Weekly auction. This value may increase over time, so
# the assertion uses a lower bound to avoid false negatives when more
# properties are added.
EXPECTED_NATIONAL_WEEKLY_FOR_SALE = 186


class AuctionHouseIntegrationTest(unittest.TestCase):
    def test_discovers_all_national_weekly_for_sale_properties(self) -> None:
        session = build_session()
        try:
            property_urls = iter_sitemap_property_urls(session)
        except Exception as exc:  # pragma: no cover - network guard
            self.skipTest(f"Network unavailable for sitemap fetch: {exc}")

        active_properties = []
        fetch_errors = []
        for url in property_urls:
            try:
                html = fetch_text(session, url)
                record = parse_property_page(html, url)
            except Exception as exc:  # pragma: no cover - network guard
                fetch_errors.append((url, exc))
                continue

            if is_for_sale(record):
                active_properties.append(record)

        if fetch_errors:
            self.fail(f"Failed to fetch {len(fetch_errors)} property pages: {fetch_errors[:3]}")

        self.assertGreaterEqual(
            len(active_properties),
            EXPECTED_NATIONAL_WEEKLY_FOR_SALE,
            "Scraper missed National Weekly auction properties",
        )


if __name__ == "__main__":
    unittest.main()
