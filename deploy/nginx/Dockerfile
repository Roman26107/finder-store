FROM nginx:1.27-alpine

RUN apk add --no-cache gettext
COPY nginx-entrypoint.sh /usr/local/bin/finder-nginx-entrypoint
COPY templates /etc/nginx/finder-templates
RUN sed -i 's/\r$//' /usr/local/bin/finder-nginx-entrypoint \
    /etc/nginx/finder-templates/*.conf \
    /etc/nginx/finder-templates/*.template \
    && chmod +x /usr/local/bin/finder-nginx-entrypoint

ENTRYPOINT ["/usr/local/bin/finder-nginx-entrypoint"]
CMD ["nginx", "-g", "daemon off;"]