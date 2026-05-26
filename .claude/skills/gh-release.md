---
name: gh-release
description: >-
  Publish a new GitHub release for the Focus app. Always runs build.bat first
  to compile a fresh dist\Focus.exe from the current source before uploading.
  TRIGGER when: user says "release", "publish a release", "create a release",
  or invokes /gh-release. DO NOT use dist\Focus.exe from a prior build.
origin: project
---

# gh-release — Focus App Release Publisher

Builds a fresh Focus.exe from the current source and publishes it as a GitHub release.

## When to Use

- Publishing a new version of the Focus app to GitHub Releases
- Any time the user says "release", "create a release", or "publish a release"

**Never upload `dist\Focus.exe` without running `build.bat` first.** The file on disk may be stale from a previous session.

## Pre-flight Checks

Before building, verify:

1. **Focus.exe is not running** — PyInstaller cannot overwrite a locked file. Check with:
   ```powershell
   Get-Process -Name "Focus" -ErrorAction SilentlyContinue
   ```
   If it is running, tell the user to close Focus before proceeding. Do not kill it silently.

2. **Determine the version tag** — If the user did not supply a tag (e.g. `v1.2.0`), ask them for it now before starting the build. Format must be `vMAJOR.MINOR.PATCH`.

3. **Confirm the tag does not already exist** — Run:
   ```powershell
   git tag --list "<tag>"
   ```
   If it already exists, stop and ask the user whether they want to overwrite it or use a different tag.

## Build Step (mandatory)

Run the release build:

```powershell
.\build.bat
```

This compiles `dist\Focus.exe` as a single portable file using PyInstaller `--onefile`. It installs dependencies, generates the icon if missing, and deletes any old `dist\Focus.exe` before writing the new one.

**Verify the build succeeded:**
- `build.bat` must exit with code 0
- `dist\Focus.exe` must exist after the build
- Report the file size as a sanity check

If the build fails, stop. Do not proceed to the release step. Show the error output to the user.

## Release Step

Once the build is confirmed good:

1. **Create and push the git tag:**
   ```powershell
   git tag <tag>
   git push origin <tag>
   ```

2. **Create the GitHub release:**
   ```powershell
   gh release create <tag> dist\Focus.exe `
     --title "Focus <tag>" `
     --notes "<release notes>"
   ```

   For release notes, summarize the commits since the previous tag:
   ```powershell
   git log <previous-tag>..HEAD --oneline
   ```
   If there is no previous tag, summarize all commits. Keep notes concise — bullet points per commit are fine.

3. **Confirm success** — Print the release URL returned by `gh release create`.

## Example Flow

```
User: /gh-release v1.2.0

1. Check Focus.exe is not running        → not running, OK
2. Check tag v1.2.0 does not exist       → new tag, OK
3. Run build.bat                         → SUCCESS, dist\Focus.exe = 14.2 MB
4. git tag v1.2.0 && git push origin v1.2.0
5. gh release create v1.2.0 dist\Focus.exe --title "Focus v1.2.0" --notes "..."
6. Release published: https://github.com/Albertovh05/focus/releases/tag/v1.2.0
```

## Failure Modes

| Problem | Action |
|---------|--------|
| Focus.exe is running | Tell user to close the app first. Stop. |
| Tag already exists | Ask user: overwrite or pick a new tag? |
| build.bat exits non-zero | Show error. Stop. Do not release. |
| dist\Focus.exe missing after build | Build silently failed. Show error. Stop. |
| gh not authenticated | Run `gh auth status` and tell user to re-authenticate. |
