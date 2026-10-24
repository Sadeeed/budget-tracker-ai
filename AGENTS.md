# Agent instructions — BudgetPilot

You are implementing the **BudgetPilot DNotifier hackathon MVP**.

1. Read `DESIGN.md` completely; it is the technical source of truth.
2. Follow `TODO.md` in order, checking off completed work and noting deviations.
3. **Write executable code**, not just a new plan. Deliver a working Streamlit app with synthetic fixtures, SQLite persistence, and tests.
4. Begin by verifying DNotifier Python SDK authentication and one **real** `send_ai` call. The SDK publicly documents `app_id` + `secret`; the hackathon credential may be described as an API key, so don't assume the formats are interchangeable.
5. Use DNotifier for AI budget analysis / invoice text interpretation and WebSocket expense alerts. Keep all SDK-specific behavior in an adapter; inspect response shapes from the actual installed SDK.
6. Keep financial math deterministic in Python using integer minor currency units and proper transaction types. Require review before committing an invoice; prevent double counting with CSV imports.
7. Preserve the 3–4 hour MVP scope. Do not add bank logins, payment functions, or production auth. Use PKR, one demo user, and normalized CSVs.
8. Use synthetic financial data, protect secrets, and never describe simulated or mocked output as a successful live integration.
9. Run tests and provide exact run commands, completed features, blocked features, and honest SDK integration status at the end.

**Minimum demo:** Import two months of spending CSV → see budgets → DNotifier AI reviews month → upload and confirm invoice → expense total changes → a real DNotifier alert reaches a separate subscriber.
