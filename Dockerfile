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

ADD ./docker_entrypoint.sh /usr/local/bin/docker_entrypoint.sh
RUN chmod a+x /usr/local/bin/docker_entrypoint.sh
