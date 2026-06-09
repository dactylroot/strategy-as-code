#!/bin/bash
set -e

PROJECT_DIR="${PROJECT_DIR:-/project}"

# Seed the volume from git on first deploy (volume is empty, git URL is set)
if [ ! -f "${PROJECT_DIR}/PRODUCT.MD" ] && [ -n "${GIT_REPO_URL}" ]; then
    echo "First run: seeding ${PROJECT_DIR} from ${GIT_REPO_URL}..."
    git clone "${GIT_REPO_URL}" "${PROJECT_DIR}"
    echo "Seed complete."
fi

# Configure git identity for sync commits
git config --global user.name  "${GIT_USER_NAME:-strategy-as-code}"
git config --global user.email "${GIT_USER_EMAIL:-noreply@strategy-as-code}"

# Store HTTPS token so git push works without a prompt
if [ -n "${GIT_TOKEN}" ] && [ -n "${GIT_REPO_URL}" ]; then
    REPO_HOST=$(echo "${GIT_REPO_URL}" | sed 's|https://||;s|/.*||')
    git config --global credential.helper store
    printf 'https://%s@%s\n' "${GIT_TOKEN}" "${REPO_HOST}" > /root/.git-credentials
    chmod 600 /root/.git-credentials
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
