# Security

## Reporting a vulnerability

Email security@example.com. We reply within two working days.

## Secrets

Secrets live in AWS Secrets Manager and reach the container as environment
variables. Never commit them; `.env` is git-ignored.
