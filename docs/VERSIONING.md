# Branch Chat version numbers

One number for everything: server, web app, Android, desktop (Linux/Windows/macOS) and iOS.

* Format **`YY.MM.PATCH`**: `26.10.0` is the October 2026 feature release; `26.10.1` is a fix to it.
* Betas (test server first): `26.11.0-beta.1`, `-beta.2`, …; the final drops the suffix.
* **Compatibility:** an app and a server work together when `YY.MM` matches. PATCH can differ.
  Servers answer `GET /api/version` → `{"version": "26.10.0", "api": "26.10", "min_app": "26.10.0"}`.
  An app older than `min_app` asks you to update; a server older than the app keeps working and the app notes it.
* **Single source of truth:** the `VERSION` file at the repo root.
  * Server: `web/bcconfig.py` reads it (`BC_VERSION` overrides; Docker and install.sh write `VERSION` next to the code).
  * Web: nothing to stamp. Pages, labels, aria-labels, `?v=` cache-busters and the service-worker cache name carry `{{BC_VERSION}}`-style placeholders that server.py fills from VERSION as it serves them; `/api/version` reads the same value. `tools/release-check.py` guards it.
  * Android: `android/app/build.gradle` reads it. Since 26.10.13.13 versionCode = `YY·1,000,000 + MM·10,000 + PATCH·100 + BUILD`
    (BUILD = 4th part, 0-99; prerelease suffix ignored, so a final after a beta bumps BUILD). 26.10.13.13 → 26101313.
    The old formula (`MM·1,000 + PATCH·10 + branchBuild`) ended at 26010131 (26.10.13.12); the build refuses anything not above it.
    `tools/bcversion.py android-code VERSION` prints it.
  * Desktop: `npm run dist*` runs `version:sync` (desktop/version-sync.js): package.json `version` = the semver part
    (electron-builder garbles 4 parts), `bcVersion` = full VERSION (shown in the app, compared by the updater), and the
    file names `BranchChat-<VERSION>-<arch>.AppImage`.
  * Updaters: desktop/version.js and tools/bcversion.py are the ONE comparator (strictly newer only; a beta is below its
    release; shared vectors in tests/version/vectors.json). Android compares versionCode from the same formula.
  * iOS: `ios/project.yml` `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` (same formula as Android).
* GitHub releases are tagged `v<VERSION>` and carry every platform's apps, the server packages and SHA256SUMS.
* **Release gate (26.10.13.13):** every release ships EVERY client built from the same VERSION.
  `tools/make-update-manifest.py` refuses a missing platform (`--carry PLATFORM=old update.json` only with a loud
  VERSION SKEW warning). Before publishing run `tools/version-check.sh --dist DIR --tag v<VERSION>` (APK name/code,
  AppImage app version, update.json fields/URLs/sha256, file names, tag); after deploy `tools/version-check.sh --live`
  checks `/api/version` on every server in `tools/servers.list` (the one deploy list; add servers there).
  Tests: `python3 tests/version/test_comparator.py`, `node tests/version/test_comparator.js`,
  `python3 tests/version/test_version_check.py`.

## What's new pop-up (every release, since 26.10.10)

Each release needs `web/static/whatsnew/<VERSION>.json` (copy the previous one and rewrite it). Signed-in users see it
once in a closeable pop-up the first time they open the app on that version (browsers, Android and desktop apps alike;
brand-new accounts never do), and it can be reopened from the menu's "What's new". Write it like the feature-update
email: `title`, a warm `intro`, `sections` of `{heading, items: [{icon, title, text}]}`, `thanks`, `signoff`
("God bless, and happy chatting!") and `signer`. Only list what is live on the servers. Keep it family friendly and
generic (no server names: the pop-up adds this server's name and icon itself). `tools/release-check.py` warns when the
file is missing and fails when it is broken.
