# Setup — wiring up the live terminal stat card

This all lives in your **profile repo** — the special one GitHub renders on your
profile page, named exactly `aenoshrajora/aenoshrajora`. If that repo doesn't
exist yet, create a new public repo with that exact name first; everything
in this folder is its contents.

## 1. Create a fine-grained Personal Access Token

GitHub Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.

- **Resource owner:** your account
- **Repository access:** All repositories (so it can read commit history across your repos)
- **Permissions:**
  - Account permissions → none needed beyond default read
  - Repository permissions → `Contents: Read-only`, `Metadata: Read-only`, `Commit statuses: Read-only`

Copy the token — you won't see it again.

## 2. Add it as a repo secret

In `aenoshrajora/aenoshrajora` → Settings → Secrets and variables → Actions → New repository secret:

- Name: `ACCESS_TOKEN`
- Value: the token from step 1

`GITHUB_TOKEN` (used by the auto-commit step) is provided automatically — nothing to do there.

## 3. Enable Actions

Settings → Actions → General → Allow all actions, and make sure **workflow
permissions** are set to "Read and write permissions" so `profile-stats.yml`
can push the refreshed SVGs back to the repo.

## 4. First run

Actions tab → "Update terminal stat card" → Run workflow (manual trigger). It
should finish in well under a minute for a repo your size and commit updated
`assets/terminal_dark.svg` / `assets/terminal_light.svg`. After that it
refreshes itself every 6 hours automatically.

## 5. Latest / Most Starred / Recently Exploring sections

These three sections in `README.md` are markdown tables spliced in between
HTML comment markers (`<!-- LATEST-PROJECTS:START -->` etc.) by the same
`generate_stats.py` run that updates the terminal card — no separate setup
needed beyond steps 1–4. They pull:

- **Latest Public Work** — your 4 most recently pushed-to public, non-fork repos
- **Most Starred** — your own public repos ranked by star count
- **Recently Exploring** — the 6 repos you've most recently starred (anyone's)

If you ever rename or delete those HTML comment markers in `README.md`, the
script just leaves that section alone on the next run instead of erroring —
so it's safe to reorder sections, just keep the START/END marker pairs intact
around whichever table you want to stay live.

## 6. Blog posts

`blog-post-workflow.yml` now points at `https://aenoshrajora.medium.com/feed`
and drops your 5 most recent posts between the
`<!-- BLOG-POST-LIST:START -->` / `<!-- BLOG-POST-LIST:END -->` markers in
`README.md` — no token needed for that one, it runs on the default
`GITHUB_TOKEN`.

## Notes

- `scripts/generate_stats.py` walks your account year-by-year to total commits
  (the GraphQL `contributionsCollection` API caps each query at a 1-year
  window), and caches per-repo lines-of-code counts in `cache/` so re-runs
  only re-scan repos with new commits.
- Want a different refresh cadence? Edit the `cron` line in
  `.github/workflows/profile-stats.yml` (currently `"0 */6 * * *"`, every 6h).
- Want different numbers on the card? All the fields the script fills in are
  the `id="stat_*"` elements in `assets/terminal_dark.svg` /
  `terminal_light.svg` — add a new `<text id="stat_whatever">` in the SVG and
  a matching key in the `stats` dict in `generate_stats.py`.
