# Contributing

Thanks for helping out! A few things to know before you open a pull request.

## Workflow

1. Fork the repository and create a branch from `main`.
2. Make your change, with tests.
3. Run the test suite with `pytest` and make sure it passes.
4. Open a pull request and fill in the template.

## Style

We format code with `ruff format` and lint with `ruff check`. CI runs both,
so please run them locally first.

## Commit messages

Use the imperative mood in the subject line ("Add retry to the CRM client",
not "Added retry"), keep it under 72 characters, and explain the why in the
body when it isn't obvious from the diff.
