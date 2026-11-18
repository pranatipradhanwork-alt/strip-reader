#!/usr/bin/env bash
# Upload the committed code to the Hugging Face Space (Docker). Secrets are set in the Space settings, not here.
#   ./deploy/push_space.sh                 # default Space: pranatipradhan123/strip-reader
#   ./deploy/push_space.sh user/space-name
set -euo pipefail
SPACE="${1:-pranatipradhan123/strip-reader}"
cd "$(dirname "$0")/.."
[ -z "$(git status --porcelain -- strip_reader data requirements.txt Dockerfile)" ] || { echo "Commit your changes first."; exit 1; }
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
git archive HEAD | tar -x -C "$STAGE"          # only committed files: never .env, samples or readings
cat deploy/space_header.md README.md > "$STAGE/README.md"
rm -rf "$STAGE/tests" "$STAGE/.github" "$STAGE/samples"
hf upload "$SPACE" "$STAGE" . --repo-type space --commit-message "Deploy $(git rev-parse --short HEAD)"
echo "Uploaded. Build log: https://huggingface.co/spaces/$SPACE?logs=build"
echo "App: https://$(echo "$SPACE" | tr '/' '-').hf.space"
