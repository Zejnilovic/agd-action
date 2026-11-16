# agd-action

Keeps a project's agentic setup up to date with [agd](https://zejnilovic.github.io/agd/). When a kit the project installed has a newer release, the action runs `agd update`, commits the result to the `agd-update` branch and opens a pull request, or updates the one that is open. The commit is signed by GitHub, so rulesets that require signed commits accept it.

## Use it

Add `.github/workflows/agd-update.yml`:

```yaml
name: Update the agentic setup

on:
  schedule:
    - cron: "0 7 * * 1" # Mondays
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with:
          persist-credentials: false
      - uses: Zejnilovic/agd-action@v1
```

Then, in **Settings → Actions → General**, under "Workflow permissions", turn on **Allow GitHub Actions to create and approve pull requests**. In an organization, the organization's settings may have to allow it first. Without it, the branch is updated and the run fails at `createPullRequest`.

The pull request's body is what `agd update` printed. Read it before you merge: the files are instructions for your agents. [Keep projects up to date automatically](https://zejnilovic.github.io/agd/guides/automatic-updates/) explains what the pull request shows and how to settle an `edited` file.

## Inputs

| Input | Default | What it does |
| --- | --- | --- |
| `mode` | `pull-request` | `pull-request` updates and opens or updates the pull request. `check` only sets the `due` output. |
| `branch` | `agd-update` | The branch the update is committed to. Each run replaces it. |
| `token` | `github.token` | The token that commits and opens the pull request. |

## Outputs

| Output | Value |
| --- | --- |
| `due` | `true` when `agd status --check` reports an update due, else `false`. A file your project edited that has a newer version counts, though the update leaves it alone. |
| `pr-number` | The pull request opened or updated, if any. |
| `pr-url` | Its URL. |

In `check` mode the action never fails because an update is due. To fail a job on it, add a step with `if: steps.<id>.outputs.due == 'true'`.

## Which agd it runs

Each release of the action installs one agd release and checks the download against its SHA-256. agd's own release moves the action: agd 0.5.3 becomes a patch release of the action, agd 0.6.0 a minor one. A new agd major version, which may need manual steps in projects, comes as a new major version of the action, so `@v1` never jumps a major.

`@v1` follows each release. To review each agd version first, pin the action to a commit and let Dependabot propose the next one:

```yaml
      - uses: Zejnilovic/agd-action@<commit sha> # v1.0.0
```

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
```

## Good to know

- **Checks on the pull request.** GitHub doesn't run your checks on its own for a commit made with the workflow's token: they wait as **Action required** until you click **Approve and run**. With a GitHub App's or a person's token as `token`, they run without that step.
- **Executable files.** GitHub's API commits files without their mode, so a kit file agd installs as executable may lose that bit. The pull request lists such files.
- **Private sources.** Give git a token that can read the source in a step before the action, as [the FAQ shows](https://zejnilovic.github.io/agd/faq/#can-a-source-be-private).
- **Runners.** GitHub's Linux and macOS runners have what the action needs: bash, git, curl, jq and gh. A self-hosted runner needs them too.
- **Only allowed actions.** If your organization runs only actions it allows, fork this repository into it and use the fork.

## Releasing

Only the maintainer releases. Describe the change in a `## <version> (unreleased)` entry of `CHANGELOG.md`, push it to `main`, then run the **Release** workflow, first as a dry run:

```bash
gh workflow run release.yml -f dry_run=true
```

It runs the tests, dates the entry, tags `v<version>`, moves the major tag (`v1`) to it, creates the GitHub release, and then runs the action from `@v1`. To list the version on the Marketplace, edit the release and tick **Publish this Action to the GitHub Marketplace**: GitHub has no API for it.

## License

MIT
