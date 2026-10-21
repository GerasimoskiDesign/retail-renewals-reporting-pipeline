# Build plan

## Repo tree

```
retail-renewals-reporting-pipeline/
├── .github/workflows/ci.yml        ruff + pytest (incl. e2e with Chromium)
├── config/users.yaml               demo dashboard users (bcrypt hashes)
├── data/TRAPS.md                   the four injected data-quality traps
├── docs/img/                       charts + dashboard screenshots
├── src/renewals/
│   ├── __main__.py / cli.py        python -m renewals <command>
│   ├── settings.py                 paths + .env loading
│   ├── world/                      seeded synthetic world (shops, agents, processes)
│   ├── portal/                     report builders + FastAPI mock BI and CRM portals
│   ├── downloader/                 Playwright bot, Monday logic, naming, retry
│   ├── transform/                  read → normalise → dedupe → channel → metrics, Excel
│   ├── validation/                 invariants + naive (old manual) logic
│   ├── orchestrator.py             run --as-of: download missing → transform → validate → publish
│   ├── dashboard/                  auth, permission-enforcing data access, Streamlit app
│   └── reporting/                  README charts, results summary, screenshots
├── tests/                          pytest suite (unit + e2e marker)
├── pyproject.toml, README.md, LICENSE, .env.example, .gitignore
```

## Build order

1. Scaffold: pyproject, settings, CLI skeleton, .gitignore, .env.example.
2. Synthetic world generator with traps + campaign week; `data/TRAPS.md`.
3. Report builders (6 reports) and FastAPI mock portals.
4. Downloader: date logic, naming, idempotent plan, retry, Playwright client.
5. Transform: normalise, dedupe rules, channel assignment, metrics, Parquet + Excel.
6. Validation layer + naive mode.
7. Orchestrator with timed summary.
8. Dashboard: auth, access layer, Streamlit UI.
9. Tests for everything listed in section H.
10. Charts, screenshots, results numbers, README.
11. Finish line: clean run, ruff, confidentiality scan, quickstart in fresh venv, commits, private repo, topics, CI green.
