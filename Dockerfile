FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6-dev libpng-dev pkg-config git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY entrypoint.sh ./entrypoint.sh
COPY app ./app
COPY scripts ./scripts

# No baked-in user: deployers run this as whatever UID owns their bind-mounted
# /project (docker-compose's `user:` override), which varies per host - a
# fixed UID here would fight that, as it did before (see renewals'
# docker-compose.prod.yml, which bind-mounts its whole checkout as /project).
#
# Since the runtime UID is unknown at build time, both problems this used to
# paper over with a single fixed `appuser` need a different fix:
#   - Source files copied above inherit whatever host permissions they had
#     (some checkouts use a restrictive umask), so make them world-readable.
#   - auth.yml/server.yml/git config are written under $HOME at runtime
#     (figtion configs, git credential store) - give any UID a writable home
#     rather than one baked-in user's home directory.
RUN chmod -R a+rX /app && chmod 755 entrypoint.sh
ENV HOME=/app/.home

# auth.py's own default is `enabled: False` (safe for a library default, but
# not for this image - a fresh deployment should come up password-protected,
# not open). Pre-seed the same enabled/admin/changeme default the old fixed
# `appuser` build used to bake in, at auth.py's now-writable-by-anyone default
# path so the app can still self-update it (secret_key generation on first
# run, and any settings the operator changes later).
RUN mkdir -p /app/.home/.config/strategy-as-code && \
    printf 'enabled: true\nusername: admin\npassword: changeme\nsecret_key: ""\n' \
    > /app/.home/.config/strategy-as-code/auth.yml && \
    chmod 777 /app/.home /app/.home/.config /app/.home/.config/strategy-as-code && \
    chmod 666 /app/.home/.config/strategy-as-code/auth.yml

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
