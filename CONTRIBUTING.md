# Contributing to autoresearch-dex

Thank you for your interest in contributing! This document provides guidelines for participating in the project.

## How to Contribute

### Reporting Issues

If you find a bug or have a feature request, please open an issue with:

- A clear description of the problem or suggestion
- Steps to reproduce (for bugs)
- Your environment (OS, Python version, GPU if applicable)
- Relevant logs or error messages

### Pull Requests

1. **Fork** the repository and create your branch from `main`
2. **Install** development dependencies:
   ```bash
   uv sync --extra dev
   ```
3. **Make** your changes
4. **Run** the linter:
   ```bash
   uv run ruff check .
   uv run ruff format .
   ```
5. **Test** your changes locally
6. **Submit** a pull request with a clear description

### Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

- Line length: 100 characters
- Import sorting enabled
- Follow PEP 8 guidelines

Run before committing:

```bash
uv run ruff check . --fix
uv run ruff format .
```

### Commit Messages

Use clear, descriptive commit messages:

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation changes
- `style:` formatting, missing semicolons, etc.
- `refactor:` code restructuring
- `test:` adding or updating tests
- `chore:` maintenance tasks

Example:
```
feat: add GridStrategy position sizing limit

Adds a max_position parameter to GridStrategy to prevent
unbounded risk accumulation in trending markets.
```

### Strategy Contributions

When adding a new strategy:

1. Place it in `dex/strategies/`
2. Inherit from the base strategy class
3. Add it to `dex/strategies/__init__.py`
4. Document parameters in `docs/STRATEGIES.md`
5. Include a backtest example in the PR description

### Live Trading Changes

Changes affecting live trading (`live_nado_quant.py`, `live_okx_quant.py`, `dex/live/common.py`) require extra scrutiny:

- Clearly describe risk implications
- Prefer Maker orders to minimize fees
- Include timeout and fallback logic
- Test on paper trading first if possible

## Development Setup

```bash
# Clone your fork
git clone https://github.com/yourusername/autoresearch-dex.git
cd autoresearch-dex

# Install dependencies
uv sync --extra dev

# Download sample data
uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 30

# Run a quick backtest
uv run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 30
```

## Code of Conduct

- Be respectful and constructive
- Focus on the technical merits of contributions
- Help newcomers learn and participate

## Questions?

Open a [Discussion](https://github.com/yourusername/autoresearch-dex/discussions) or reach out in an issue.
