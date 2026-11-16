# Changelog

What changed in agd-action. The next version's changes are collected here; each released version's notes are on its GitHub release.

Version numbers follow agd's: a new agd patch release makes a patch release of the action, a minor one a minor release. A new agd major version, or a renamed or removed input or output, makes a major release.

## 1.0.0 (2026-11-16)

- The first release. `uses: Zejnilovic/agd-action@v1` runs `agd update` with the agd release it pins and opens or updates a pull request from `agd-update`, committed through GitHub's API so the commit is signed. Inputs `mode`, `branch` and `token`; outputs `due`, `pr-number` and `pr-url`. It replaces the workflow that agd's [automatic-updates guide](https://zejnilovic.github.io/agd/guides/automatic-updates/) had you copy.
