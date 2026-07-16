#!/bin/bash
set -e

PROJECT_DIR="${PROJECT_DIR:-/project}"

# Never let git fall back to an interactive credential prompt: this container is
# headless (and runs as an arbitrary host UID with no /etc/passwd entry), so a
# prompt just dies with "could not read Username for 'https://github.com': No
# such device or address" and takes the content-sync push down with it. Fail
# fast and loud instead.
export GIT_TERMINAL_PROMPT=0

# Seed the volume from git on first deploy (volume is empty, git URL is set)
if [ ! -f "${PROJECT_DIR}/PRODUCT.MD" ] && [ -n "${GIT_REPO_URL}" ]; then
    echo "First run: seeding ${PROJECT_DIR} from ${GIT_REPO_URL}..."
    git clone "${GIT_REPO_URL}" "${PROJECT_DIR}"
    echo "Seed complete."
fi

# Configure git identity for sync commits
git config --global user.name  "${GIT_USER_NAME:-strategy-as-code}"
git config --global user.email "${GIT_USER_EMAIL:-noreply@strategy-as-code}"

# Build an authenticated push URL by embedding the token in the userinfo, e.g.
# https://x-access-token:<TOKEN>@github.com/owner/repo.git. This is what the
# content-sync remote below is pointed at, so the push authenticates from the
# URL itself and does NOT depend on a credential helper resolving the right HOME
# for whatever UID the container was launched as - the exact gap that was
# silently breaking content-sync in prod. `x-access-token` is GitHub's accepted
# basic-auth username for a PAT/installation token (the token can be either the
# username or the password; naming the username explicitly keeps the line a
# well-formed credential rather than a token-as-username-with-empty-password).
AUTH_REPO_URL="${GIT_REPO_URL}"
if [ -n "${GIT_TOKEN}" ] && [ -n "${GIT_REPO_URL}" ]; then
    case "${GIT_REPO_URL}" in
        https://*@*)  AUTH_REPO_URL="${GIT_REPO_URL}" ;;  # already has userinfo; leave as-is
        https://*)    AUTH_REPO_URL="https://x-access-token:${GIT_TOKEN}@${GIT_REPO_URL#https://}" ;;
    esac
    # Keep the credential store as a secondary path (harmless if unused).
    REPO_HOST=$(echo "${GIT_REPO_URL}" | sed 's|https://||;s|/.*||')
    git config --global credential.helper store
    printf 'https://x-access-token:%s@%s\n' "${GIT_TOKEN}" "${REPO_HOST}" > "${HOME}/.git-credentials"
    chmod 600 "${HOME}/.git-credentials"
fi

# Ensure a dedicated remote exists for content-sync pushes (see app/git_sync.py),
# separate from whatever remote(s) the host checkout already has configured -
# e.g. an SSH-based origin used for the normal deploy flow, which the
# HTTPS-token credential store above can't authenticate against anyway. Point it
# at the token-embedded URL so the headless push authenticates without a prompt.
if [ -n "${GIT_REPO_URL}" ] && [ -d "${PROJECT_DIR}/.git" ]; then
    SYNC_REMOTE="${GIT_SYNC_REMOTE:-content-sync-origin}"
    git -C "${PROJECT_DIR}" remote add "${SYNC_REMOTE}" "${AUTH_REPO_URL}" 2>/dev/null \
        || git -C "${PROJECT_DIR}" remote set-url "${SYNC_REMOTE}" "${AUTH_REPO_URL}"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
