# AucTok

## Auction House scraper

`scrape_national_lots.py` loads the National Weekly auction landing page at
<https://www.auctionhouse.co.uk/national>, discovers all linked online lot
pages, scrapes key details from each one (title, address, guide price, status,
auction date, and lot number), and writes them to a CSV file.

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

- `--delay` – seconds to sleep between lot requests (default: 0.75s).
- `--max-lots` – cap the number of lots processed (useful for smoke tests).
