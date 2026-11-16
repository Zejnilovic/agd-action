"""Behavior tests for the action's scripts, with fake curl, gh and agd. Run: python3 -m unittest discover -s tests"""

import base64
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
# macOS ships bash 3.2 as /bin/bash; the scripts must work with it.
BASH = "/bin/bash" if sys.platform == "darwin" else "bash"

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

# Logs each call; serves the file named by $FAKE/download for -o.
FAKE_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE/curl.log"
while [ $# -gt 0 ]; do
  case "$1" in -fsSLo|-o) out=$2; shift ;; esac
  shift
done
cp "$FAKE/download" "$out"
"""

# Answers the gh calls of update.sh and logs each one; a graphql call's input goes to graphql.json.
FAKE_GH = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE/gh.log"
case "$*" in
  "api graphql --input - --jq "*) cat > "$FAKE/graphql.json"; cat "$FAKE/oid" ;;
  "api repos/owner/repo/git/ref/heads/"*) [ -f "$FAKE/branch-exists" ] ;;
  # The open pull requests are $FAKE/prs.json (none by default), filtered with the real --jq expression.
  "pr list "*)
    while [ "$1" != --jq ]; do shift; done
    [ -f "$FAKE/prs.json" ] || echo '[]' > "$FAKE/prs.json"
    jq -r "$2" "$FAKE/prs.json" ;;
  "pr create "*) touch "$FAKE/pr-created"; echo "https://github.com/owner/repo/pull/13" ;;
esac
"""

# agd status --check exits with $FAKE/status (default 1); agd update changes the project as an update would.
FAKE_AGD = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE/agd.log"
case "$1 $2" in
  "status --check") echo "agentic-defaults 1.1.0 is out"; exit "$(cat "$FAKE/status" 2>/dev/null || echo 1)" ;;
esac
[ "$1" = update ] || exit 9
if [ -f "$FAKE/only-edited" ]; then
  echo "edited   AGENTS.md: left alone"
  exit 0
fi
echo changed > kept.md
rm gone.md
mkdir -p "new dir" && printf 'a\\nb\\n' > "new dir/a file.md"
printf '#!/bin/sh\\n' > run.sh && chmod +x run.sh
echo "kit is at 1.1.0: 1 updated, 2 added, 1 removed."
"""


def run(args, cwd, env=None):
    return subprocess.run(args, cwd=cwd, env=env or GIT_ENV, capture_output=True, text=True)


class ScriptCase(unittest.TestCase):
    """A temporary project, a copy of the action, fake commands first on PATH, and the runner's files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.project, self.fake, self.temp, self.action = root / "project", root / "fake", root / "runner", root / "action"
        for folder in (self.project, self.fake, self.temp, root / "bin"):
            folder.mkdir()
        # A copy, so a test can write its own agd-release.
        shutil.copytree(REPO / "scripts", self.action / "scripts")
        shutil.copy(REPO / "agd-release", self.action / "agd-release")
        for name, text in (("curl", FAKE_CURL), ("gh", FAKE_GH), ("agd", FAKE_AGD)):
            (root / "bin" / name).write_text(text)
            (root / "bin" / name).chmod(0o755)
        self.output, self.path_file = self.temp / "output", self.temp / "path"
        self.env = {**GIT_ENV, "PATH": f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}", "FAKE": str(self.fake),
                    "RUNNER_TEMP": str(self.temp), "GITHUB_OUTPUT": str(self.output), "GITHUB_PATH": str(self.path_file),
                    "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "1",
                    "BRANCH": "agd-update", "MODE": "pull-request"}

    def tearDown(self):
        self.tmp.cleanup()

    def script(self, name):
        # GitHub runs a composite step with shell: bash as bash --noprofile --norc -eo pipefail.
        return run([BASH, "--noprofile", "--norc", "-eo", "pipefail", "-c", f'bash "{self.action}/scripts/{name}"'],
                   self.project, self.env)

    def outputs(self):
        if not self.output.exists():
            return {}
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines())

    def log(self, name):
        path = self.fake / f"{name}.log"
        return path.read_text().splitlines() if path.exists() else []


class ContractTest(unittest.TestCase):
    """action.yml's inputs and outputs are a contract: renaming or removing one needs v2."""

    def test_inputs_and_outputs(self):
        text = (REPO / "action.yml").read_text()
        section = lambda name: re.search(rf"^{name}:\n((?:  .*\n|\n)*)", text, re.M).group(1)
        names = lambda block: re.findall(r"^  ([a-z-]+):", block, re.M)
        self.assertEqual(names(section("inputs")), ["mode", "branch", "token"])
        self.assertEqual(names(section("outputs")), ["due", "pr-number", "pr-url"])
        self.assertIn("using: composite", text)

    def test_inputs_reach_scripts_only_through_env(self):
        # ${{ inputs.x }} inside run: would be pasted into the script, so a branch name could run code.
        for line in (REPO / "action.yml").read_text().splitlines():
            if line.strip().startswith("run:"):
                self.assertNotIn("${{", line)

    def test_agd_release_names_a_version_and_its_checksum(self):
        lines = (REPO / "agd-release").read_text().splitlines()
        self.assertRegex(lines[0], r"^version [0-9]+\.[0-9]+\.[0-9]+$")
        self.assertRegex(lines[1], r"^sha256 [0-9a-f]{64}$")


