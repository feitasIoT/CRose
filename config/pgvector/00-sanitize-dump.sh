#!/bin/sh

DUMP_FILE="/docker-entrypoint-initdb-src/init.sql"

if [ ! -f "$DUMP_FILE" ]; then
  return 0 2>/dev/null || exit 0
fi

tr -d '\r' < "$DUMP_FILE" \
  | awk '
      BEGIN { skip_comment_on = 0 }
      /^\\restrict/ { next }
      /^\\unrestrict/ { next }
      skip_comment_on {
        if ($0 ~ /;[[:space:]]*$/) {
          skip_comment_on = 0
        }
        next
      }
      /^COMMENT ON / {
        if ($0 !~ /;[[:space:]]*$/) {
          skip_comment_on = 1
        }
        next
      }
      { print }
    ' \
  | psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"
