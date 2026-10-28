#!/bin/sh
# ---------------------------------------------------------------------------
#  Caddy container entrypoint — makes the ACME account e-mail optional.
#
#  A Caddyfile `email` directive without an argument is a parse error, and that is
#  what `email {$ACME_EMAIL}` turns into when ACME_EMAIL is empty or unset (the
#  address only receives Let's Encrypt expiry notices; Caddy works without it). So
#  the Caddyfile `import`s acme-email.caddy from its own directory and this script
#  writes that file at every start: `email "<address>"` when ACME_EMAIL holds an
#  address, only a comment otherwise. A file on disk, not an exported variable, so
#  `docker compose exec caddy caddy reload` sees the same thing the server started
#  with. The image ships the comment-only version.
#
#  A value that does not look like an e-mail address is ignored with a warning (it
#  would be a syntax error, or more, in the Caddyfile) — the site still comes up.
# ---------------------------------------------------------------------------
set -eu

SNIPPET="${CADDY_ACME_EMAIL_FILE:-/etc/caddy/acme-email.caddy}"

# surrounding blanks go (a .env with CRLF line endings leaves a \r behind)
email="${ACME_EMAIL:-}"
email="${email#"${email%%[![:space:]]*}"}"
email="${email%"${email##*[![:space:]]}"}"

# letters, digits and . _ % + - around exactly one @ — nothing the Caddyfile syntax treats specially
valid_email() {
  case "$1" in
    *[!A-Za-z0-9@._%+-]*) return 1 ;;
    @* | *@ | *@*@*) return 1 ;;
    *@?*) return 0 ;;
    *) return 1 ;;
  esac
}

directive=""
if [ -n "$email" ]; then
  if valid_email "$email"; then
    directive="email \"$email\""
  else
    echo "[caddy-entrypoint] ACME_EMAIL does not look like an e-mail address — starting without an ACME account e-mail" >&2
  fi
fi

{
  echo "# generated at container start by caddy-entrypoint.sh from ACME_EMAIL"
  if [ -n "$directive" ]; then
    echo "$directive"
  else
    echo "# (no ACME_EMAIL: certificates are issued without an account e-mail)"
  fi
} >"$SNIPPET" 2>/dev/null || echo "[caddy-entrypoint] cannot write $SNIPPET — starting with the file from the image" >&2

exec "$@"
