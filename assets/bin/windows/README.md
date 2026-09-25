# Windows bundled binaries

This directory mirrors the existing `assets/bin/linux/<arch>/` convention that
`find_aria2()` (src/core/utils.py) already searches.

Populate at Windows build/release time with:

- `x86_64/aria2c.exe`
- `arm64/aria2c.exe` (only if you have a genuine Windows-ARM64 aria2 build --
  aria2 does not currently publish an official one; x86_64 emulation is the
  practical fallback on Windows-on-ARM)

If this directory is left empty (as it is by default), `ensure_aria2()` will
fall back to downloading aria2c.exe from the official aria2/aria2 GitHub
release at first run instead. Bundling it here is purely an optimization to
avoid that first-run download.
