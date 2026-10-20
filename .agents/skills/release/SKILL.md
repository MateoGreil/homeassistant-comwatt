---
name: release
description: Use when asked to make, cut, or publish a release of this repository (homeassistant-comwatt), bump the integration version, tag a release, or create the GitHub release. Triggers include "release", "fais une release", "nouvelle version", "cut a release".
---

# Release homeassistant-comwatt

Cut a release across the two remotes. `origin` (Forgejo, git.greil.fr) is the source of truth; a Forgejo→GitHub push mirror replicates every push to the `github` remote within seconds. Never push `main` or tags to `github` before they exist on Forgejo, and never push with `--force`.

## 1. Sync the three `main`s

```bash
git fetch origin && git fetch github
git rev-parse main origin/main github/main   # must all match
```

If `github/main` is ahead (a GitHub PR was just merged), catch the local checkout and Forgejo up:

```bash
git checkout main && git merge --ff-only github/main
git push origin main
```

Fast-forward only. If the two remotes have genuinely diverged, stop and reconcile with the user — do not guess.

## 2. Propose — stop for the user's OK

- List commits since the last tag: `git log --oneline $(git describe --tags --abbrev=0)..main`
- Pick the version: patch for fixes/deps/assets, minor for features, major for breaking changes
- Release notes will be GitHub-generated

Present the version and the plan, then wait for explicit confirmation. This is the only stop; after it, run the whole flow without pausing.

## 3. Release commit

Bump only `"version"` in `custom_components/comwatt/manifest.json`, commit with the exact convention:

```
:bookmark: Release vX.Y.Z
```

## 4. Push to Forgejo first, then tag

```bash
git push origin main
git tag vX.Y.Z && git push origin vX.Y.Z
```

## 5. Verify the mirror synced GitHub

```bash
git ls-remote github main refs/tags/vX.Y.Z
```

Both refs must point at the release commit. If the mirror lagged or is down, push manually: `git push github main vX.Y.Z`.

## 6. GitHub release

```bash
gh release create vX.Y.Z -R MateoGreil/homeassistant-comwatt --title "vX.Y.Z" --generate-notes
```

`--generate-notes` lists only GitHub-merged PRs. If there are none since the previous tag, preview first:

```bash
gh api repos/MateoGreil/homeassistant-comwatt/releases/generate-notes \
  -f tag_name=vX.Y.Z -f previous_tag_name=vPREV --jq '.body'
```

An empty "What's Changed" means the work landed as direct commits: write a short English summary of those commits and pass `--notes "..."` instead of `--generate-notes`.

## 7. Report

Output a table of local `main`, Forgejo, GitHub, the tag, and the release URL — all at the same commit.

## Common mistakes

| Mistake | Reality |
|---|---|
| Pushing to `github` first | The mirror is one-directional Forgejo→GitHub; GitHub-first pushes drift out of sync or get overwritten |
| Editing manifest beyond the version | Release commits bump the version only — one file, one line |
| A literal 🔖 emoji in the commit message | The convention is the `:bookmark:` code, like every previous release commit |
| Force-pushing to reconcile | Diverged mains are reconciled by explicit merge, never forced |
| Trusting the mirror blindly | Verify with `ls-remote` after every push; fall back to a manual push |
