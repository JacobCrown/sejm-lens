#!/usr/bin/env bash
# Copies the local docker-compose database (schema + data) into Neon, replacing what is there.
#
#   NEON_DATABASE_URL='postgres://USER:PASS@HOST/neondb?sslmode=require' scripts/seed_neon.sh
#
# Accounts, sessions and the admin log are NOT copied. Create a production superuser afterwards:
#   DATABASE_URL='<neon url>' uv run python manage.py createsuperuser
set -euo pipefail

: "${NEON_DATABASE_URL:?Set NEON_DATABASE_URL to the Neon connection string (direct, with sslmode=require)}"

cd "$(dirname "$0")/.."

if [[ "${CONFIRM:-}" != "yes" ]]; then
  echo "This REPLACES all data in the database behind NEON_DATABASE_URL."
  read -r -p "Type 'yes' to continue: " answer
  [[ "$answer" == "yes" ]] || { echo "Aborted."; exit 1; }
fi

DUMP="$(mktemp)"
trap 'rm -f "$DUMP"' EXIT

echo "1/2 Dumping the local database (docker compose service 'db')..."
# pg_dump runs inside the same postgres:16 container as the local DB, so the client version matches.
docker compose exec -T db pg_dump -U sejm -d sejmwatch \
  --clean --if-exists --no-owner --no-privileges \
  --exclude-table-data=auth_user \
  --exclude-table-data=auth_user_groups \
  --exclude-table-data=auth_user_user_permissions \
  --exclude-table-data=django_session \
  --exclude-table-data=django_admin_log \
  > "$DUMP"

echo "2/2 Restoring into Neon..."
# Uses a postgres:16 psql image, so no local PostgreSQL client is needed.
docker run --rm -i postgres:16 psql "$NEON_DATABASE_URL" -v ON_ERROR_STOP=1 -q < "$DUMP"

echo "Done. Neon now has the local data. Deploy with: git push origin prod"
