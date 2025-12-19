# AucTok

## Auction House scraper

`scrape_auctionhouse.py` crawls [auctionhouse.co.uk](https://www.auctionhouse.co.uk/) and collects the
details of every property currently for sale. The script walks the site's sitemap to
discover property pages, fetches each page, extracts key details (title, address,
guide price, status, auction date, and lot number), and writes them to a CSV file.

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Usage

```bash
python scrape_auctionhouse.py --output properties.csv
```

Key options:

- `--sitemap` – override the root sitemap URL if the site changes.
- `--delay` – seconds to sleep between requests (default: 0.75s).
- `--limit` – cap the number of properties processed (useful for smoke tests).
- `--include-sold` – keep properties flagged as sold/withdrawn instead of filtering them out.
