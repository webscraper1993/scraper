# Merchant Rate History

Each merchant has a CSV file (for example, `adidas.csv`) that GitHub displays
as a table. You can also open or import these files in Excel.

| Column | Meaning |
| --- | --- |
| `date` | Observation date in UTC, formatted as YYYY-MM-DD |
| `miles` | Highest numeric miles offer observed by the scraper on that date |

Every run checks the current `TARGET_MERCHANTS` list in `scraper_AAshopping`.
History is recorded independently of each merchant's email threshold. Only
values of at least 0.5 are included; missing or unparseable offers are skipped.
A missing date means no eligible observation was recorded, not a zero rate.
These are observed highs, not guaranteed highs between scraper runs. The
numeric value preserves the feed's offer units (per-dollar or flat bonuses).

Adding a merchant starts its history with its first eligible observation.
Removing a merchant stops updates without deleting its previous history.
Changing an email threshold does not reset history. Filenames use canonical
merchant names, removing punctuation, spaces, and `.com`.

The scheduled GitHub Actions workflow commits and pushes changed CSVs after
each run, preserving intraday highs and adding a new row each day with an
eligible observation. Unchanged history does not produce a commit. Manual
workflow runs also publish updates; local script runs only update local files.
History starts when this version runs; past dates are not backfilled.

Email alerts retain the existing rules: a rate at or above its configured
threshold triggers an alert when it first qualifies or returns from below the
threshold, not on every qualifying run. The 0.5 history cutoff does not change
email thresholds, including Amazon's configured 0.1 threshold.