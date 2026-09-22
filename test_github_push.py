import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "github_push.sh"


class GithubPushFlowTests(unittest.TestCase):
    def make_repo(self):
        temp_dir = tempfile.TemporaryDirectory()
        repo = Path(temp_dir.name)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/example/example.git"],
            check=True,
        )
        (repo / "tracked.txt").write_text("workspace\n")
        return temp_dir, repo

    def fake_git_path(self, temp_dir):
        bin_dir = Path(temp_dir.name) / "bin"
        bin_dir.mkdir()
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        wrapper = bin_dir / "git"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ -n \"${FAKE_GIT_LOG:-}\" ]]; then printf '%s\\n' \"$*\" >> \"$FAKE_GIT_LOG\"; fi\n"
            "for arg in \"$@\"; do\n"
            "  if [[ \"$arg\" == \"fetch\" || \"$arg\" == \"push\" || \"$arg\" == \"ls-remote\" ]]; then exit 0; fi\n"
            "done\n"
            f'exec "{real_git}" "$@"\n'
        )
        wrapper.chmod(0o755)
        gh = bin_dir / "gh"
        gh.write_text("#!/usr/bin/env bash\nexit 1\n")
        gh.chmod(0o755)
        return bin_dir

    def base_env(self, repo, bin_dir, token=True):
        env = os.environ.copy()
        env.update(
            {
                "WORKSPACE_DIR": str(repo),
                "PATH": f"{bin_dir}:{env['PATH']}",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
            }
        )
        for key in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
            "GITHUB_TOKEN",
            "GH_TOKEN",
        ):
            env.pop(key, None)
        if token:
            env["GITHUB_PERSONAL_ACCESS_TOKEN"] = "dummy-token-not-printed"
        else:
            env.pop("GITHUB_PERSONAL_ACCESS_TOKEN", None)
        return env

    def test_sync_creates_commit_without_global_identity(self):
        temp_dir, repo = self.make_repo()
        try:
            bin_dir = self.fake_git_path(temp_dir)
            result = subprocess.run(
                ["bash", str(SCRIPT), "--sync", "--yes"],
                cwd=ROOT,
                env=self.base_env(repo, bin_dir),
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Workspace sync completed", result.stdout)
            self.assertNotIn("dummy-token-not-printed", result.stdout + result.stderr)

            commit_line = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(repo),
                    "log",
                    "-1",
                    "--format=%an <%ae> %s",
                ],
                env=self.base_env(repo, bin_dir),
                text=True,
            ).strip()
            self.assertEqual(
                commit_line,
                "example <example@users.noreply.github.com> "
                "chore: sync workspace to GitHub",
            )
        finally:
            temp_dir.cleanup()

    def test_https_sync_fails_before_commit_without_token(self):
        temp_dir, repo = self.make_repo()
        try:
            bin_dir = self.fake_git_path(temp_dir)
            result = subprocess.run(
                ["bash", str(SCRIPT), "--sync", "--yes"],
                cwd=ROOT,
                env=self.base_env(repo, bin_dir, token=False),
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No GitHub token available", result.stderr)
            self.assertNotIn("dummy-token-not-printed", result.stdout + result.stderr)
        finally:
            temp_dir.cleanup()

    def test_check_auth_is_read_only_and_uses_noninteractive_header(self):
        temp_dir, repo = self.make_repo()
        try:
            bin_dir = self.fake_git_path(temp_dir)
            auth_log = Path(temp_dir.name) / "git-args.log"
            env = self.base_env(repo, bin_dir)
            env["FAKE_GIT_LOG"] = str(auth_log)
            result = subprocess.run(
                ["bash", str(SCRIPT), "--check-auth"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("authentication succeeded", result.stdout)
            self.assertNotIn("dummy-token-not-printed", result.stdout + result.stderr)
            args = auth_log.read_text()
            self.assertIn("http.extraHeader=Authorization: Basic", args)
            self.assertIn("core.askPass=", args)
            self.assertNotIn("dummy-token-not-printed", args)
            no_commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                env=self.base_env(repo, bin_dir),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(no_commit.returncode, 0)
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()