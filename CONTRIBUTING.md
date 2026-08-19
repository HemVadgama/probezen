# Contributing

Thank you for helping make Probezen quieter and more trustworthy.

Using Probezen does not require a source checkout:

```bash
uv tool install probezen
probezen --help
```

For development, clone the repository and install the locked development environment:

```bash
uv sync --extra dev
```

Then:

1. Create a focused branch and test deterministic behavior without external services.
2. Run `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src`,
   `uv run pytest`, and `uv build`.
3. Explain observable behavior and false-positive tradeoffs in the pull request.

Please do not add telemetry, network-dependent tests, or broad platform features without prior discussion. By contributing, you agree that your work is provided under the MIT License.
