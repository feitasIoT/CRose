#!/bin/sh

DUMP_FILE="/docker-entrypoint-initdb.d/init.sql"

if [ ! -f "$DUMP_FILE" ]; then
  return 0 2>/dev/null || exit 0
fi

tmp_file="$(mktemp)"

tr -d '\r' < "$DUMP_FILE" | sed -e '/^\\restrict/d' -e '/^\\unrestrict/d' > "$tmp_file"

mv "$tmp_file" "$DUMP_FILE"
