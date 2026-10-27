#!/bin/sh
set -eu

DOMAIN="${DOMAIN:-localhost}"
export DOMAIN

if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] \
  && [ -f "/etc/letsencrypt/live/$DOMAIN/privkey.pem" ]; then
  TEMPLATE=/etc/nginx/finder-templates/https.conf.template
else
  TEMPLATE=/etc/nginx/finder-templates/http.conf.template
fi

envsubst '${DOMAIN}' < "$TEMPLATE" > /etc/nginx/conf.d/default.conf
exec "$@"