#!/usr/bin/env sh
set -eu

(
  cd backend
  .venv/bin/ruff check .
  .venv/bin/pytest
)

(
  cd frontend
  npm run lint
  npm run typecheck
  npm test
  npm run build
)
