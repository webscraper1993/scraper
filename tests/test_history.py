import csv
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scraper_AAshopping"
loader = importlib.machinery.SourceFileLoader("history_scraper", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
scraper = importlib.util.module_from_spec(spec)
loader.exec_module(scraper)


class HistoryTests(unittest.TestCase):
	def test_new_and_updated_highs_are_newest_first(self):
		with tempfile.TemporaryDirectory() as directory:
			with mock.patch.object(scraper, "HISTORY_DIR", directory):
				scraper.record_daily_high("Adidas", 2, "2026-11-02")
				scraper.record_daily_high("Adidas", 3, "2026-11-04")
				scraper.record_daily_high("Adidas", 4, "2026-11-01")
				scraper.record_daily_high("Adidas", 5, "2026-11-02")
				scraper.record_daily_high("Adidas", 1, "2026-11-02")

			with open(Path(directory) / "adidas.csv", encoding="utf-8", newline="") as history:
				self.assertEqual(list(csv.reader(history)), [
					["date", "miles"],
					["2026-11-04", "3"],
					["2026-11-02", "5"],
					["2026-11-01", "4"],
				])


if __name__ == "__main__":
	unittest.main()
