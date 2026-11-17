import contextlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scraper_AAshopping"


def git(directory, *args):
	return subprocess.run(
		["git", "-C", str(directory), *args],
		check=True, capture_output=True, text=True,
	)


class LocalUpdateTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		root = Path(self.temp.name)
		self.remote = root / "remote.git"
		self.local = root / "local"
		self.other = root / "other"
		subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(self.remote)],
			check=True, capture_output=True, text=True)
		subprocess.run(["git", "clone", str(self.remote), str(self.local)],
			check=True, capture_output=True, text=True)
		self.configure(self.local)
		(self.local / "scraper_AAshopping").write_bytes(SCRIPT.read_bytes())
		(self.local / "history.csv").write_text("initial\n", encoding="utf-8")
		git(self.local, "add", ".")
		git(self.local, "commit", "-m", "initial")
		git(self.local, "push", "origin", "main")
		subprocess.run(["git", "clone", str(self.remote), str(self.other)],
			check=True, capture_output=True, text=True)
		self.configure(self.other)

		loader = importlib.machinery.SourceFileLoader("test_scraper", str(self.local / "scraper_AAshopping"))
		spec = importlib.util.spec_from_loader(loader.name, loader)
		self.scraper = importlib.util.module_from_spec(spec)
		loader.exec_module(self.scraper)
		self.environment = mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "", "_AA_SHOPPING_UPDATED": ""})
		self.environment.start()
		self.addCleanup(self.environment.stop)

	def configure(self, directory):
		git(directory, "config", "user.name", "Test")
		git(directory, "config", "user.email", "test@example.invalid")

	def push_change(self, filename, content):
		(self.other / filename).write_text(content, encoding="utf-8")
		git(self.other, "add", filename)
		git(self.other, "commit", "-m", "remote update")
		git(self.other, "push", "origin", "main")

	def test_fast_forward_restarts_updated_script_and_preserves_unrelated_changes(self):
		original = (self.other / "scraper_AAshopping").read_text(encoding="utf-8")
		self.push_change("scraper_AAshopping", original + "\n# remote update\n")
		(self.local / "history.csv").write_text("local change\n", encoding="utf-8")
		real_run = subprocess.run
		restarts = []

		def run(command, **kwargs):
			if command[0] == "git":
				return real_run(command, **kwargs)
			restarts.append((command, kwargs))
			return subprocess.CompletedProcess(command, 0)

		with mock.patch.object(self.scraper.subprocess, "run", side_effect=run):
			with self.assertRaises(SystemExit) as exit_error:
				self.scraper.update_local_checkout()
		self.assertEqual(exit_error.exception.code, 0)
		self.assertEqual((self.local / "history.csv").read_text(encoding="utf-8"), "local change\n")
		self.assertTrue((self.local / "scraper_AAshopping").read_text(encoding="utf-8").endswith("# remote update\n"))
		self.assertEqual(len(restarts), 1)
		self.assertEqual(restarts[0][1]["env"]["_AA_SHOPPING_UPDATED"], "1")

	def test_conflicting_local_change_stops_with_git_error(self):
		self.push_change("history.csv", "remote change\n")
		(self.local / "history.csv").write_text("local change\n", encoding="utf-8")
		with self.assertRaises(SystemExit) as exit_error:
			self.scraper.update_local_checkout()
		self.assertIn("local changes", str(exit_error.exception).lower())
		self.assertIn("Fix the Git error manually", str(exit_error.exception))
		self.assertEqual((self.local / "history.csv").read_text(encoding="utf-8"), "local change\n")

	def test_restart_executes_pulled_script_and_propagates_exit_code(self):
		marker = self.local / "restarted.txt"
		self.push_change(
			"scraper_AAshopping",
			f"from pathlib import Path\nPath({str(marker)!r}).write_text('updated')\nraise SystemExit(7)\n",
		)
		with self.assertRaises(SystemExit) as exit_error:
			self.scraper.update_local_checkout()
		self.assertEqual(exit_error.exception.code, 7)
		self.assertEqual(marker.read_text(encoding="utf-8"), "updated")

	def test_diverged_commits_stop_without_merging(self):
		self.push_change("history.csv", "remote change\n")
		(self.local / "local.txt").write_text("local commit\n", encoding="utf-8")
		git(self.local, "add", "local.txt")
		git(self.local, "commit", "-m", "local update")
		before = git(self.local, "rev-parse", "HEAD").stdout
		with self.assertRaises(SystemExit) as exit_error:
			self.scraper.update_local_checkout()
		self.assertIn("fast-forward", str(exit_error.exception).lower())
		self.assertEqual(git(self.local, "rev-parse", "HEAD").stdout, before)

	def test_other_branch_stops_without_pulling(self):
		git(self.local, "switch", "-c", "test-branch")
		with self.assertRaises(SystemExit) as exit_error:
			self.scraper.update_local_checkout()
		self.assertIn("not main", str(exit_error.exception))

	def test_actions_and_restarted_process_skip_pull(self):
		for variable, value in (("GITHUB_ACTIONS", "true"), ("_AA_SHOPPING_UPDATED", "1")):
			with self.subTest(variable=variable), mock.patch.dict(os.environ, {variable: value}):
				with mock.patch.object(self.scraper.subprocess, "run") as run:
					self.scraper.update_local_checkout()
					run.assert_not_called()

	def test_current_checkout_continues_without_restart(self):
		with contextlib.redirect_stdout(io.StringIO()):
			self.scraper.update_local_checkout()


if __name__ == "__main__":
	unittest.main()
