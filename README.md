# budget-tracker-ai

## Runtime

Python **3.10.x** is required (`.python-version`; `pyproject.toml` restricts the project to `>=3.10,<3.11`). Use the latest available 3.10 patch release.

Create the environment with `python3.10 -m venv .venv`. Dependency constraints in `constraints.txt` select Python 3.10-compatible Streamlit, pandas, PyMuPDF, and DNotifier releases. Apply them with `python -m pip install -c constraints.txt <packages>`; the implementation's `requirements.txt` must include `-c constraints.txt`.

Streamlit 1.65.0 includes `streamlit skills --yes`. Full dependency installation and runtime tests on Python 3.10 remain setup acceptance tasks; compatibility metadata alone does not verify the app.

See [the design](docs/DESIGN.md), [checklist](docs/TODO.md), and [two-developer plan](docs/PARALLEL_PLAN.md).
