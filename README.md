# Sabermetrics for Magic

Personal Commander/EDH deck optimization tool using LLM-driven reasoning over multiple data sources. Applies sabermetric "moneyball" methodology to find cards with the best cost-to-impact ratio.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
# Initialize the database
python scripts/setup_db.py

# Ingest card data from Scryfall
python scripts/initial_ingestion.py --scryfall-only

# CLI help
python -m sabermetrics --help
```
