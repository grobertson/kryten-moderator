# Tooling Upgrade — Agent TODO

**Status:** Pending. This repo runs **years-old linters/formatters** and should be bumped to
match the rest of the Kryten ecosystem. Left as a standalone task because a black major bump
reformats widely and must not be mixed into feature diffs.

## Current vs target

| Tool  | This repo (locked)      | Ecosystem (e.g. kryten-llm) |
|-------|-------------------------|------------------------------|
| ruff  | **0.1.15** (Dec 2023)   | 0.16.x |
| black | **23.12.1** (Dec 2023)  | 24.10.x |

CI (`.github/workflows/ci.yml`) runs `uv run ruff check .`, `uv run black --check .`,
`uv run mypy .`, and `uv run pytest`; versions come from `pyproject.toml` + `uv.lock`. The
pre-commit hooks (`.pre-commit-config.yaml`) call the same `uv run` tools, so bumping here
fixes local commits and CI together.

## Upgrade procedure (do as an ISOLATED commit — no feature work mixed in)

1. In `pyproject.toml` `[dependency-groups] dev`, bump the constraints to the ecosystem pins
   (check `../kryten-llm/pyproject.toml` for the current values), e.g.:
   - `ruff>=0.14.0,<1.0.0`
   - `black>=24.0.0,<25.0.0`
2. `uv lock` then `uv sync` to re-resolve the environment.
3. `uv run ruff check --fix .` — apply the newer safe autofixes.
4. `uv run black .` — **black 23 → 24 changes the stable style and will reformat many files.**
   A large, noisy diff is expected; keep it contained in this one commit.
5. Resolve anything the newer tools surface:
   - New ruff rules may flag issues — fix them, or add targeted entries under
     `[tool.ruff.lint.per-file-ignores]` if intentional.
   - Newer mypy (if also bumped) may add errors — fix with `cast(...)` / annotations
     (no runtime change), same pattern used elsewhere in the ecosystem.
6. Run the full gate until clean (match `ci.yml`):
   ```
   uv run ruff check .
   uv run black --check .
   uv run mypy .
   uv run pytest
   ```
7. Commit standalone, e.g. `chore: bump ruff/black to ecosystem versions`, and push.

## Why isolated?
The black major bump reformats widely. Landing it alone keeps the churn out of feature diffs
and makes the reformat trivially reviewable and revertable.
