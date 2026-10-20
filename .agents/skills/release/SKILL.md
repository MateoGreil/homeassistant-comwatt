---
name: release
description: Use when asked to make or publish a release of homeassistant-comwatt, bump its integration version, or tag a new version. Includes requests such as "fais une release" or "publie une nouvelle version".
---

# Release homeassistant-comwatt

`origin` is Forgejo; `github` is GitHub (`MateoGreil/homeassistant-comwatt`). Forgejo is the source of truth. A push mirror may synchronize GitHub's `main` and tags; verify actual refs instead of assuming synchronization or timing.

Publish in this order: **release commit on Forgejo → tag on Forgejo → same commit and tag on GitHub → GitHub release**. Forgejo gets a tag only, not a release object. Use fast-forward pushes only; never force-push or move an existing tag.

## 1. Inspect synchronization without pushing

Check both remote URLs, CLI authentication (`gh auth status`), and working-tree status. Preserve unrelated changes.

```bash
git fetch origin --tags && git fetch github --tags
git rev-parse main origin/main github/main
gh release view -R MateoGreil/homeassistant-comwatt --json tagName --jq '.tagName'
```

Choose `release_base`: the newest approved commit containing all three mains. A GitHub PR merge may put `github/main` ahead of Forgejo; Forgejo or local `main` may also be ahead. Use `git merge-base --is-ancestor` to prove fast-forward relationships. If histories diverge, stop and ask the user how to reconcile them.

Use the last published release tag as `previous_tag`; inspect `git log "$previous_tag..$release_base"` and the manifest version at that base. If there are no changes, report it instead of publishing an empty release.

## 2. Propose and get approval before any push

Propose the version, changes, English release-notes plan, and any required synchronization. Recommend patch for fixes/dependencies/assets, minor for features, major for breaking changes; the user approves the version.

Wait for explicit confirmation **before any remote mutation, including pre-sync pushes**. After approval, proceed without routine confirmation prompts; stop on errors or changed/unexpected refs.

Set `version` to the approved number, `tag="v$version"`, and retain `release_base` and `previous_tag`. Check that the proposed tag and GitHub release do not already exist, locally or remotely. On a partial previous attempt, report existing artifacts rather than recreating or moving them.

## 3. Synchronize, isolate, and create the release commit

After approval, fast-forward older mains to `release_base`, Forgejo first. For a GitHub merge awaiting reconciliation:

```bash
git push origin "$release_base:refs/heads/main"
```

Verify Forgejo now has the approved base; if GitHub is behind, verify the mirror or fast-forward it manually. Safely fast-forward a clean local `main` as needed, without resetting or stashing unrelated work. Fetch again and verify all three mains match the approved base before editing.

Create a dedicated worktree (verify `.worktrees/` is ignored; otherwise add it to `.git/info/exclude`):

```bash
git worktree add ".worktrees/release-$version" -b "release-$version" "$release_base"
cd ".worktrees/release-$version"
```

Bump only `"version"` in `custom_components/comwatt/manifest.json`. Run the repository's tests and review the staged diff for the version-only change and absence of secrets/local artifacts.

```bash
git add custom_components/comwatt/manifest.json
git commit -m ":bookmark: Release $tag"
release_commit=$(git rev-parse HEAD)
```

Use the literal `:bookmark:` code, matching release commits in this repository.

## 4. Publish the commit and tag on Forgejo

Recheck remote `main` against `release_base`; if it changed since approval, stop and re-evaluate.

```bash
git push origin HEAD:refs/heads/main
git ls-remote origin refs/heads/main
git tag "$tag" "$release_commit"
git push origin "refs/tags/$tag"
git ls-remote origin refs/heads/main "refs/tags/$tag"
```

Verify the commit push succeeded before creating/pushing the tag. Both Forgejo refs must resolve to `release_commit`; stop on failure.

## 5. Verify or publish the same refs on GitHub

```bash
git ls-remote github refs/heads/main "refs/tags/$tag"
```

Both must resolve to `release_commit`. If the mirror has not synchronized, push missing refs explicitly, branch first then tag:

```bash
git push github HEAD:refs/heads/main
git push github "refs/tags/$tag"
```

Recheck both remotes. A conflicting existing tag or rejected push is a stop condition, not permission to force-push.

## 6. Publish the GitHub release with generated notes

Preview GitHub's default notes once the tag exists there:

```bash
gh api repos/MateoGreil/homeassistant-comwatt/releases/generate-notes \
  -f tag_name="$tag" -f previous_tag_name="$previous_tag" --jq '.body'
```

The default has `## What's Changed`, `## New Contributors` when applicable, and a bold `**Full Changelog**` compare link (not a heading). Inspect the result rather than assuming it covers direct commits.

```bash
gh release create "$tag" -R MateoGreil/homeassistant-comwatt \
  --title "$tag" --verify-tag --generate-notes --notes-start-tag "$previous_tag"
```

If generated notes contain no change entries despite direct commits, use `--notes-file` instead of `--generate-notes`/`--notes-start-tag`: write English bullets under `## What's Changed` from the commit range, keep the full compare link, and preserve any generated contributor section. Store the temporary notes outside tracked files. Do not reduce the fallback to a one-line summary.

## 7. Verify and report

Fetch updated refs, safely fast-forward a clean local `main` to the release commit, and check local/Forgejo/GitHub mains and both tags. Read the published release with `gh release view "$tag" -R MateoGreil/homeassistant-comwatt --json url,tagName,isDraft,isPrerelease,body` and verify its tag, notes, and published status.

Report the version, release URL, commit SHA, and synchronization table. If the local checkout cannot be updated safely, report that explicitly. Remove the release worktree once published; on failure report the last successful step and retain the workspace for recovery.
