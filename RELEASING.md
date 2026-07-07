# Releasing kryten-moderator

This document describes how to cut a release, monitor CI, and confirm the package is live on PyPI.

## How the release pipeline works

```
push to main (pyproject.toml changed)
  └─ Release workflow
       ├─ reads version from pyproject.toml
       ├─ creates git tag  v<version>
       ├─ creates GitHub Release (body from CHANGELOG.md)
       └─ calls python-publish.yml
            ├─ builds sdist + wheel  (uv build)
            └─ uploads to PyPI       (pypa/gh-action-pypi-publish)
```

The trigger is simple: **a push to `main` that changes `pyproject.toml`**.  
No manual tag creation or GitHub Release drafting is needed.

---

## Step-by-step release process

### 1. Do the work on a feature branch

```bash
git checkout -b 0.X.Y          # or feature/<name>
# … make changes, commit …
git push origin 0.X.Y
```

### 2. Update the version in `pyproject.toml`

Bump `version = "..."` according to [Semantic Versioning](https://semver.org):

| Change type | Example |
|---|---|
| Bug fix / patch | `0.7.0` → `0.7.1` |
| New feature, backwards-compatible | `0.7.1` → `0.8.0` |
| Breaking change | `0.8.0` → `1.0.0` |

```toml
# pyproject.toml
[project]
version = "0.7.1"
```

### 3. Add a CHANGELOG entry

Add a section at the top of `CHANGELOG.md` (below the header, above the previous release):

```markdown
## [0.7.1] - 2026-07-06

### Fixed

- **Short description**: longer explanation of what changed and why.
```

The Release workflow extracts this section verbatim as the GitHub Release body, so keep the formatting clean.

### 4. Commit both files together

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "fix: short description (v0.7.1)"
git push origin 0.X.Y
```

### 5. Merge to `main`

Open a PR and merge it (or push directly if you have access):

```bash
# Via gh CLI — merge the open PR
gh pr merge <PR-number> --merge

# Or push directly if working on main
git push origin main
```

> **Important**: make sure the commit with the bumped `pyproject.toml` is the one that lands on `main`.  
> If the branch has diverged since you last pushed, rebase before merging:
> ```bash
> git fetch origin
> git rebase origin/<branch>
> git push origin <branch>
> ```

---

## Watching the CI pipeline

Once the push to `main` lands, two workflows run:

| Workflow | Trigger | What it does |
|---|---|---|
| **CI** | push to main | runs tests, lint, type-check |
| **Release** | push to main + `pyproject.toml` changed | tags, creates GitHub Release, publishes to PyPI |

### Watch both runs

```bash
# List the two most-recent runs and grab their IDs
gh run list --limit 4 -R grobertson/kryten-moderator

# Watch the Release workflow (replace <ID> with the databaseId)
gh run watch <ID> -R grobertson/kryten-moderator
```

A successful Release run shows three green jobs:

```
✓ release
✓ Publish to PyPI / Build distribution packages
✓ Publish to PyPI / Publish to PyPI
```

### Confirm the tag and GitHub Release were created

```bash
gh release view v0.7.1 -R grobertson/kryten-moderator
```

---

## Confirming the package is live on PyPI

PyPI takes 10–30 seconds to propagate after the upload job finishes.

```bash
pip index versions kryten-moderator
# kryten-moderator (0.7.1, 0.7.0, ...)
```

Or check the web page: https://pypi.org/project/kryten-moderator/

---

## Recovering from a failed or partial release

### The tag was created but publish failed

Re-run just the publish job from the GitHub Actions UI, or trigger it manually:

```bash
gh workflow run python-publish.yml -R grobertson/kryten-moderator --ref v0.7.1
```

### The commit with the version bump landed on `main` but the tag already exists

The Release workflow checks for an existing tag and skips tagging/publishing if found.  
Delete the stale tag first, then re-push to re-trigger:

```bash
git push origin :refs/tags/v0.7.1          # delete remote tag
git tag -d v0.7.1                           # delete local tag (if present)
git commit --allow-empty -m "chore: re-trigger release v0.7.1"
git push origin main
```

### The feature branch was merged before the version-bump commit was included

Cherry-pick the orphaned commit onto `main` directly:

```bash
git log --oneline --all | grep "v0.7.1"    # find the commit hash
git checkout main
git cherry-pick <hash>
git push origin main
```
