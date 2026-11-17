FROM public.ecr.aws/docker/library/node:24.21.0-bookworm@sha256:3d27e5c11e5786e309ec3e03f93ae536eb36e6e5eb3714d5eb3300a36157add0
ARG LOOPX_REVISION
ARG LOOPX_SOURCE_SHA256
WORKDIR /opt/loopx
LABEL org.opencontainers.image.source="https://github.com/loopx-project/loopx" \
      org.opencontainers.image.revision="${LOOPX_REVISION}"
COPY loopx-source.tar.gz /tmp/loopx-source.tar.gz
RUN printf '%s  %s\n' "$LOOPX_SOURCE_SHA256" /tmp/loopx-source.tar.gz | sha256sum -c - \
    && tar -xzf /tmp/loopx-source.tar.gz \
    && rm /tmp/loopx-source.tar.gz \
    && command -v openssl \
    && npm ci --ignore-scripts --no-audit --no-fund --include=dev
COPY provision.mjs launch.mjs ./
USER 1000:1000
