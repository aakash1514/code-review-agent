# Inventory Service

Tracks stock levels and reservations for the warehouse.

## Requirements

- Python 3.12
- PostgreSQL 16

## Configuration

The service reads its settings from environment variables:

| Variable       | Required | Description                                  |
|----------------|----------|----------------------------------------------|
| `DATABASE_URL` | yes      | PostgreSQL connection string                 |
| `PORT`         | no       | Port the HTTP server listens on (default 8000) |
| `LOG_LEVEL`    | no       | `debug`, `info` (default) or `warning`       |

## Running locally

1. Create a virtual environment and install the dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Point the service at your local database:

   ```bash
   export DB_URL=postgresql://localhost:5432/inventory
   ```

3. Start the server:

   ```bash
   PORT=5000 python -m inventory.server
   ```

4. Open http://localhost:8000/health in your browser; it should return `ok`.
