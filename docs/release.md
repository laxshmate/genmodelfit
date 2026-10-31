# Releases and data distribution

**English** · [日本語](release.ja.md)

How genmodelfit is released and how its model list is distributed: package releases, the signed data release, the weekly list update and signing keys.
This page is for the maintainer. App developers who only consume the list can skip to [Data releases](#data-releases).

## Contents

- [GitHub environments (set up 2026-10-20)](#github-environments-set-up-2026-10-20)
- [Releasing a version](#releasing-a-version)
- [Receiving measurement issues (#113)](#receiving-measurement-issues-113)
- [Data releases](#data-releases)
- [Weekly list update (#19)](#weekly-list-update-19)
  - [What to check in the PR](#what-to-check-in-the-pr)
  - [GPU table watch (#103)](#gpu-table-watch-103)
  - [Weekly skill (genmodelfit-weekly, #42)](#weekly-skill-genmodelfit-weekly-42)
  - [Trying the same flow locally](#trying-the-same-flow-locally)
  - [One-time setup by the maintainer](#one-time-setup-by-the-maintainer)
- [Keys](#keys)
  - [Rotating keys (regularly, or as a precaution)](#rotating-keys-regularly-or-as-a-precaution)
  - [If a key leaks](#if-a-key-leaks)
- [What we do not use](#what-we-do-not-use)

There are three flows.

| Flow | What it ships | Trigger | Where it goes |
|---|---|---|---|
| Version release | The package (sdist and wheel) | Push a `v*` tag (only admins can create the tag) | PyPI and a GitHub Release |
| Data release | The model list `db.json` and its signature `db.json.sig` | A push that changes `db.json` on main, or a manual run | GitHub Release `data-latest` |
| Weekly list update | A PR with a rebuilt `db.json` (nothing is shipped) | A script on the maintainer's PC, once a week. weekly-data.yml is a manual backup | A PR (merged automatically only in a safe week; see [Weekly skill](#weekly-skill-genmodelfit-weekly-42) below) |

- Data releases are not `v*` tags, so they do not go to PyPI.
- Branch protection on main requires PRs and forbids force pushes and deletion.

## GitHub environments (set up 2026-10-20)

| Environment | Usable from | Approval | Secrets |
|---|---|---|---|
| `pypi` | `v*` tags only | None (the maintainer removed it on 2026-10-21; instead a ruleset limits `v*` tags to admins) | None (Trusted Publishing) |
| `data-signing` | The `main` branch only | None | `GENMODELFIT_SIGNING_KEY` (the private signing key) |

- Repository-wide secrets hold nothing.
- The private key exists only in `data-signing`, so workflows on branches other than main and on PRs cannot read it.
- data.yml also passes it, as an environment variable, to the one signing step only.

To check the settings, run:

- `gh api repos/laxshmate/genmodelfit/environments`
- `gh secret list -R laxshmate/genmodelfit` (must be empty)
- `gh secret list --env data-signing -R laxshmate/genmodelfit`

## Releasing a version

**Publish only after the maintainer approves.**

1. Put other users' measurements (data/contrib) into the bundled aggregate (#90).
   - A submission PR changes only data/contrib, so merging it does not reach users. Only a package release does
     (the weekly data release ships db.json only).
   - `genmodelfit contrib aggregate --check` counts the submissions not in it yet (exit code 1 if any). Until the first
     submission is merged there is no data/contrib, so `--check` exits 1 (no such folder); that is expected.
   - If there are any, rebuild it with `genmodelfit contrib aggregate --output src/genmodelfit/data/measurements_community.json`
     (the sha256 of each submission used goes into `submissions`; the test re-aggregates exactly those and compares).
   - Make the aggregate in a clone with git history (not shallow): sources (submissions sent by the same person count as
     one; #113) are counted from the git history (#90; in a shallow clone or outside git each submission counts on its own,
     and the output says so). Since 0.11.0 `--output` refuses (exit code 1) when any source would be counted per file
     (`file:…`), because one person's many files would then count as many sources and could set a level (#95).
     `--allow-file-sources` overrides this for tests and local experiments only; never use it for a release.
     `--check` shows `file_sources` (the bundled file must have 0; a test checks it).
   - If someone resubmitted a fixed submission, remove the old file in a maintainer PR (write the reason in the PR
     description; a submission PR only adds files and cannot remove them). The "add only" check of the `Contrib`
     workflow fails on this PR, which is expected (it is a check for submission PRs; the maintainer reviews and merges).
   - If `--check` lists rows that are not used (their `files_fp` differs from the current list), upstream files have
     changed. Those rows are not used for judging, so they can stay; wait for submissions measured with the new files.
   - If the consistent groups (`consistent`) changed, refit with `python -m genmodelfit.calibrate` and update
     docs/accuracy.ja.md and docs/accuracy.md.
2. Raise `__version__` in `src/genmodelfit/_version.py` (pyproject.toml reads it through hatch), and write the changes
   in the `## [<version>] - Unreleased` section of `CHANGELOG.md`.
3. **Before tagging, replace `Unreleased` in that section with the release date** (`## [0.12.0] - 2026-11-02`), and
   update the link definitions at the end of `CHANGELOG.md`. The section becomes the body of the GitHub Release, and
   release.yml stops before publishing if the section of the tag's version is missing, has no date or is empty
   (`python tools/changelog_notes.py <version>` shows the body, or the reason, locally).
4. Merge it to main by PR, with a normal merge commit (never squash or rebase).
   ci.yml does not run on the PR, so put the result of `tools/ci_local.py --linux` in the PR description.
5. After the merge, check that ci.yml, which runs on main after the merge, is green.
   If the merge changed `src/genmodelfit/data/db.json`, data.yml ships the list too ([Data releases](#data-releases)).
6. **Push the tag** on the head of main. It must match `_version.py`:

   ```powershell
   git switch main; git pull
   git tag v<version>
   git push origin v<version>
   ```
7. `.github/workflows/release.yml` runs these jobs in order:
   - test: ruff and pytest (3.12)
   - build: `uv build` → check that the tag, `_version.py` and the version in the wheel / sdist names agree → take the
     tag's section out of `CHANGELOG.md` (`tools/changelog_notes.py`; fails without a date) → `twine check --strict`
   - publish: publish to PyPI from environment `pypi` (no approver; only admins can create `v*` tags, by ruleset).
     Uses Trusted Publishing, so no API token.
   - github-release: create GitHub Release `v<version>` with the same dist files attached and the `CHANGELOG.md`
     section as its body
8. **Verify** in a clean venv, with a separate cache location:

   ```powershell
   uv venv -p 3.12 $env:TEMP\gmf-check
   uv pip install --python $env:TEMP\gmf-check\Scripts\python.exe genmodelfit==<version>
   $env:GENMODELFIT_CACHE_DIR = "$env:TEMP\gmf-check-cache"
   & $env:TEMP\gmf-check\Scripts\genmodelfit --version          # genmodelfit <version>
   & $env:TEMP\gmf-check\Scripts\genmodelfit system             # Data section: List: bundled …
   & $env:TEMP\gmf-check\Scripts\genmodelfit update --from-release   # verifies the signature and imports
   & $env:TEMP\gmf-check\Scripts\genmodelfit system             # List: release, HEAD checks expiry
   & $env:TEMP\gmf-check\Scripts\genmodelfit recommend --vram 8 --ram 32 -n 2
   ```
9. Tell AI生成キット (laxshmate/local_model_ai_gen_kit) that the release is out.

The first release (v0.1.0, 2026-10-20) followed the same steps, after merging the stacked PRs of the first version in
order (each retargeted to main before its merge).

About manual runs and PyPI:

- A manual run (workflow_dispatch) stops after the build and publishes nothing.
  Even when you pick a tag, publish and github-release are skipped by the condition `github.event_name == 'push'`.
  It is a dry run before tagging (the `CHANGELOG.md` date check runs only when the run is on a tag).
- On PyPI, account `laxshmate` (two-factor on) has a Trusted Publisher for
  `genmodelfit` / `laxshmate/genmodelfit` / `release.yml` / environment `pypi`.
- A published version cannot be deleted (only yanked). Version numbers cannot be reused, so to fix a release, ship the next version.

## Receiving measurement issues (#113)

`genmodelfit share --issue` (and choice 2 "issues" in `genmodelfit share`) opens the issue form
`.github/ISSUE_TEMPLATE/measurement.yml` with a measurement filled in. The
workflow `.github/workflows/measurement-intake.yml` handles open issues with the `measurement` label when they are
opened and when their body is edited (title-only edits and adding a label do nothing, so one issue is not processed
twice). It has two jobs:

1. `check` (read-only token): installs genmodelfit, and `genmodelfit contrib intake` reads the issue body from the
   event file as data (never through `${{ }}` in a script), runs the same checks as `contrib validate` plus the
   privacy checks of `share` (without the runner's own host / user name check), refuses a submission whose content is
   already in `data/contrib`, and counts how many submissions the same account already sent for that GPU and model
   (full git history). The result files go to an artifact ([schema.md, "contrib"](schema.md#contrib)).
2. `publish` (contents, pull-requests and issues write; installs nothing, only `git` and `gh`):
   - Accepted and new: pushes `data/contrib/<GPU>/<date>-<sha8>.json` to the branch `measurement/issue-<N>` (commit
     with `Submitted-by: github:<account>` and `Submitted-by-id: <the account's numeric ID>`, which does not change
     when the account is renamed), opens a PR (body: the issue author, the earlier submissions of that
     account, notices for the maintainer, `Closes #N`) and comments the link on the issue.
   - Same content as this issue's open PR: does nothing (no push, no comment).
   - Same content as another issue's open PR: comments the link to that PR and opens nothing.
   - Edited to different valid content after the PR exists: updates the PR (title, body, file) and comments on both the
     PR and the issue. A PR closed earlier because of invalid content is reopened first.
   - Refused: comments the reasons (English and Japanese) on the issue, once per set of reasons (a hidden marker in
     the comment; the same reasons again are not repeated). If the issue already has an open PR, the PR gets the
     reasons, the label `measurement-invalid` (created when missing) and is **closed**. When the issue is fixed,
     only a PR with that label is reopened (and the label removed); a PR a maintainer closed is never reopened
     (the bot says so on the issue).
   - Edited to content that another issue's PR already has: links that PR and closes this issue's PR the same way.
   - Only the bot's own PRs are looked at (`isCrossRepository` false and the author GitHub Actions, which `gh`
     reports as `app/github-actions`); a fork's PR from a branch with the same name is ignored.
   Merging the PR closes the issue.

What to check before merging: the notices in the PR body (the same as the `Contrib` job summary; contrib.yml does not
run for a PR made with `GITHUB_TOKEN`), and the line about how many submissions the same account already sent for
that GPU and model (one account counts as one source however many it sends, but another account, an organization the
person owns, or a pull request after a rename counts as someone else; check the author of the pull request or
issue). Merge with **"Create a merge commit" only, never squash or rebase**, for the bot's pull requests and for
measurement pull requests opened by people alike (the same rule as "Releasing a version"): sources are
counted from the merge commit (`Merge pull request #N from laxshmate/measurement/issue-M` is what makes the
`Submitted-by:` line trusted).

One-time setup (the repository's Settings):
1. Settings → Actions → General → "Workflow permissions" → tick "Allow GitHub Actions to create and approve pull
   requests" → Save.
2. Issues → Labels → New label → name `measurement` → Create label.
3. Settings → General → "Pull Requests": keep "Allow merge commits" ticked and untick "Allow squash merging" and
   "Allow rebase merging" (sources are counted from GitHub's merge commits). The maintainer's weekly script (outside this
   repository) merges its PR with `gh pr merge --merge`, which still works; nothing in this repository uses
   `--squash` or `--rebase`.
   On the same page, under "Allow merge commits", set "Default commit message" to "Default message": the first
   line of the merge commit must stay `Merge pull request #N from <owner>/<branch>`, because the source account is
   read from it ("Pull request title" or "title and description" would replace that line).
4. Protect main so that nobody, including administrators, can push to it or merge without a pull request:
   Settings → Rules → Rulesets → New ruleset → New branch ruleset → name `main` → Enforcement status "Active" →
   Bypass list: leave empty → Target branches: "Include default branch" → tick "Require a pull request before
   merging" and "Block force pushes" → Create. (With classic branch protection instead: Settings → Branches → Add
   branch protection rule → `main` → tick "Require a pull request before merging" and "Do not allow bypassing the
   above settings" → Create.)
The workflow uses only `GITHUB_TOKEN`; no secrets.

## Data releases

`.github/workflows/data.yml` signs and ships **the `src/genmodelfit/data/db.json` committed on main, as it is**.
It does not rebuild from upstream; the rebuild-and-PR flow is in [Weekly list update](#weekly-list-update-19) below.
The triggers are a push that changes db.json on main, and a manual run.

1. sign:
   - Runs in environment `data-signing` (main only).
   - Checks that the committed list can be read, signs it, and verifies the signature with the bundled public key.
   - Uses `contents: read`. The private key is available only to this job's "sign" step.
   - A manual run on a branch other than main does not run, because of `if: github.ref == 'refs/heads/main'`.
2. upload: replace `db.json` and `db.json.sig` in Release `data-latest` (prerelease). This is the only job with `contents: write`.
3. verify:
   - Right after the upload, run `update --from-release` in clean venvs: one with the code from main, and one with `genmodelfit==0.5.0` (the oldest supported version, which does not know `review`).
   - Check that the source is release, the list has not expired, it has more than 100 entries, and there is at least 1 auto-recommendation.

checkout uses `persist-credentials: false`.

HEAD checks in the bundled list expire after 30 days. When they expire, auto-recommendations drop to 0.
Normally, merging the weekly PR (below) extends them. If the weekly PR is stalled, do this before the expiry:

1. Fetch again with `genmodelfit update --heads-only --no-cache --output src/genmodelfit/data/db.json`.
2. Make a PR and merge it to main. data.yml then ships the list again.

To see the current expiry, read the Data section of `genmodelfit system --no-cache` (`valid until`).

For consumers:

```powershell
genmodelfit update --from-release                       # import into the cache
genmodelfit update --from-release --output db.json      # write the verified list to the given path (the cache is not touched)
genmodelfit update --from-release --force               # import even if older than the previously imported release (see below)
```

```python
from genmodelfit.remote import update_from_release

db = update_from_release()  # on failure, RemoteError (a kind of DatabaseError)
```

Import steps:

1. Fetch `db.json` and `db.json.sig`. Each is limited to 64 MB; the size is counted while reading and reading stops when it is exceeded.
2. Check the signature against **any one** of the bundled trusted public keys (`src/genmodelfit/data/signing_keys/*.pub`).
   - If it does not match, wait 5 seconds and fetch again **once**. GitHub Release assets are replaced one at a time, so a fetch in the middle of data.yml's upload can pair a new `db.json` with an old `db.json.sig`.
   - If it still does not match, fail.
3. Check `schema_version` and the shape. `generated_at` is required, as an ISO 8601 time with a timezone (`Z` is fine).
4. Check that it is not older than the previously imported release (rollback protection).
   - Compare against `release_state.json` in the cache (the `generated_at` of the previously imported release).
   - Do not compare against the cache's `db.json`, because a local `genmodelfit update` writes it too.
   - Times are converted to UTC before comparing (`Z` and `+00:00` are the same time). Fetching the same time again is allowed.
5. Advance `release_state.json`, then replace the output (by default the cache's `db.json`) atomically.

If any step fails:

- The output (or the bundled data if there is none) is left as it was.
- A message is printed and the exit code is 1 (with `--json`, the form `{"error": ...}`).
- The same happens when another program has the cache open and it cannot be replaced (Windows).

If the time in `release_state.json` ended up in the future (for example, a rebuilt list's time went backward):

- A normal import stops with "the list is older".
- Once you have confirmed the release is genuine, add `--force`. It imports and resets the record to that time.
- If `release_state.json` is broken, it is treated as absent and rewritten at the next import.

The signature is Ed25519. `db.json.sig` is one line: the 64-byte signature in base64.
To check a signature by hand, run `python -m genmodelfit.signing verify db.json` (it checks against any of the bundled trusted keys).

## Weekly list update (#19)

Following llmfit's weekly-model-update.yml, a list rebuilt from upstream becomes a PR every week.
**Only a list that a person has reviewed and merged is published** (rebuilding and publishing are separate).

```text
weekly-data.yml (manual backup; since #42 the scheduled run is a startup script on the maintainer's PC) → PR automated/weekly-data → the maintainer reviews and merges
  → data.yml signs main's db.json and puts it in data-latest → consumers run genmodelfit update --from-release
```

`.github/workflows/weekly-data.yml` has two jobs.

1. build (`contents: read` only):
   - Restore the cache (`actions/cache/restore`; `file_headers.json` and `repo_precision.json`).
     The key differs every run (run_id); restore-keys picks the newest.
   - Run `python -m genmodelfit.weekly --out weekly-out --apply src/genmodelfit/data/db.json --rate-limit-budget 900` (time limit 90 minutes).
     - It does the same rebuild as `genmodelfit update` (templates, HF, HEAD, file headers and precision).
     - Then it re-reads the result like `update --inspect-only` (the same 2 passes as the bundled list).
     - Carry-over comes from main's list.
     - HF 429 responses are waited out following Retry-After. It stops when the total wait exceeds 900 seconds (HF_TOKEN is not used).
   - Checks. If any fails, the exit code is 1 and no PR is made. The reason is in `problems` of `weekly-out/result.json` and in the run summary.
     - Shape (after re-reading).
     - The number of auto-recommendations is not 0.
     - **No category dropped below half of main's.** Categories: all, HF, official templates, uncensored, auto-recommendations. Only categories that had 4 or more entries in main.
     - **The HEAD expiry (`expires_at`) is more than 7 days away.** Right after a rebuild, a closer date means the HEAD refetch did not work. This is double-checked from the `genmodelfit system --json` side as well.
     - **At most 5 entries newly marked uncensored.** More suggests the selection rules loosened. Only when `allow_many_uncensored` is chosen in a manual run, this is not a failure and a warning goes at the top of the body.
   - Save the cache (`if: always()`, so it survives failures and timeouts). GitHub deletes caches unused for 7 days.
   - With the new list in place, run `ruff check`, `ruff format --check` and `pytest -q` (a stand-in for CI; see the notes below).
   - Append the result of `genmodelfit contrib aggregate --report` to the PR body.
     - Only for this manual backup. The startup script on the maintainer's PC does not run it.
     - If data/contrib does not exist yet, it says "0 submissions".
     - The report includes a table of levels that change because of other users' measurements (including a single submission). Since 2026-10-22 it is used in both directions, so both raises and drops appear.
   - Keep `weekly-out/` (db.json, diff.json, pr_body.md, result.json, gpu_watch.json, gate.json, system.json) as an artifact for 30 days.
2. pr (`contents: write` and `pull-requests: write` only in this job; the signing-key environment is not used):
   - Take in only a list whose `result.json` is ok.
   - Create or update the PR with `peter-evans/create-pull-request` (pinned by commit).
   - Branch `automated/weekly-data`, labels `automated` and `data-update`, body `weekly-out/pr_body.md`.

Decisions:

- **Everything from upstream in the PR body is made harmless.**
  - This covers model names, IDs, license names, evidence, URLs, repository names and failure reasons. It is done in `weekly.md()`.
  - HTML characters become entities (so `<!--`, `<img>` and `</details>` have no effect).
  - Markdown symbols get a backslash (links, images, emphasis, table separators).
  - A zero-width space goes after `@` (no mentions).
  - A long body is cut at a line break, and any open `<details>` is closed.
  - The #34 measurement summary (which includes values submitted by users) goes inside the `weekly.code_block()` fence.
  - The same body goes to the run summary (`$GITHUB_STEP_SUMMARY`).
- **No automatic merge** (auto-merge is not used either). This workflow neither signs nor publishes.
- **CI (ci.yml) does not run automatically on a PR created with GITHUB_TOKEN.** This is GitHub's rule, to prevent loops.
  - Instead, the build job of the same workflow runs ruff and pytest with the new list before making the PR, and the PR body says so.
  - ci.yml also does not run on PRs from branches of the same repository (#68).
  - To check, run `python tools/ci_local.py --linux` locally, or look at ci.yml, which runs on main after the merge.
- **When some data could not be fetched (`sources.partial`)**: the default is failure (no PR).
  - Only when `allow_partial` is chosen in a manual run, a PR is made with a "partially missing" warning at the top of the body.
  - This applies only when everything missing was a temporary failure of HF repositories and could be carried over from the previous list (`weekly.partial_problems`). That requires all of:
    - the 429 wait limit was not reached;
    - the HF search did not fail;
    - there is no unreadable template;
    - `license_policy` is the same as the previous list.
  - Other kinds of gaps do not become a PR, because entries that disappeared or stayed stale would look like "changes".
- **If the tests fail, no PR is made.** When an upstream change breaks the tests, make a separate PR to fix them.
  Example: in 2026-10 an HF repository was renamed and the re-match in the sources table no longer fit.

### What to check in the PR

Read the "Notes" and the tables in the body (same content as `genmodelfit diff`):

- **⚠ Entries whose license moved to the permissive side.** That means the tier went down, or the license entered the allowlist. This includes unknown → something.
  It changes what gets auto-recommended, so verify against the original text at `license_url`.
- **Entries that entered or left auto-recommendations.** The same policy is applied to both lists: the ComfyUI version is the one recorded in the new list, and HEAD age is ignored.
  Check that no removed entry is one that AI生成キット uses.
- **URLs whose HEAD newly fails** (became gated, or vanished). URLs that were already failing are folded.
- **Removed entries** (gone from upstream, or dropped out of the top search results).
- **The evidence for entries newly marked uncensored.** Entries aimed at sexual content involving minors are excluded ([sources.md](sources.md)).
- **If a file of a measured model changed**, make a separate PR to fix it with `python -m genmodelfit.calibrate`.
- **Entries awaiting review (review)** and the URLs of the `evidence` attached by `review approve` (#42; see [Weekly skill](#weekly-skill-genmodelfit-weekly-42) below).
- The full differences are in `diff.json` of the artifact (its shape is in [schema.md, "diff"](schema.md#diff)).
- The candidates of the "GPU table watch" (below). It does not stop the merge, so candidates become separate issues.

### GPU table watch (#103)

After rebuilding the list, the weekly update (`python -m genmodelfit.weekly`) checks the GPU registration tables (`src/genmodelfit/gputables.py`; every row is in [gpu-inventory.md](gpu-inventory.md)) against the official lists (`gpuwatch.py`).

- Sources read (with `fetch`'s HttpFetcher: a User-Agent, never ~/.netrc):
  - pci.ids (raw from pciutils/pciids)
  - ROCm GPU hardware specs, and the Radeon / Ryzen Windows and Linux compatibility tables
  - TheRock `SUPPORTED_GPUS.md` and `RELEASES.md` (GFX target lookup table)
  - The Hardware Prerequisite tables of PyTorch's "Getting Started on Intel GPU" (stable)
  - NVIDIA cuda-gpus and cuda-legacy-gpus
  - The latest PyTorch release (GitHub releases API), its `torch/cuda/__init__.py` (`PYTORCH_RELEASES_CODE_CC`, `DEVICE_REQUIREMENT`; since 2.14 the build_cuda.sh arch list is copied there and PyTorch's own test checks they match) and `.ci/docker/manywheel/build.sh` (`PYTORCH_ROCM_ARCH`)
  - The torch index on download.pytorch.org (PEP 503 HTML; it does not serve PEP 691 JSON: on 2026-10-27 `Accept: application/vnd.pypi.simple.v1+json` returned text/html)
  - The "Minimum Required Driver Version for CUDA Minor Version Compatibility" table of the CUDA release notes
  - `comfy/model_management.py` at ComfyUI's latest tag (releases API), read with ast **without importing it** (GPL-3.0 code is never run)
- Tables are read by their header words, not column positions. Each source has time limits (10 s connect, 60 s read, 4 attempts); an unreadable source goes to "sources that could not be read" and the rest continues. Whatever fails in the watch, no exception leaves it.
- Candidates come from two checks:
  - **Against the tables**: official products whose gfx / compute capability differs from the tables; gfx, CUDA versions (once wheels exist) or CUDA major versions missing from the tables; device IDs whose verdict flips between the new pci.ids name and the device ID table; PyTorch and ComfyUI lists that differ from the tables.
  - **Against last time**: compared with `tests/fixtures/gpu_official` (the previous official lists, updated together with the tables): new or renamed device IDs, new NVIDIA names, TheRock status changes, additions / removals in the compatibility tables and Intel's table, new wheels.
- The result goes to the "GPU 表の見張り" (GPU table watch) section of the PR body (a table of candidates and the unreadable sources) and to `weekly-out/gpu_watch.json`.
- **The tables are never rewritten automatically.** A person checks each candidate against the primary source, adds a row (with its source and date) to `gputables.py` in a separate issue, refreshes the previous lists with `python -m genmodelfit.gpuwatch --save-fixtures tests/fixtures/gpu_official`, and regenerates the inventory with `python tools/gpu_inventory.py`.
- **It does not stop the automatic data merge.** Candidates and unreadable sources affect neither `ok` in `result.json` nor the weekly skill's safe-week rule (only db.json changed, merge-check, status).
- To run it by hand: `python -m genmodelfit.gpuwatch [--json]` (uses the network). `--from-dir <dir>` reads saved raw responses (`<source name>.<ext>`) without the network. Skip it in the weekly update with `--no-gpu-watch`.

### Weekly skill (genmodelfit-weekly, #42)

Maintainer's decision of 2026-10-21:

- The weekly update is run by a startup script on the maintainer's PC (Windows Task Scheduler, once a week).
- Only the judgment on entries awaiting review is delegated to the Claude Code skill `genmodelfit-weekly`.
- **The scheduled run (schedule) of weekly-data.yml was dropped.** It remains only as a manual backup (workflow_dispatch).

How it runs:

- The script and its settings live outside this repository, on the maintainer's PC. It runs the same commands as
  weekly-data.yml (rebuild, checks, tests) in a clone used only for this, synced to main on each run.
- Only the judgment on entries awaiting review goes to the Claude Code skill, read-only: it may read files and fetch
  pages from huggingface.co, github.com and raw.githubusercontent.com, and cannot run commands, use MCP or change files.
- approve is re-checked by a different model under the same restrictions.
- On failure an Issue "毎週の更新が失敗しました" ("The weekly update failed") is opened, and it is closed at the next success.

Roles are split.

- **Claude (the skill) does not rewrite files and does not run commands** (the maintainer's decision of 2026-10-21). It only reads, and uses WebFetch for Hugging Face and GitHub.
  - The runner passes `review_queue.json`, with the entries awaiting review: id, name, modality, reasons, and per file the repo, path, license, license_url, homepage and card_url.
  - Claude checks them against primary sources (license text, model card, upstream repository) and returns only a JSON list of decisions:

    ```json
    {"decisions": [
      {"id": "hf:org/model", "action": "approve", "evidence": ["https://huggingface.co/org/model/blob/main/LICENSE"], "note": "Apache-2.0 の原文を確かめた"},
      {"id": "hf:org/other", "action": "reject", "evidence": ["https://huggingface.co/org/other/blob/main/LICENSE"], "note": "非商用だけ", "safety": false},
      {"id": "hf:org/unclear", "action": "hold", "note": "カードにライセンスの記載なし"}
    ]}
    ```

  - A reject for a safety reason (such as sexual content involving minors) gets `"safety": true` (the name is not kept; only the sha256 is recorded).
  - Anything unclear is a hold (stays pending; it appears neither in auto-recommendations nor in data-latest).
- **The runner script** (on the maintainer's PC, outside the skill) does the rebuild, tests, applying decisions, merge-check and the PR:
  1. Run `python -m genmodelfit.weekly --out <dir> --apply src/genmodelfit/data/db.json`. Carry-over and the comparison baseline are main's db.json. If the exit code is 1, stop (see `problems` in `result.json`).
  2. Pass `<dir>/review_queue.json` to Claude and receive the list of decisions.
  3. Run `python -m genmodelfit.review apply <decisions.json> --db src/genmodelfit/data/db.json --max-approve 10 --json`.
     - The shape is checked strictly. These are rejected: unknown keys or actions, ids not awaiting review, duplicate ids, an approve without evidence, evidence that is not https or has no host, a note over 500 characters or with control characters, and going over the approve limit.
     - If even one is wrong, nothing is applied (exit code 2).
     - approve is recorded as `decided_by: claude-weekly`.
     - The summary it prints (counts and per-entry results) is made harmless by `weekly.md`, so it can go into the PR body as it is.
  4. Commit to a branch, push, and make a PR. **Merge only when every machine check passes:**
     - The only changed file is `src/genmodelfit/data/db.json`. In a week where approve or reject was applied, statuses change, so the condition below always sends the PR to the maintainer.
     - `python -m genmodelfit.weekly merge-check --base <main's db.json> --head <branch's db.json> --json` exits 0 (`safe: true`). The classification is recomputed from the file's JSON; the skill's report is not trusted. A flagged popularity-bias check (`popularity_bias:<type>`, docs/scoring.md "Popularity") also makes it not safe (0.11.0).
     - No review status changed. A change to approved or to awaiting review also becomes "needs judgment" in the classification.
     - Local tests (ruff and pytest, with the new list) passed. ci.yml does not run on PRs (the rule since 2026-10-22 is to pass locally before pushing), so CI is not awaited. ci.yml runs on main after the merge.

    If any check fails, do not merge. **The maintainer reads the PR and merges.** Look at the "classification" and "awaiting review" sections of the body, and at the `evidence` left by `review approve`.
- Outside the weekly skill, a PR that fixes an issue may run `review approve` on entries of the bundled list (`decided_by` is `claude-maker-<issue number>`; from 0.14.0). It does so to leave no pending entries in the bundled list (`tests/test_review_42.py`), and **the maintainer checks that `evidence` in the PR before merging**. The test checks that `decided_by` is an allowed name (`baseline-2026-10-21`, `claude-weekly`, `claude-maker-<number>`, the owner's login), that `evidence` is https URLs with a host, and that `decided_at` is between 2026-10-21 and today.
- Signing and publishing after the merge are done by data.yml, as before. **Entries awaiting review are removed from the list that is shipped** (`python -m genmodelfit.review publish`), because 0.5.x does not know `review` and would put them in auto-recommendations.

Conditions for a safe week: the classification is safe. Entries that stay awaiting review unchanged from before may be included.
The classification is decided by a **table of what is allowed** (classification in [schema.md, "diff"](schema.md#diff)). Allowed changes:

- popularity counts (`downloads`, `downloads_all_time`, `likes`, template `usage`);
- `updated`;
- HEAD results (`head_status`, `head_checked_at`);
- a license becoming stricter;
- removed entries;
- entries that dropped out of auto-recommendations;
- `sources` counts;
- entries that stay awaiting review unchanged.

Every other change (including unknown fields) needs judgment.

- An entry whose HEAD recovered and which returned to auto-recommendations is safe only when no file's URL or sha256 changed.
- Since 0.9.0, any change in a file's size needs judgment, in either direction (`files_changed`; `update` also puts it awaiting review).
- The reason: apps download the list's URLs as they are, so a file at the same URL getting bigger means the contents were swapped. That must not pass silently.
- A week when a file in an HF repository was overwritten is therefore not merged automatically, and the maintainer checks. Before, a file that only got bigger was merged as a safe week.

Decisions:

- Pending entries are also in main's list (they appear in `recommend` / `fit` / `info` from 0.6.0). The gate (auto-recommendation) holds them through `reviewed`.
  Apps filter with `entry.review is None or entry.review.status == "ok"`.
- Approval needs at least 1 evidence URL that is https and has a host. Without it, it is refused.
- An entry given `review reject` stays in `src/genmodelfit/data/review_rejected.txt`, and `update` does not add it back even when rebuilding.
- The bundled list started in 0.6.0 with everything `ok` (`decided_by: baseline-2026-10-21`).

### Trying the same flow locally

```powershell
.venv\Scripts\python -m genmodelfit.weekly --dry-run --out $env:TEMP\weekly-out                         # from upstream (writes only out)
.venv\Scripts\python -m genmodelfit.weekly --dry-run --out $env:TEMP\weekly-out --record $env:TEMP\weekly-rec   # also record responses
.venv\Scripts\python -m genmodelfit.weekly --dry-run --out $env:TEMP\weekly-out2 --offline-sources $env:TEMP\weekly-rec  # no network
.venv\Scripts\genmodelfit diff src\genmodelfit\data\db.json $env:TEMP\weekly-out\db.json
```

About `--offline-sources`:

- It rebuilds using only the GET responses and HEAD status codes saved by `--record`. File headers and repository precision come only from what is in the cache `GENMODELFIT_CACHE_DIR`.
- HEAD times become the time of the run, so the expiry check is not real.
- Failed responses (401, no connection) are not saved, so the re-read result differs a little from a real run (in the 2026-10-21 trial, 334 entries against 335).
- It is for trying the flow and seeing the shape of the PR body.
- To keep your local cache clean, point `GENMODELFIT_CACHE_DIR` somewhere else.

Exit codes: 0 = passed, 1 = a check failed (`problems` in `result.json`), 2 = usage error.

### One-time setup by the maintainer

- Settings → Actions → General → Workflow permissions: **turn on "Allow GitHub Actions to create and approve pull requests".** Without it, create-pull-request fails.
  Check with `gh api repos/laxshmate/genmodelfit/actions/permissions/workflow` (`can_approve_pull_request_reviews` must be true).
  - As its name says, this setting **also allows approving**; GitHub has no setting that allows only creating PRs.
  - weekly-data.yml does not approve (it uses neither `gh pr review` nor auto-merge; tests/test_workflows.py checks this).
  - The maintainer does review and merge.
- (Not relevant now, since the scheduled run was dropped.) **If a repository has no activity (commits and so on) for 60 days, GitHub stops its scheduled workflows.**
  If that happens, press "Enable workflow" on "Weekly data" in Actions, or run it manually (workflow_dispatch).
- Create the labels `automated` and `data-update`. The workflow also tries to create them, but `pull-requests: write` alone sometimes cannot:
  `gh label create automated -R laxshmate/genmodelfit`, `gh label create data-update -R laxshmate/genmodelfit`
- ci.yml jobs are skipped on PRs in the same repository (#68). Do not put ci.yml in the required checks of branch protection.
  Put the result of the local `tools/ci_local.py` in the PR description.

## Keys

- Public keys: `src/genmodelfit/data/signing_keys/*.pub` (bundled in the package). The importing side accepts a signature that matches any one of them. Today there is one, `2026-10.pub`.
- Private key: the secret `GENMODELFIT_SIGNING_KEY` of environment `data-signing` (laxshmate/genmodelfit).
- Private key backup: the maintainer keeps one copy offline, encrypted, in a single place readable only by the
  maintainer (the place is not written here). Do not commit it. Do not paste it on screen or in an Issue.

### Rotating keys (regularly, or as a precaution)

The public keys are bundled with each version, so **the keys a version trusts are fixed when the version is released.** Bundle the old and new keys side by side and switch in stages.

1. Create a new key (see below).
   - **Add** its public key as `src/genmodelfit/data/signing_keys/<year-month>.pub` (keep the old key).
   - Release this version and have users (especially AI生成キット) upgrade.
   - Signing still uses the old key at this point.
   - Both old and new versions accept signatures from the old key.
2. When the new version is widespread, switch the `data-signing` secret to the new key and replace the backup. Signing then uses the new key.
   - Versions from step 1 onward accept it.
   - Versions before step 1 can no longer accept new signatures (`--from-release` fails, and they keep working with the data they have).
3. In a later version, delete the old public key file.

How to create a key. Write the private key to a file only and never show it on screen. Before overwriting, keep the old backup under another name.

```powershell
python -c "
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as s
k = Ed25519PrivateKey.generate()
open(sys.argv[1], 'wb').write(k.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
open(sys.argv[2], 'wb').write(k.public_key().public_bytes(s.Encoding.PEM, s.PublicFormat.SubjectPublicKeyInfo))
" "<path-to-key>\signing_key.new.pem" src\genmodelfit\data\signing_keys\2027-04.pub
```

Keep the folder `<path-to-key>` readable only by the maintainer (on Windows, remove inherited permissions with
`icacls`).

To replace the secret in step 2 (pass the value on standard input, never on screen):

```powershell
cmd /c "gh secret set GENMODELFIT_SIGNING_KEY --env data-signing -R laxshmate/genmodelfit < ""<path-to-key>\signing_key.new.pem"""
```

### If a key leaks

Trust in signatures is lost, so move fast.

- Deleting `data-latest` is not enough. An attacker with the key can serve a fake list from a position on the network path, such as a corporate TLS-inspection device or a hijacked DNS.
- Harm continues as long as versions that trust the leaked key remain. **Recovery is replacing the key and shipping a new version.**

1. Delete the secret `GENMODELFIT_SIGNING_KEY` of `data-signing`, and disable data.yml.
2. Create a new key. **Delete** the leaked key's public key file, and release **urgently** a version that bundles only the new public key (no staged rotation). Have users (especially AI生成キット) upgrade.
3. Tell users of old versions that trust the leaked key not to use `--from-release` (they work with the bundled data alone).
4. Set the secret again with the new key, and ship a list signed with the new key through data.yml.

## What we do not use

- PyPI API tokens are not created or stored (Trusted Publishing only).
- The private key is not put in the repository, and not in repository-wide secrets. It is kept out of CI logs (it is read from an environment variable with `--key-env`).
