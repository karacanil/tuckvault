#!/usr/bin/env bash
# Settings GitHub only allows on a public repository. Run once, right after
# switching the repository to public (needs the GitHub CLI, signed in as the owner):
#
#     bash tools/after_going_public.sh
set -u
R=repos/karacanil/tuckvault

echo "Private vulnerability reporting"
gh api -X PUT "$R/private-vulnerability-reporting" >/dev/null && echo "  on"

echo "Pull requests from outside contributors need approval before workflows run"
gh api -X PUT "$R/actions/permissions/fork-pr-contributor-approval" \
  -f approval_policy=all_external_contributors >/dev/null && echo "  on"

echo "Secret scanning with push protection"
gh api -X PATCH "$R" --input - >/dev/null <<'JSON' && echo "  on"
{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}
JSON

echo "Protect main: tests must pass, no force pushes, no deletion"
gh api -X PUT "$R/branches/main/protection" --input - >/dev/null <<'JSON' && echo "  on"
{"required_status_checks":{"strict":false,"contexts":["test"]},"enforce_admins":false,"required_pull_request_reviews":null,"restrictions":null,"allow_force_pushes":false,"allow_deletions":false}
JSON
