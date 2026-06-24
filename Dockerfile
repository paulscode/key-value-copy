FROM alpine:3.20

RUN apk update && \
    apk add --no-cache tini python3 && \
    rm -f /var/cache/apk/*

# Copy static web files
RUN mkdir -p /var/www/html
COPY index.html /var/www/html/
COPY styles.css /var/www/html/
COPY app.js /var/www/html/
COPY favicon.svg /var/www/html/

# Copy server
COPY server.py /usr/local/bin/server.py

# Shared env-driven supervisor (used directly on StartOS 0.4.x and for plain
# `docker run`), plus the 0.3.x config.yaml-driven shim that execs it.
COPY docker_entrypoint_040.sh /usr/local/bin/docker_entrypoint_040.sh
COPY docker_entrypoint.sh /usr/local/bin/docker_entrypoint.sh
RUN chmod a+x /usr/local/bin/docker_entrypoint_040.sh \
    /usr/local/bin/docker_entrypoint.sh

EXPOSE 80

# tini reaps zombies; the supervisor launches the Python server. Used for plain
# `docker run`; StartOS invokes the entrypoints directly.
ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/docker_entrypoint_040.sh"]
