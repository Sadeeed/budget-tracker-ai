# Agent instructions — BudgetPilot

You are implementing the **BudgetPilot DNotifier hackathon MVP**.

1. Read `docs/DESIGN.md` completely; it is the technical source of truth.
2. Follow `docs/TODO.md` in order, checking off completed work and noting deviations. For parallel development, follow ownership and checkpoints in `docs/PARALLEL_PLAN.md`.
3. **Write executable code**, not just a new plan. Deliver a working Streamlit app with synthetic fixtures, SQLite persistence, and tests.
4. Begin by verifying DNotifier Python SDK authentication and one **real** `send_ai` call. The SDK publicly documents `app_id` + `secret`; the hackathon credential may be described as an API key, so don't assume the formats are interchangeable.
5. Use DNotifier for AI budget analysis / invoice text interpretation and WebSocket expense alerts. Keep all SDK-specific behavior in an adapter; inspect response shapes from the actual installed SDK.
6. Keep financial math deterministic in Python using integer minor currency units and proper transaction types. Require review before committing an invoice; prevent double counting with CSV imports.
7. Preserve the 3–4 hour MVP scope. Do not add bank logins, payment functions, or production auth. Use PKR, one demo user, and normalized CSVs.
8. Use synthetic financial data, protect secrets, and never describe simulated or mocked output as a successful live integration.
9. Run tests and provide exact run commands, completed features, blocked features, and honest SDK integration status at the end.
10. **Use installed Streamlit agent skills for all Streamlit work.** Before implementing, reviewing, or testing Streamlit UI, discover available Streamlit skills in the agent's skill catalog and project-local `.agents/skills/` or `.claude/skills/` (or the agent's global skill location). Load the applicable skills through the harness skill mechanism, or read their `SKILL.md` instructions if they are not exposed in the catalog; follow their guidance within this project's scope and constraints. Installation alone does not count as using a skill. During environment setup, after installing Streamlit, run `streamlit skills --yes` to install the official project-local skills if missing and supported by the installed CLI. If unavailable, document the exact blocker and use the official Streamlit documentation; do not claim skills were loaded. See https://docs.streamlit.io/develop/api-reference/cli/skills.
11. Target **Python 3.10.x only**, as declared in `.python-version` and `pyproject.toml`. Apply `constraints.txt` to dependency installation (include `-c constraints.txt` in the future `requirements.txt`). Use Python 3.10-compatible code and dependencies; do not use 3.11-only APIs such as `asyncio.Runner`, `TaskGroup`, or `tomllib`. Verify dependency installation, startup, and tests under Python 3.10; report incompatibilities rather than silently changing the runtime target.

**Minimum demo:** Import two months of spending CSV → see budgets → DNotifier AI reviews month → upload and confirm invoice → expense total changes → a real DNotifier alert reaches a separate subscriber.
