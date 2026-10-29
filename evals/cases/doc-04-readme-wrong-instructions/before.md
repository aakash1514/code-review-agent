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
