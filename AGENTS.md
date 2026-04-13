# AGENTS

## Repo layout (actual)
- `frontend/`: Angular 21 standalone app (npm workspace is local to this folder).
- `backend/`: single-file FastAPI service in `backend/main.py`.
- No root workspace/CI/pre-commit config found.

## Run commands (verified)
- Frontend (from `frontend/`):
  - `npm install`
  - `npm run start` (alias of `ng serve`, serves on `http://localhost:4200`)
  - `npm run build`
  - `npm run test` (Angular unit tests via `@angular/build:unit-test` + Vitest types)
- Backend (from `backend/`):
  - `python main.py` (starts uvicorn on `0.0.0.0:8000`)

## Verification workflow for edits
- Frontend: run `npm run build` then `npm run test`.
- Backend: no test/lint/typecheck tooling is configured in repo; do at least a startup smoke check with `python main.py`.

## Integration assumptions / gotchas
- Frontend hardcodes backend URL `http://localhost:8000` in:
  - `frontend/src/app/services/auth.ts`
  - `frontend/src/app/services/event.ts`
- Backend CORS only allows `http://localhost:4200` (`backend/main.py`).
- OAuth env vars are required by backend auth flow; copy from `backend/.env.example`.
- `backend/` currently has no `requirements.txt`/`pyproject.toml`; dependencies are implied by imports and local `.venv`.

## Style/conventions seen in repo
- Angular uses standalone bootstrap (`frontend/src/main.ts`) and signals (see `frontend/src/app/app.ts`).
- Formatting: 2-space indentation, single quotes in TS (`frontend/.editorconfig`, `frontend/.prettierrc`).
- Keep changes minimal; avoid adding new tooling/config unless requested.