class InstallTest(ScriptCase):
    def pin(self, content, version="1.2.3", checksum=None):
        (self.fake / "download").write_bytes(content)
        checksum = checksum or hashlib.sha256(content).hexdigest()
        (self.action / "agd-release").write_text(f"version {version}\nsha256 {checksum}\n")

    def test_installs_the_pinned_release(self):
        self.pin(b"#!/usr/bin/env bash\necho agd\n")
        result = self.script("install.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("https://github.com/Zejnilovic/agd/releases/download/v1.2.3/agd", self.log("curl")[0])
        folder = pathlib.Path(self.path_file.read_text().strip())
        self.assertTrue(os.access(folder / "agd", os.X_OK))

    def test_a_changed_download_fails_and_is_removed(self):
        self.pin(b"something else\n", checksum="0" * 64)
        result = self.script("install.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::The agd 1.2.3 download's SHA-256 is", result.stdout)
        self.assertFalse(self.path_file.exists())
        self.assertFalse(any(self.temp.rglob("agd")))

    def test_a_broken_agd_release_fails(self):
        (self.action / "agd-release").write_text("version 1.2.3\n")
        result = self.script("install.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::agd-release", result.stdout)
        self.assertEqual(self.log("curl"), [])


class CheckTest(ScriptCase):
    def test_due(self):
        result = self.script("check.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs(), {"due": "true"})

    def test_nothing_due(self):
        (self.fake / "status").write_text("0")
        self.assertEqual(self.script("check.sh").returncode, 0)
        self.assertEqual(self.outputs(), {"due": "false"})

    def test_check_mode_never_fails_on_due_and_calls_no_gh(self):
        self.env["MODE"] = "check"
        self.assertEqual(self.script("check.sh").returncode, 0)
        self.assertEqual(self.outputs(), {"due": "true"})
        self.assertEqual(self.log("gh"), [])

    def test_an_agd_error_fails_with_its_code(self):
        (self.fake / "status").write_text("2")
        result = self.script("check.sh")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.outputs(), {})

    def test_an_unknown_mode_fails_before_agd_runs(self):
        self.env["MODE"] = "push"
        result = self.script("check.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::mode must be pull-request or check, not 'push'.", result.stdout)
        self.assertEqual(self.log("agd"), [])


class UpdateTest(ScriptCase):
    """update.sh commits through GitHub's API, which signs the commit (Zejnilovic/agd#15)."""

    def setUp(self):
        super().setUp()
        (self.fake / "oid").write_text("c0ffee\n")
        (self.project / "kept.md").write_text("old\n")
        (self.project / "gone.md").write_text("x\n")
        run(["git", "init", "-q"], self.project)
        run(["git", "add", "-A"], self.project)
        run(["git", "commit", "-q", "-m", "start"], self.project)
        self.base = run(["git", "rev-parse", "HEAD"], self.project).stdout.strip()
        self.env["GH_TOKEN"] = "token"

    def test_commits_the_changes_through_the_api(self):
        result = self.script("update.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        variables = json.loads((self.fake / "graphql.json").read_text())["variables"]["input"]
        self.assertEqual(variables["branch"], {"repositoryNameWithOwner": "owner/repo",
                                               "branchName": "agd-update-tmp-7-1"})
        self.assertEqual(variables["expectedHeadOid"], self.base)
        self.assertEqual(variables["message"]["headline"], "Update the agentic setup")
        self.assertIn("kit is at 1.1.0", variables["message"]["body"])
        added = {a["path"]: base64.b64decode(a["contents"]).decode()
                 for a in variables["fileChanges"]["additions"]}
        self.assertEqual(added, {"kept.md": "changed\n", "new dir/a file.md": "a\nb\n", "run.sh": "#!/bin/sh\n"})
        self.assertEqual(variables["fileChanges"]["deletions"], [{"path": "gone.md"}])
        # Nothing is committed or pushed with git.
        self.assertEqual(run(["git", "rev-parse", "HEAD"], self.project).stdout.strip(), self.base)

    def test_a_new_branch_gets_a_pull_request(self):
        self.assertEqual(self.script("update.sh").returncode, 0)
        calls = self.log("gh")
        refs = "api -X POST repos/owner/repo/git/refs"
        self.assertEqual(calls[0], f"{refs} -f ref=refs/heads/agd-update-tmp-7-1 -f sha={self.base}")
        self.assertIn(f"{refs} -f ref=refs/heads/agd-update -f sha=c0ffee", calls)
        self.assertTrue((self.fake / "pr-created").exists())
        self.assertEqual(self.outputs(), {"pr-number": "13", "pr-url": "https://github.com/owner/repo/pull/13"})
        self.assertEqual(calls[-1],"api -X DELETE repos/owner/repo/git/refs/heads/agd-update-tmp-7-1")

    def test_an_existing_branch_moves_and_its_pull_request_is_updated(self):
        (self.fake / "branch-exists").touch()
        (self.fake / "prs.json").write_text('[{"number": 12, "url": "https://github.com/owner/repo/pull/12"}]')
        self.assertEqual(self.script("update.sh").returncode, 0)
        calls = self.log("gh")
        self.assertIn("api -X PATCH repos/owner/repo/git/refs/heads/agd-update -f sha=c0ffee -F force=true", calls)
        self.assertTrue(any(c.startswith("pr edit 12 ") for c in calls), calls)
        self.assertFalse((self.fake / "pr-created").exists())
        self.assertEqual(self.outputs(), {"pr-number": "12", "pr-url": "https://github.com/owner/repo/pull/12"})

    def test_the_branch_input_names_the_branch(self):
        self.env["BRANCH"] = "setup-update"
        self.assertEqual(self.script("update.sh").returncode, 0)
        calls = self.log("gh")
        self.assertIn("api -X POST repos/owner/repo/git/refs -f ref=refs/heads/setup-update -f sha=c0ffee", calls)
        self.assertTrue(any(c.startswith("pr create --head setup-update ") for c in calls), calls)

    def test_the_pull_request_names_executable_files(self):
        self.assertEqual(self.script("update.sh").returncode, 0)
        body = (self.temp / "body.md").read_text()
        self.assertIn("may lose their executable bit", body)
        self.assertIn("- `run.sh`", body)
        self.assertNotIn("`kept.md`", body)

    def test_only_edited_files_due_makes_no_commit(self):
        (self.fake / "only-edited").touch()
        result = self.script("update.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log("gh"), [])
        self.assertEqual(self.outputs(), {})

    def test_no_commit_from_the_api_fails_and_deletes_the_temporary_branch(self):
        (self.fake / "oid").write_text("")
        result = self.script("update.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::GitHub's API made no commit.", result.stdout)
        self.assertEqual(self.log("gh")[-1], "api -X DELETE repos/owner/repo/git/refs/heads/agd-update-tmp-7-1")


class ReleaseTest(unittest.TestCase):
    """scripts/release.sh, which the Release workflow runs."""

    CHANGELOG = "# Changelog\n\nWhat changed.\n\n## 1.2.0 (unreleased)\n\n- Newer agd.\n- Faster.\n\n## 1.1.0 (2026-01-01)\n\n- Old.\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "scripts").mkdir()
        shutil.copy(REPO / "scripts/release.sh", self.root / "scripts")
        self.changelog = self.root / "CHANGELOG.md"
        self.changelog.write_text(self.CHANGELOG)

    def tearDown(self):
        self._tmp.cleanup()

    def release(self):
        return run([BASH, "scripts/release.sh", "--date", "2026-02-02", "--notes", "notes.md"], self.root)

    def test_dates_the_entry_and_writes_its_notes(self):
        result = self.release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1.2.0")
        self.assertIn("\n## 1.2.0 (2026-02-02)\n", self.changelog.read_text())
        self.assertNotIn("unreleased", self.changelog.read_text())
        self.assertEqual((self.root / "notes.md").read_text(), "- Newer agd.\n- Faster.\n")

    def test_refuses_a_changelog_it_cant_release(self):
        cases = {
            "no unreleased entry": self.CHANGELOG.replace("1.2.0 (unreleased)", "1.2.0 (2026-01-02)"),
            "two unreleased entries": self.CHANGELOG.replace("1.1.0 (2026-01-01)", "1.1.0 (unreleased)"),
            "a TODO": self.CHANGELOG.replace("- Faster.", "- TODO: describe the change"),
            "no bullet": self.CHANGELOG.replace("- Newer agd.\n- Faster.\n", "Text.\n"),
            "no version": self.CHANGELOG.replace("1.2.0 (unreleased)", "next (unreleased)"),
        }
        for case, changelog in cases.items():
            self.changelog.write_text(changelog)
            result = self.release()
            self.assertNotEqual(result.returncode, 0, case)
            self.assertIn("CHANGELOG.md", result.stderr, case)
            self.assertEqual(self.changelog.read_text(), changelog, case)
            self.assertFalse((self.root / "notes.md").exists(), case)


if __name__ == "__main__":
    unittest.main()
