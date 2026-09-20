# Releasing wave-alpha

Releases are **fully automated**. Push a [conventional commit](https://www.conventionalcommits.org/) to `main`, and the release pipeline does the rest:

```
push to main           ── version.yml ──▶ bumps version + tags vX.Y.Z + pushes
   (feat:/fix:/...)                             │
                                                ▼
                                          publish.yml ──▶ verifies tag → builds → publishes to PyPI → creates GitHub Release
```

## Conventional commit → version bump

| Commit prefix       | Bump        | Example                                      |
|---------------------|-------------|----------------------------------------------|
| `fix:`              | patch       | `fix(web): align α popover with default`     |
| `feat:`             | minor       | `feat(scan): add 4h-only scan mode`          |
| `feat!:` / `BREAKING CHANGE:` footer | major | `feat!: drop Python 3.10 support`            |
| `docs:` / `chore:` / `refactor:` / `test:` / `style:` / `ci:` | none | (no release) |

Scopes (the `(web)`, `(scan)` part) are optional but encouraged — see existing commits for conventions.

If a push to `main` only contains non-release commits, no version bump happens. To force a release without a `feat/fix` commit, use the manual trigger (below).

## One-time setup

Before the first automated release works, three things must be configured. Each is done **once** and then forgotten.

### 1. PyPI Trusted Publisher

1. Sign in to https://pypi.org and open the **wave-alpha** project page.
2. Project settings → **Publishing** → **Add a new publisher** → GitHub.
3. Fill in:
   - **Owner:** `chen-star`
   - **Repository name:** `wave-alpha`
   - **Workflow name:** `publish.yml`
   - **Environment name:** `pypi`
4. Save.

(For a *brand-new* PyPI project, use the "Pending publisher" form on https://pypi.org/manage/account/publishing/ instead — same fields. The first successful run claims the project name.)

### 2. GitHub `pypi` environment

1. Open the repo on GitHub → **Settings** → **Environments** → **New environment**.
2. Name it exactly `pypi`.
3. (Optional but recommended) Add a deployment protection rule limiting it to tags matching `v*.*.*`.

### 3. `RELEASE_TOKEN` secret (for `version.yml`)

The default `GITHUB_TOKEN` cannot push commits that re-trigger workflows, so we need a personal access token (or fine-grained app token) for the version-bump job.

1. Generate a fine-grained PAT at https://github.com/settings/personal-access-tokens/new:
   - **Resource owner:** `chen-star`
   - **Repository access:** Only select repositories → `wave-alpha`
   - **Repository permissions:** `Contents: Read and write`, `Metadata: Read-only`
2. Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
3. Name: `RELEASE_TOKEN`. Value: the PAT from step 1.

## Day-to-day

Just commit and push.

```bash
git commit -m "feat(scan): add 4h-only mode"
git push origin main
# version.yml runs → bumps to 0.7.0 → tags v0.7.0
# publish.yml runs → builds → publishes to PyPI → GitHub Release
```

`version.yml` skips itself when the head commit message starts with `chore(release):`, so no infinite loops.

## Manual release (no qualifying commits)

If you want to cut a release without a `feat`/`fix` commit (e.g., a packaging-only change, or the very first release), trigger `version.yml` manually:

1. Repo → **Actions** → **version** → **Run workflow**.
2. Pick `patch` / `minor` / `major`. Run.

> **Heads-up:** `version.yml` has `paths-ignore` for `docs/**`, `**/*.md`, and `.github/**`, and **does not fire on empty commits** (no changed paths means nothing to compare against the ignore list). For the very first smoke test of a fresh setup, prefer the `workflow_dispatch` button above over `git commit --allow-empty`.

## Pre-release versions

Tag manually as `vX.Y.Z-rc1` if you need a release candidate. PyPI lists them as pre-releases. Bypassing semantic-release for pre-releases keeps the configuration simple.

## CHANGELOG.md

`CHANGELOG.md` is currently maintained by hand under `## [Unreleased]`. semantic-release v8 is configured with `changelog.mode = "update"`, which inserts auto-generated entries at the top of the file on each release while preserving older content. If you prefer to keep the hand-written narrative as the source of truth, edit each release section after the auto-bump commit lands and re-tag (or just leave it — both formats coexist).

## If something goes wrong

- **`version.yml` fails to push:** the `RELEASE_TOKEN` likely lacks `Contents: write` or expired. Regenerate.
- **Tag pushed but publish fails:** delete the tag and retry — `git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`, fix root cause, push another conventional commit.
- **Wheel uploaded but is broken:** PyPI does not allow re-uploading the same version. Push a `fix:` commit; that bumps to `X.Y.(Z+1)`. Never reuse a version number.
- **OIDC publish fails:** Trusted Publisher config must match `chen-star/wave-alpha` + `publish.yml` + `pypi` environment exactly. A typo in any field aborts the upload.
- **Tag rejected as "not on main":** `publish.yml` refuses to publish a tag whose commit isn't an ancestor of `origin/main`. Don't tag from feature branches.

## Smoke test after publishing

```bash
uvx --refresh wave-alpha==X.Y.Z version
```
