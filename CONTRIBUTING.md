# Contributing to EIDOLON

Thank you for your interest in contributing to **EIDOLON**.

## Getting Started

1. Fork the repository on GitHub (`https://github.com/AYMRIKNG/EIDOLON`).
2. Clone your fork and create a virtual environment:
   ```bash
   git clone https://github.com/<your-username>/EIDOLON.git
   cd EIDOLON
   python -m venv .venv
   python setup.py
   ```
3. Create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```

## Adding New Actions or Plugins

- **Built-in Actions** live in `actions/` and export a `TOOL` schema + `run()` function, auto-discovered by `core/action_loader.py`.
- **Custom Plugins** live in `plugins/`. Copy `plugins/_template.py` as a starting point.

## Code Guidelines

- Target **Python 3.11 – 3.13**.
- Keep cross-platform compatibility in mind (Windows, macOS, Linux); guard OS-specific imports cleanly.
- Never hardcode absolute user paths, API keys, tokens, or personal data.
- Before opening a pull request, verify syntax across all modified Python files:
  ```bash
  python -m compileall -q .
  ```

## Pull Request Process

1. Keep pull requests focused on a single bug fix or feature.
2. Update relevant documentation (`README.md` or `docs/`) when changing behavior or adding actions.
3. Fill out the pull request template clearly and describe how you tested your changes.

## License

By contributing to EIDOLON, you agree that your contributions will be licensed under the project's [CC BY-NC 4.0](LICENSE) license.
