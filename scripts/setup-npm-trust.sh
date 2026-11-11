#!/usr/bin/env bash
# One-time npm setup for .github/workflows/release-node.yml. Run it yourself:
# npm asks for your login and 2FA codes.
#
# For each package it reserves the name if it does not exist yet, lets the
# workflow's `npm` environment stage releases through trusted publishing (OIDC),
# and disallows token publishing. Safe to run again, for example after adding a
# platform to the release matrix.
set -euo pipefail

REPOSITORY=patelvivekdev/pdf-textlayer
# The main package and one package per platform in release-node.yml's matrix.
PACKAGES=(
  pdf-textlayer
  pdf-textlayer-darwin-arm64
  pdf-textlayer-darwin-x64
  pdf-textlayer-linux-x64-gnu
  pdf-textlayer-linux-arm64-gnu
  pdf-textlayer-linux-x64-musl
  pdf-textlayer-win32-x64-msvc
  pdf-textlayer-win32-arm64-msvc
)

npm whoami >/dev/null 2>&1 || npm login

for name in "${PACKAGES[@]}"; do
  echo "== $name"

  if ! npm view "$name" name >/dev/null 2>&1; then
    # Trusted publishing can only be set up on a package that exists. Staging a
    # new package creates a public 0.0.0-stage placeholder and nothing else.
    placeholder=$(mktemp -d)
    printf '{"name":"%s","version":"0.0.1","license":"MIT","repository":{"type":"git","url":"https://github.com/%s.git"}}\n' \
      "$name" "$REPOSITORY" >"$placeholder/package.json"
    (cd "$placeholder" && npm stage publish --access public)
    rm -rf "$placeholder"
  fi

  npm trust github "$name" \
    --file release-node.yml \
    --repository "$REPOSITORY" \
    --environment npm \
    --allow-stage-publish \
    --yes

  # Require 2FA to publish and refuse tokens; trusted publishing keeps working.
  npm access set mfa=publish "$name"
done

cat <<'EOF'

Done. Reject the staged 0.0.1 placeholders under Staged Packages on npmjs.com,
then check one package with: npm trust list pdf-textlayer
EOF
