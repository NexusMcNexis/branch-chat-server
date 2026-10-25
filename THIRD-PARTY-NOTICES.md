# Third-party notices

BranchChat's own code is licensed under [LICENSE.md](LICENSE.md) (the BranchChat License 2.0)
(see [LICENSING.md](LICENSING.md)). That license does **not** cover the components below; each keeps its own license.

Items marked **[UNCONFIRMED]** have no license file or header in this repository; the license given is the one the
upstream project publishes, and it still needs checking against the exact bundled version.

| Component | Where | License | Status |
|---|---|---|---|
| Fluent Emoji (Microsoft) | `web/static/emoji/fluent/` | MIT | Confirmed: `LICENSE-MIT.txt` |
| Fluent Emoji Animated (Classic Reactions stickers) | `web/static/stickers/classic/` | MIT | Confirmed: `LICENSE.txt` |
| Noto Emoji (Google) SVGs, flags from region-flags | `web/static/emoji/noto/` | Apache-2.0 (flags: public domain) | **[MISMATCH]** README says Apache-2.0, but `LICENSE-Apache-2.0.txt` contains the SIL OFL 1.1 font text. Upstream: images Apache-2.0, fonts OFL-1.1. Replace the file with the Apache-2.0 text. |
| Twemoji 15.0 (@twemoji/svg) | `web/static/emoji/twemoji/` | CC-BY-4.0 | Confirmed: `emoji/README.txt` and About page credit (no license file in folder) |
| Outfit font | `web/static/outfit.woff` | SIL OFL 1.1 | **[UNCONFIRMED]** no license file shipped; OFL requires the license text to travel with the font |
| libheif-js 1.23.5 / libheif / libde265 | `web/vendor/libheif/` | LGPL-3.0 | Confirmed: `LICENSE`, `LICENSE-libheif-js` |
| ffmpeg.wasm core 0.12.9 (@ffmpeg/core) | `web/vendor/core/` | GPL-2.0-or-later (built with `--enable-gpl`, x264, x265) | **[UNCONFIRMED / NEEDS REVIEW]** build flags read from the wasm; no license file or source offer shipped. GPL needs its text and a source offer. |
| @ffmpeg/ffmpeg wrapper | `web/vendor/ffmpeg/` | MIT | **[UNCONFIRMED]** no header in repo |
| mp4-muxer | `web/vendor/mp4-muxer.mjs` | MIT | **[UNCONFIRMED]** no header in repo |
| mp4box.js (2021-08-20 build) | `web/vendor/mp4box.all.min.js` | BSD-3-Clause | **[UNCONFIRMED]** no header in repo |
| Woodland / painter / robot / turtle sound effects and voices | `web/static/woodland/`, `web/static/painter/` | CC0-1.0 (made by BranchChat; voices made with Parler-TTS mini v1, Apache-2.0) | Confirmed: `LICENSES-sounds.txt` |
| Ambient "Forest wind" ("wind in trees" by chimerical, Freesound) | `web/static/ambient/forest-wind.mp3` | CC0-1.0 | Confirmed: About page and patch notes |
| Ambient Leaves and birds, Campfire, Elevator | `web/static/ambient/` | CC0-1.0 (made by BranchChat) | Confirmed: patch notes only (no license file) |
| BranchChat character stickers and art | `web/static/stickers/*` (except `classic`) | BranchChat's own work (LICENSE.md) | Confirmed: original art |
| Electron 44.8.0 (includes Chromium, Node.js, FFmpeg) | desktop app builds | MIT (Electron); Chromium BSD-3-Clause plus many others | Electron ships its own `LICENSE` and `LICENSES.chromium.html` inside every build |
| electron-builder 26.15.3 | desktop build tool only, not shipped | MIT | Dev dependency |
| cryptography (Python) | server, `server/requirements.txt` | Apache-2.0 OR BSD-3-Clause | Installed separately, not bundled |
| Python standard library | server | PSF-2.0 | Not bundled |
| AndroidX / Android libraries | Android app (`android-share-media` branch) | Apache-2.0 | **[UNCONFIRMED]** not checked on this branch |
| Server icon credit | per server, `BC_CREDITS_FILE` | Set by each server owner | n/a |

## Microsoft Fluent Emoji (emoji style and Classic Reactions sticker pack)
Files: `web/static/emoji/fluent/*` and `web/static/stickers/classic/*` (resized and converted from
https://github.com/microsoft/fluentui-emoji and https://github.com/microsoft/fluentui-emoji-animated).

```
MIT License

Copyright (c) Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE
```

The BranchChat character stickers (`web/static/stickers/friends`, `badger`, `owl`, `bunny`, `treant`, `painter`,
`fisherman`, `robot`, `turtle`, `fox`) are original BranchChat art, drawn from the app's own delete-animation characters.

## Noto Emoji
Files: `web/static/emoji/noto/*`, from https://github.com/googlefonts/noto-emoji (2D SVGs), (c) Google LLC,
Apache License 2.0, https://www.apache.org/licenses/LICENSE-2.0. Flags from third_party/region-flags (public domain).
Changes: SVGs optimized for size (svgo).

## Twemoji
Files: `web/static/emoji/twemoji/*`, Twemoji 15.0 (https://github.com/jdecked/twemoji). Graphics (c) Twitter, Inc and
other contributors, CC BY 4.0, https://creativecommons.org/licenses/by/4.0/. Changes: SVGs optimized for size (svgo).

## libheif-js 1.23.5 (libheif 1.23, libde265) — web/vendor/libheif/
HEIC/HEIF decoding in browsers that cannot draw HEIC themselves. Loaded on demand as an unmodified WebAssembly bundle
(libheif-bundle.mjs). License: LGPL-3.0 (libheif, libheif-js; libde265 LGPL-3.0). License texts: web/vendor/libheif/LICENSE
and LICENSE-libheif-js. Source: https://github.com/catdad-experiments/libheif-js and https://github.com/strukturag/libheif.

## ffmpeg.wasm — web/vendor/core/, web/vendor/ffmpeg/
Video conversion in the browser, loaded on demand. @ffmpeg/core 0.12.9 is FFmpeg compiled with `--enable-gpl` and
libx264/libx265, so it is under the GNU GPL 2.0 or later. Source: https://github.com/ffmpegwasm/ffmpeg.wasm and
https://ffmpeg.org. The @ffmpeg/ffmpeg wrapper is MIT. [UNCONFIRMED: license files to be added.]

## mp4-muxer and mp4box.js — web/vendor/
mp4-muxer (https://github.com/Vanilagy/mp4-muxer, MIT) and mp4box.js (https://github.com/gpac/mp4box.js, BSD-3-Clause).
[UNCONFIRMED: no license text in the bundled files.]

## Sounds
See `web/static/woodland/LICENSES-sounds.txt`. All delete-animation sounds and voices were made by BranchChat and are
dedicated CC0 1.0. Forest wind: "wind in trees" by chimerical (https://freesound.org/people/chimerical/sounds/104282/), CC0.

## Electron (desktop app)
The desktop app is built on Electron (https://github.com/electron/electron, MIT, (c) Electron contributors), which
includes Chromium and other components; their full notices ship in every build as `LICENSE` and `LICENSES.chromium.html`.

## Hunspell en-US dictionary (desktop/build/dict/en-US-10-1.bdic)
Chromium's en-US spell-check dictionary (from chromium/deps/hunspell_dictionaries, SCOWL-derived word list; BSD/MIT-style licenses, see https://chromium.googlesource.com/chromium/deps/hunspell_dictionaries/). Bundled unmodified so desktop spell check works offline.
