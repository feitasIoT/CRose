#!/bin/sh

DUMP_FILE="/docker-entrypoint-initdb-src/init.sql"

if [ ! -f "$DUMP_FILE" ]; then
  return 0 2>/dev/null || exit 0
fi

tr -d '\r' < "$DUMP_FILE" \
  | sed -e '/^\\restrict/d' -e '/^\\unrestrict/d' \
  | psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"
