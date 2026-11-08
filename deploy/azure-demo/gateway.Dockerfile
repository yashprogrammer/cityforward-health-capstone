FROM caddy:2.10.2-alpine
# High port 8080 needs no file capability. Keep the pod's drop-ALL policy.
RUN setcap -r /usr/bin/caddy
USER 10001:10001
