# Congressional Stock Trading Research & Analytics

A data engineering and analytics platform for researching and analyzing Congressional stock trading patterns, member portfolios, and performance metrics.

## Project Purpose

This repository contains tools and infrastructure to:
- Ingest and normalize Congressional stock trade disclosures
- Track member information and historical committee memberships
- Correlate trades with company events (SEC filings, press releases, government contracts, legislation)
- Calculate trade performance from both transaction and disclosure dates
- Compare Congressional trading returns against market benchmarks (SPY)
- Expose normalized data through a REST API
- Provide a web interface for searching, filtering, and analyzing trades and performance metrics

## High-Level Architecture

```
Data Ingestion → Data Processing → PostgreSQL Database → FastAPI Backend → Web Frontend
     (Python)        (Python)           (SQL/Alembic)     (Python/API)    (React/TypeScript)
```

### Reference Applications

This repository includes two reference applications/data sources used to inform the main StockGov build:

- **`congress-legislators-main/`** - Legislative data source with current/historical member information, committee memberships, and social media handles in YAML format
- **`Quantgress-main/`** - Existing scraping/ingestion framework with scripts for trades, contracts, lobbying, donors, patents, executive compensation, and other data sources

### Top-Level Directories

- **`congress-legislators-main/`** - Reference data source (legislators, committees, social media)
- **`Quantgress-main/`** - Reference ingestion application with scraping scripts
- **`backend/`** - FastAPI application, API endpoints, database models, business logic
- **`ingestion/`** - Python modules for ingesting data from various sources (Congress, SEC, prices, etc.)
- **`data/`** - Raw input data, processed/transformed data, and sample datasets
- **`database/`** - SQL migrations, seeds, and database-related utilities
- **`frontend/`** - Web application for searching and visualizing trades and analytics
- **`scripts/`** - Utility scripts for maintenance, testing, data validation, and scheduled jobs
- **`tests/`** - Project-wide tests (integration, end-to-end)
- **`docs/`** - Architecture documentation, API specs, and developer guides
- **`docker/`** - Docker configuration files and container-related utilities
- **`logs/`** - Application and ingestion logs

## Project Status

**Current Stage: Initial Scaffolding**

This repository contains the initial directory structure and minimal starter files. Application logic, database schemas, and frontend implementation have not yet been completed.

## Getting Started

*(Coming soon - database schema and basic setup instructions)*

## Contributing

See `docs/` for development guidelines and contribution instructions.

## License

See LICENSE file for details.
