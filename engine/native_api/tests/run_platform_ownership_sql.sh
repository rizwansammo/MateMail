#!/usr/bin/env bash
# Only a disposable PostgreSQL cluster. Never connect to an existing DB.
set -euo pipefail
command -v pg_virtualenv >/dev/null || {
  echo "pg_virtualenv is required; do not point this script at production" >&2
  exit 2
}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATIONS="$(cd "$HERE/../migrations" && pwd)"
export HERE MIGRATIONS
pg_virtualenv -v "${PG_TEST_VERSION:-18}" bash -c '
  set -euo pipefail
  for migration in "$MIGRATIONS"/00[1-6]_*.sql; do
    psql -X -q -v ON_ERROR_STOP=1 -f "$migration" >/dev/null
  done
  psql -X -q -v ON_ERROR_STOP=1 -f "$HERE/platform_ownership_seed.sql" >/dev/null
  psql -X -q -v ON_ERROR_STOP=1 -f "$MIGRATIONS/007_platform_ownership_guards.sql" >/dev/null
  psql -X -q -v ON_ERROR_STOP=1 -f "$HERE/platform_ownership_verify.sql"
'
