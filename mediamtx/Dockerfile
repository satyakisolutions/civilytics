# bluenviron/mediamtx:latest ships a single static binary with no shell
# at all -- no /bin/sh, no wget/curl, nothing else in the image. That
# makes any CMD-SHELL healthcheck impossible (confirmed: "stat /bin/sh:
# no such file or directory"), so its dependents could only wait for it
# to *start*, not to actually be ready to accept connections. This
# rewraps the same real binary into a base that has wget, restoring a
# proper readiness check.
FROM bluenviron/mediamtx:latest AS upstream

FROM alpine:3.20
RUN apk add --no-cache wget
COPY --from=upstream /mediamtx /mediamtx

ENTRYPOINT ["/mediamtx"]
