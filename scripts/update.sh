#!/usr/bin/env bash
# Runs agd update, commits the result to $BRANCH through GitHub's API and opens or updates its pull request.
# Sets the pr-number and pr-url outputs.
set -euo pipefail

agd update | tee "$RUNNER_TEMP/update.txt"
# Only files this project edited are due, and the update leaves those alone.
[ -n "$(git status --porcelain)" ] || exit 0

# The commit goes through GitHub's API, which signs it, so a ruleset that requires signed commits
# accepts it. Contents go to JSON files, not arguments: Linux limits one argument to 128 KiB, and a
# whole kit can be larger.
git add -A
: > "$RUNNER_TEMP/additions.jsonl"
: > "$RUNNER_TEMP/executable.txt"
git diff --cached --no-renames --name-only --diff-filter=AM -z | while IFS= read -r -d '' path; do
  base64 < "$path" | tr -d '\n' | jq -Rs --arg path "$path" '{path: $path, contents: .}' >> "$RUNNER_TEMP/additions.jsonl"
  [ ! -x "$path" ] || echo "$path" >> "$RUNNER_TEMP/executable.txt"
done
jq -s . "$RUNNER_TEMP/additions.jsonl" > "$RUNNER_TEMP/additions.json"
git diff --cached --no-renames --name-only --diff-filter=D -z \
  | while IFS= read -r -d '' path; do jq -n --arg path "$path" '{path: $path}'; done \
  | jq -s . > "$RUNNER_TEMP/deletions.json"

# The commit is made on a temporary branch, then $BRANCH moves to it in one step. Resetting $BRANCH
# first would leave it without commits for a moment, and GitHub closes an open pull request whose
# branch has none. The trap deletes the temporary branch, also after a failure.
base=$(git rev-parse HEAD)
tmp="$BRANCH-tmp-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
gh api -X POST "repos/$GITHUB_REPOSITORY/git/refs" -f ref="refs/heads/$tmp" -f sha="$base" > /dev/null
trap 'gh api -X DELETE "repos/$GITHUB_REPOSITORY/git/refs/heads/$tmp" > /dev/null 2>&1 || true' EXIT
commit=$(jq -n --arg repo "$GITHUB_REPOSITORY" --arg branch "$tmp" --arg oid "$base" \
  --arg headline "Update the agentic setup" --rawfile body "$RUNNER_TEMP/update.txt" \
  --slurpfile add "$RUNNER_TEMP/additions.json" --slurpfile del "$RUNNER_TEMP/deletions.json" \
  '{query: "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }",
    variables: {input: {branch: {repositoryNameWithOwner: $repo, branchName: $branch},
      expectedHeadOid: $oid, message: {headline: $headline, body: $body},
      fileChanges: {additions: $add[0], deletions: $del[0]}}}}' \
  | gh api graphql --input - --jq '.data.createCommitOnBranch.commit.oid')
[ -n "$commit" ] || { echo "::error::GitHub's API made no commit."; exit 1; }
if gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/$BRANCH" > /dev/null 2>&1; then
  gh api -X PATCH "repos/$GITHUB_REPOSITORY/git/refs/heads/$BRANCH" -f sha="$commit" -F force=true > /dev/null
else
  gh api -X POST "repos/$GITHUB_REPOSITORY/git/refs" -f ref="refs/heads/$BRANCH" -f sha="$commit" > /dev/null
fi

{
  echo '`agd update` changed these files. Read the changes before you merge: they are instructions for agents.'
  echo
  echo '```text'
  cat "$RUNNER_TEMP/update.txt"
  echo '```'
  echo
  echo 'An `edited` line is a file this project changed, which the update left alone. Settle it after you merge, with `agd diff`, then `agd merge` or `agd reset`: the next run replaces this branch.'
  if [ -s "$RUNNER_TEMP/executable.txt" ]; then
    echo
    echo 'The commit carries no file modes, so these executable files may lose their executable bit. Check their mode in the diff, and after you merge, restore it with `git update-index --chmod=+x <path>`:'
    echo
    sed 's/.*/- `&`/' "$RUNNER_TEMP/executable.txt"
  fi
} > "$RUNNER_TEMP/body.md"

# "// empty": with no open pull request, .[0] is null, and the string would read "null<tab>null".
pr=$(gh pr list --head "$BRANCH" --state open --json number,url --jq '.[0] // empty | "\(.number)\t\(.url)"')
if [ -n "$pr" ]; then
  number=${pr%%$'\t'*} url=${pr#*$'\t'}
  gh pr edit "$number" --body-file "$RUNNER_TEMP/body.md" > /dev/null
else
  url=$(gh pr create --head "$BRANCH" --title "Update the agentic setup" --body-file "$RUNNER_TEMP/body.md")
  number=${url##*/}
fi
{ echo "pr-number=$number"; echo "pr-url=$url"; } >> "$GITHUB_OUTPUT"
echo "Pull request: $url"
