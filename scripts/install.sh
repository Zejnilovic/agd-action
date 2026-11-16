#!/usr/bin/env bash
# Installs the agd release that agd-release names, and checks the download against its SHA-256.
# agd's Release workflow rewrites agd-release, so each action release runs one known agd.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"

version=$(awk '$1 == "version" { print $2 }' "$here/agd-release")
checksum=$(awk '$1 == "sha256" { print $2 }' "$here/agd-release")
if [ -z "$version" ] || [ -z "$checksum" ]; then
  echo "::error::agd-release must name a version and a sha256."
  exit 1
fi

bin="$RUNNER_TEMP/agd-action-bin"
mkdir -p "$bin"
curl -fsSLo "$bin/agd" "https://github.com/Zejnilovic/agd/releases/download/v$version/agd"
if command -v sha256sum > /dev/null; then
  got=$(sha256sum "$bin/agd" | cut -d' ' -f1)
else
  got=$(shasum -a 256 "$bin/agd" | cut -d' ' -f1)
fi
if [ "$got" != "$checksum" ]; then
  rm -f "$bin/agd"
  echo "::error::The agd $version download's SHA-256 is $got, not $checksum as agd-release says."
  exit 1
fi
chmod +x "$bin/agd"
echo "$bin" >> "$GITHUB_PATH"
echo "Installed agd $version."
