# Sermon Search

A one-page search for Alabaster NYC sermons. Type in the box and the list filters on every keystroke by title, Bible reference, Podbean description or date.

- `index.html`: the whole page. It loads `sermons.json` and does all filtering in the browser.
- `sermons.json`: generated. One record per sermon, newest first.
- `scripts/build_sermons.py`: builds `sermons.json` from Coda (the Sermon Recordings table in "Standard Programs") and the Podbean RSS feed. Standard library only.
- `data/podbean-cache.json`: every Podbean episode the sync has seen. The feed only lists the latest 50, so this keeps older descriptions searchable.
- `.github/workflows/sync.yml`: runs the sync every Sunday night (Mondays 03:00 UTC), on the "Run workflow" button, and on every push to `main`, then deploys to GitHub Pages.

## Setup

1. In the repo, go to **Settings → Secrets and variables → Actions** and add a secret named `CODA_API_TOKEN` (create a token at coda.io/account → API settings; read access to the Standard Programs doc is enough).
2. Go to **Settings → Pages** and set **Source** to **GitHub Actions**.
3. Open **Actions → Sync sermons and deploy → Run workflow**.

## How the sync works

1. Reads every row of Sermon Recordings by column ID (renaming a column in Coda won't break it).
2. Matches each row to a Podbean episode by its link, or by date when there is exactly one episode and one row that day.
3. Cleans references: `acts 5` → `Acts 5`, `Matthew 13, 16` → `Matthew 13`, `Matthew 16`, `Proverbs 17:2` → `Proverbs 17`.
4. Leaves out rows whose only link is SharePoint (plenary sessions aren't public) and rows with no title.
5. Writes `sermons.json` with a lowercase `searchText` (title, references, event, description and the date spelled several ways).

If Coda returns fewer than 200 rows or any request fails, the run stops and the last good version stays live.

## Running locally

```bash
CODA_API_TOKEN=... python3 scripts/build_sermons.py
python3 -m http.server 8000
```

Then open http://localhost:8000.

## Search behavior

- Case-insensitive substring match: `Isai` finds every Isaiah sermon.
- Every word must match somewhere: `isaiah 2025` = Isaiah sermons from 2025.
- `Isaiah 5` also matches Isaiah 52 and 53 (plain substring matching, by design).
- Dates match as `2024-03-10`, `3/10/2024`, `March 10, 2024`, `Mar 2024`, `March 2024` or `2024`.
- Clicking a reference chip searches for it. The query is kept in the URL (`?q=john+15`), so searches can be shared.
