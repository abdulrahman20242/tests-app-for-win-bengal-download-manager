# Windows Port — Changes Made

This documents every change applied on top of the original codebase to address the
blockers identified in the prior Windows compatibility audit. Linux behavior is
preserved throughout — every change is additive (`platform.system() == "Windows"`
branches) rather than a replacement of existing logic, per the audit's recommended
strategy.

**What this is:** a source-code patch that makes the app correctly Windows-aware.
**What this is not:** a compiled, tested `.exe`. That step has to happen on an actual
Windows machine or Windows CI runner — see "What's left to do" at the bottom.

---

## Bug fixes from real-world testing

This section will grow as issues get reported from actually running the app on
Windows -- exactly the kind of verification this sandbox can't do on its own.

### 1. `[WinError 183]` on yt-dlp download: `yt-dlp.tmp -> yt-dlp.exe`
- **Report**: `Failed to download yt-dlp binary: [WinError 183] Cannot create a file
  when that file already exists: '...\bin\yt-dlp.tmp' -> '...\bin\yt-dlp.exe'`
- **Root cause**: `src/core/media_downloader.py`, `YtDlpManager.ensure_binary()` (was
  line 380) called `tmp_path.rename(YT_DLP_BIN)`. `Path.rename()` wraps `os.rename()`,
  which on POSIX silently replaces an existing destination but on **Windows raises an
  error if the destination already exists** -- a classic POSIX/Windows semantic gap.
  Most likely trigger: two extraction workers started close together (e.g. queuing
  several video URLs at once) both saw `is_binary_available() == False` and both
  attempted the download; whichever finished second hit the conflict.
- **Fix**: changed to `tmp_path.replace(YT_DLP_BIN)`. `Path.replace()`/`os.replace()`
  is explicitly documented to unconditionally replace the destination on **both**
  Windows and POSIX, which is exactly the "silently overwrite with the newer download"
  behavior wanted here.
- **Checked for the same pattern elsewhere**: grepped the entire `src/` tree for every
  remaining `.rename()`/`os.rename()` call. The only other rename-based finalization
  (`DependencyManagerWorker._download_and_install_tool`'s "direct" branch, used by the
  *other*, non-`YtDlpManager` yt-dlp download path) already explicitly deletes the
  destination before moving (`if dest.exists(): dest.unlink()`), so it does not hit
  this same error -- left unchanged rather than "fixed" for something it doesn't have.
- **Verified**: reproduced the exact reported precondition (destination file already
  present, fresh `.tmp` file ready to finalize) in a throwaway temp directory and
  confirmed `Path.replace()` completes without error and the destination ends up with
  the new content, no stale tmp file left behind.

### 2. Ctrl+C doesn't stop the app when run via `python src/main.py`
- **Report**: pressing Ctrl+C repeatedly printed the same traceback
  (`KeyboardInterrupt` inside `main_window.py`'s `_check_scheduled_queues`) without
  the process ever exiting.
- **Root cause**: this is a well-known category of PyQt/PySide issue, not something
  specific to this codebase, but it wasn't handled here. Qt's C++ event loop
  (`app.exec()`) blocks the Python interpreter from getting a chance to notice a
  pending OS signal. Python's *default* SIGINT handler just raises
  `KeyboardInterrupt` at the next available bytecode checkpoint -- inside a Qt app,
  that checkpoint only comes when some Qt-driven callback happens to run. Here, that
  was `_check_scheduled_queues`, confirmed wired to `self.scheduler_timer.timeout`.
  PyQt6 catches unhandled exceptions raised inside slots and keeps the event loop
  running rather than letting them propagate, so the interrupt got logged and
  swallowed instead of exiting. `src/main.py` also sets
  `app.setQuitOnLastWindowClosed(False)` (intentional -- the app is designed to keep
  running in the tray after the window closes), so there was no "closing the last
  window" fallback either.
- **Fix** (`src/main.py`, right after `window = MainWindow()`): installs a real
  `signal.signal(signal.SIGINT, ...)` handler that schedules the *same* `quit_app()`
  method the tray/menu "Exit" action already uses, via `QTimer.singleShot(0, ...)` so
  it runs as a normal, safely-scheduled Qt callback rather than synchronously inside
  the raw signal handler. This preserves the existing cleanup sequence (stopping the
  IPC listener, closing dialogs, terminating the aria2 daemon, saving state) instead
  of bypassing it. A short 200ms no-op `QTimer` is also started so Python gets a
  prompt, predictable chance to notice the signal rather than depending on whichever
  other timer happens to fire next.
- **Verified**: sent a real `SIGINT` to a live Python process (`os.kill(pid,
  signal.SIGINT)` -- the same signal Ctrl+C sends) using the exact handler-registration
  code now in `main.py`, and confirmed it correctly invokes the handler (which
  schedules `quit_app()`) instead of raising a bare `KeyboardInterrupt`. The Qt event
  loop / GUI side of this can't be exercised in this sandbox (no PyQt6, no display),
  so please confirm Ctrl+C now exits cleanly (including the aria2c.exe process
  actually terminating) when you get a chance to test.

### 3. `[WinError 10013]` when yt-dlp tries to reach youtube.com
Reported alongside the above, but **not a code bug** -- flagging it here for the
record rather than in "Files changed" since nothing was changed for it.
`WinError 10013` (`WSAEACCES`) on an *outbound* connection almost always means
something in the Windows network stack is refusing the connection before it leaves
the machine: Windows Firewall or third-party antivirus blocking the (unsigned,
freshly-downloaded) `yt-dlp.exe`/`python.exe`, an active VPN client, or -- a specific,
fairly common cause on machines with Hyper-V/WSL2/Docker Desktop installed -- the
OS's dynamic port range having been exhausted/reserved by one of those features,
which can make normal outbound `connect()` calls fail with exactly this error. None
of `get_clean_env()`'s stripped environment variables relate to networking/firewall
permissions, so this isn't something the port introduced. Worth checking Windows
Firewall's rules for the app, temporarily disabling third-party AV to test, and (if
Hyper-V/WSL2/Docker Desktop is installed) running `netsh interface ipv4 show
excludedportrange protocol=tcp` in an elevated PowerShell to check for an
unreasonably large reserved range.

---

---

## Round 3: Real Windows test run + test-guard review

A full `pytest -v tests/` run on real Windows (172 passed, 10 failed) plus a
test-guard code review of the suite surfaced real, confirmed issues. This section
covers both.

### Real Windows bugs found and fixed

#### 4. `get_process_memory()` returns `0` on Windows
- **Report**: `test_get_process_memory` failed on real Windows; flagged as "may be
  a real Windows code bug."
- **Root cause**: classic 64-bit ctypes pitfall, confirmed against CPython core
  developer commentary and the canonical reference implementation for this exact
  task (see citations checked during this session). `GetCurrentProcess()` returns a
  pointer-sized `HANDLE` (the pseudo-handle `-1`, all 64 bits set). Without an
  explicit `.restype`, ctypes defaults to `c_int` (32-bit) and **truncates** it; the
  truncated value then gets marshaled incorrectly into `GetProcessMemoryInfo`'s
  `HANDLE` argument (no `.argtypes` declared there either), which no longer
  recognizes it as a valid handle. `GetProcessMemoryInfo` fails, the code falls
  through to `import resource` (POSIX-only, raises `ImportError` on Windows), and
  the outer `except Exception: return 0` fires -- exactly the reported symptom.
- **Fix** (`src/core/utils.py`, `get_process_memory()`): explicitly set
  `.argtypes`/`.restype` on both `GetCurrentProcess` and `GetProcessMemoryInfo`
  using `wintypes.HANDLE`/`wintypes.BOOL`, so ctypes marshals the full pointer
  width correctly.
- **Also fixed the same pattern found nearby**: `MemoryGuard.trim_heap()`
  (`src/core/memory_guard.py`) had the identical missing-argtypes issue on
  `GetCurrentProcess`/`SetProcessWorkingSetSize`, plus a separate bug where it
  reported `trimmed = True` unconditionally without ever checking whether the call
  actually succeeded. Both fixed together. Grepped the whole `src/` tree for every
  remaining `ctypes.windll`/`ctypes.WinDLL` call afterward; the third one
  (`theme_service.py`'s `DwmGetColorizationColor`) was checked and is **not**
  affected -- its arguments are passed via `ctypes.byref()` (safe regardless of
  argtypes) and its return type is a genuinely 32-bit `HRESULT`, so ctypes'
  default `c_int` restype is coincidentally correct there. Confirmed by reading the
  code, not assumed.

#### 5. Single-instance IPC: "payload never received" (`test_single_instance_server_ipc`)
- **Report**: `assert len(received_payloads) == 1` failed with `0 == 1` -- the
  server-side `SingleInstanceServer` never got the message the client sent.
- **Root cause**: `check_single_instance()` called `socket.disconnectFromServer()`
  immediately after `socket.waitForBytesWritten()` returned. Per Qt's own
  documentation, `disconnectFromServer()` only guarantees *the client's own*
  pending write buffer is flushed before closing -- it says nothing about whether
  the *server* has actually read the data yet. Unix domain sockets (Linux/macOS)
  are generally forgiving of an immediate half-close after a completed write
  (already-buffered data survives); Windows named pipes are less forgiving, and an
  immediate disconnect can race ahead of the server's read.
- **Fix**: added an explicit handshake instead of relying on transport-specific
  buffering behavior. `SingleInstanceServer._read_client()` (`ipc_service.py`) now
  writes back `b'OK'` after successfully parsing the message and emitting
  `messageReceived`, then explicitly disconnects. `check_single_instance()` now
  calls `socket.waitForReadyRead(1000)` to wait for that ack before disconnecting,
  instead of disconnecting the instant its own write finished. This makes delivery
  deterministic on every platform, and as a side effect makes the real "bring
  existing instance to front" feature more reliable in general, not just on
  Windows -- previously there was no confirmation the primary instance had
  actually received the request at all.
- **Verified**: confirmed from Qt's own documentation that `disconnectFromServer()`
  never guaranteed peer-side delivery (so the old code's assumption was never
  actually correct, on any platform, even though it happened to work often enough
  on Linux). Could not exercise the real Qt event loop / named pipe in this
  sandbox (no PyQt6, no Windows) -- please confirm this test passes on your next
  Windows run.

### Test suite: the 8 "Linux-only" failures, handled individually

The test-run report suggested `skipif(sys.platform != "linux")` across the board.
Checked each one against its actual code rather than applying that blindly, because
two of them turned out to be a different kind of problem entirely:

| Test | What it actually was | What was done |
|---|---|---|
| `test_get_clean_env` | **Not** Linux-only behavior -- it asserted the *old, buggy* hardcoded `":"` PATH separator that Round 2's `get_clean_env()` fix replaced with `os.pathsep`. Skipping it would have hidden verification of that fix on Windows. | **Fixed**, not skipped: assertion now checks `os.pathsep` |
| `test_dependency_tools_standalone_yt_dlp_url` | Same category: asserted the pre-port assumption that the yt-dlp URL is always the Linux build. | **Fixed**: checks the platform-appropriate URL via `IS_WINDOWS`/`IS_ARM` |
| `test_get_unique_filepath_with_existing_names_and_paths` | Not actually about OS-specific *logic* -- `get_unique_filepath()` reconstructs its result via `os.path.join(dirname, filename)`, which inserts a backslash on Windows even when the input used forward slashes. The hardcoded all-forward-slash expected strings were the problem, not the numbering logic itself. | **Fixed**: both input and expected paths built with `os.path.join` so the test is separator-agnostic |
| `test_show_in_folder_single_nautilus` | Genuinely Linux-only: doesn't mock `platform.system()`, so on real Windows `show_in_folder()` correctly takes its real (already-correct) Windows branch instead of the nautilus path this test checks. | **Skipped** (`sys.platform != "linux"`) |
| `test_user_home_and_downloads_dir_in_snap` | Genuinely Linux-only: Snap confinement (`SNAP_REAL_HOME`/`SNAP_USER_DATA`) has no Windows equivalent at all. | **Skipped** |
| `test_autostart_environment_command_and_file_management` | Mixed: `get_executable_command()`'s AppImage/Flatpak/Snap detection is genuinely cross-platform; only the `.desktop`-file-writing portion is Linux-specific. | **Split** into `test_executable_command_detection` (unconditional) + `test_autostart_desktop_file_management` (skipped on non-Linux) |
| `test_show_in_folder_linux` | **Uncertain** -- unlike its sibling above, this one *does* mock `platform.system()` to force the Linux branch, and `show_in_folder()`'s Windows/Linux check (`utils.py:1172`) correctly reads that mock. Structurally, this should take the same Linux code path on Windows as on Linux. Couldn't find a concrete reason in the code it would still fail. | **Left unskipped** -- please re-run and let me know if it still fails; if so, that's genuinely new information, not something a skip should paper over |
| `test_portal_open_directory_and_single_invocation` | Same situation as above: mocks `platform.system()` and mocks `subprocess.run`/`shutil.which` at the boundary, so the underlying logic should be platform-independent under test. | **Left unskipped**, same reasoning |

Also added `test_autostart_windows_registry` (`test_utils.py`) -- the Windows HKCU
Run-key autostart implementation from Round 2 had zero test coverage until now.

### test-guard review: items addressed

**Must fix:**
1. `test_start_menu_launch_vs_autostart_minimized_flag` -- replaced (it only checked
   Python's `in` operator on hardcoded lists, testing no application code at all)
   with `test_main_window_start_minimized_flag_sets_tray_state_on_init`, which
   verifies `MainWindow`'s actual `__init__`-time handling of `start_minimized`.
2. `test_options_dialog_height_matches_main_window` -- was dead (created a widget,
   resized it, ended -- never even instantiated `OptionsDialog` despite importing
   it). Now actually constructs the dialog and asserts the height match it claims
   to test.
3. `test_memory_guard_collect_garbage`/`trim_heap`/`clean_and_trim` -- collapsed
   into one `test_memory_guard_dialog_lifecycle_triggers_cleanup`, asserting the
   real observable contract of `auto_manage_dialog()`: `WA_DeleteOnClose` gets set,
   and finishing the dialog triggers `clean_and_trim()`.
4. `test_twilight_theme_and_accent` -- dropped the two `ACCENT_COLORS["Twilight"]
   == "#8b5cf6"`-style constant-equals-its-own-literal assertions; kept the real
   palette-application assertions, which prove the same fact functionally anyway.
5. `test_sanitize_media_url` -- the dangling `# 6. Plain URL without tracking`
   comment had no assertion after it at all. Parametrized all 6 cases and added
   the missing one.

**Should fix:**
6. `test_download_worker_format_bytes`/`test_aria2_worker_format_bytes`/
   `test_fetcher_worker_format_bytes` -- parametrized into one
   `test_worker_format_bytes` covering all three worker classes.
7. `test_workers_respect_configured_max_connections` -- renamed to
   `test_extension_max_connections_save_load_and_clamp` (it tests
   `core.utils` config save/load/clamp, not any worker).

**Not changed:** item 8 (`test_version.py` near-duplicates) -- the review itself
flagged this as "acceptable as-is; low priority," so left alone.

---

### Additional verification performed on the claims above

Two specific claims made while diagnosing the test failures were checked directly
rather than left as asserted:

- **"`os.path.join` inserts a backslash on Windows even when the input uses forward
  slashes"** (the basis for the `test_get_unique_filepath_with_existing_names_and_paths`
  fix): confirmed by directly exercising the real `ntpath` module, which is pure
  Python and importable on any OS regardless of what the host actually is. Ran
  `ntpath.join("/tmp/virtual", "testfile (1).txt")` and got
  `'/tmp/virtual\\testfile (1).txt'` -- mixed separators, confirming the original
  hardcoded-forward-slash test really would have failed on Windows, and that the
  fix (building both the input and expected value with `os.path.join`) produces a
  self-consistent result under `ntpath` too.
- **"`shutil.which()` on Windows tries `PATHEXT` extensions against a bare name"**
  (from the original audit, underlying the claim that `find_aria2()`'s PATH
  fallback would work): attempted to verify this the same way, by faking
  `sys.platform = "win32"` and calling the real `shutil.which()`. This failed --
  not because the claim was wrong, but because `shutil.which()`'s Windows branch
  also imports `_winapi`, a C extension that only exists on real Windows, so the
  call raised `AttributeError` in this sandbox regardless of the `sys.platform`
  fake. Pivoted to reading CPython 3.12's actual `shutil.py` source directly
  (`/usr/lib/python3.12/shutil.py`, `which()`, lines ~1521-1602) instead of relying
  on recalled documentation. Confirmed precisely: on `win32`, it builds
  `files = [cmd] + [cmd + ext for ext in pathext]` from `PATHEXT`, reorders so an
  extension-suffixed match is checked before the bare name when `cmd` itself has
  no recognized extension, then returns the first candidate that exists and is
  accessible. For `cmd="aria2c"` this means `aria2c.exe` is found and returned
  before the bare `aria2c` is ever tried -- the original claim was correct, now
  confirmed from source rather than memory.

Everything else that depends on actually running on Windows (the ctypes fixes, the
IPC ack handshake, the two tests left deliberately unskipped above) still can't be
executed from this sandbox -- no Windows OS, no PyQt6 installed, no network access
for the container. Those remain "diagnosed with high confidence, not executed"
rather than "verified," and are flagged as such rather than presented as settled.

---

## Files changed (Round 3 additions)

- `src/core/utils.py` -- `get_process_memory()` ctypes fix
- `src/core/memory_guard.py` -- `trim_heap()` ctypes fix + return-value check
- `src/core/services/ipc_service.py` -- IPC ack handshake
- `tests/test_utils.py`, `tests/test_ui.py`, `tests/test_duplicate_dialog.py`,
  `tests/test_media_downloader.py`, `tests/test_memory_guard.py`,
  `tests/test_table_styles.py`, `tests/test_workers.py` -- see tables above

---

## Files changed

### `src/main.py`
- Added the SIGINT/Ctrl+C handling described above, right after `window = MainWindow()`.

### `src/core/utils.py`
- **`find_aria2()`** — now builds candidate paths using a platform-aware
  `bin_name`/`plat_folder` (`aria2c.exe` / `windows` vs `aria2c` / `linux`) instead of
  hardcoding the Linux names. The system-PATH lookup (`shutil.which`) and the
  `~/.local/bin` convenience-symlink check are adjusted accordingly (the latter is
  skipped entirely on Windows, since it's a Linux-only convenience path).
- **`ensure_aria2()`** — added a Windows branch that downloads from the **official
  `aria2/aria2` GitHub releases** (`release-1.37.0`, verified current via web search
  during this session), not the `abcfy2/aria2-static-build` project used for Linux —
  that project is musl-static-only and does not publish Windows assets. No official
  Windows ARM64 aria2 build exists, so that arch correctly falls through to `None`
  (documented in a code comment) rather than guessing at a URL.
- **`get_clean_env()`** — fixed a real bug: the `PATH` prepend at the end of this
  function used a hardcoded `:` separator, which corrupts `PATH` on Windows (where the
  separator is `;`). Now uses `os.pathsep`.
- **New: `get_subprocess_creationflags()`** — returns `subprocess.CREATE_NO_WINDOW` on
  Windows, `0` elsewhere. Added so every place that launches a console-mode tool
  (aria2c, ffmpeg, yt-dlp, deno) can suppress the console-window flash on Windows.
- **`resolve_filename()`** — now strips the Windows-reserved filename characters
  (`\ / * ? : " < > |`) and trims trailing dots/spaces, on every platform (not just
  Windows), so a given download produces the same filename regardless of OS. This was
  previously only done for the separate media/yt-dlp downloader.
- **`get_data_dir()` / `get_config_dir()` / `get_cache_dir()`** — now default to
  `%LOCALAPPDATA%` (data + cache, cache in its own `Cache` subfolder to avoid colliding
  with data) and `%APPDATA%` (config) on Windows, **only when no `XDG_*_HOME` override
  is already set** — so `tests/conftest.py`'s existing test-isolation mechanism (which
  sets those env vars directly) keeps working unmodified, on every platform.
- **Autostart (`is_autostart_enabled()`, `set_autostart_enabled()`)** — added a full
  Windows implementation using the per-user `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
  registry key via the stdlib `winreg` module (no new dependency). `get_autostart_filepath()`
  itself is untouched — it's still Linux-`.desktop`-specific, and tests monkeypatch it
  directly, so its contract wasn't changed.

### `src/core/media_downloader.py`
- Added `IS_WINDOWS` alongside the existing `IS_ARM` flag.
- `YT_DLP_BIN` and every `DEPENDENCY_TOOLS` entry's `binary_name`/`extract_files` are
  now `.exe`-aware.
- Added verified Windows download URLs for all five tools:
  - **yt-dlp** → `yt-dlp.exe` / `yt-dlp_arm64.exe` (confirmed live from the yt-dlp
    releases page this session — version-agnostic filenames, same pattern the Linux
    entries already rely on)
  - **ffmpeg/ffprobe** → `yt-dlp/FFmpeg-Builds`' `ffmpeg-master-latest-win64-gpl.zip`
    (same publisher as the Linux `.tar.xz` builds already used). No native Windows
    ARM64 build exists from this project, so ARM64 Windows uses the win64 (x86_64)
    build via Windows' built-in x64 emulation — documented in a comment, not silently
    assumed.
  - **deno** → `deno-x86_64-pc-windows-msvc.zip` / `deno-aarch64-pc-windows-msvc.zip`
  - **AtomicParsley** → `AtomicParsleyWindows.zip`, pinned to the *same* release tag
    (`20240608.083822.1ed9031`) already used for the Linux asset, confirmed from the
    project's own README this session
- Added `creationflags=get_subprocess_creationflags()` to all four `subprocess` calls
  in this file (the version-check call and all three tool-invocation `Popen` calls).
- **Fixed a real bug found via actual Windows testing** (see "Bug fixes from real-world
  testing" below): `YtDlpManager.ensure_binary()` used `Path.rename()` to move the
  downloaded temp file into place, which raises `[WinError 183]` on Windows if the
  destination already exists (unlike POSIX `rename()`, which replaces silently).
  Changed to `Path.replace()`, the documented cross-platform-safe equivalent.

### `src/ui/main_window.py`
- Imported `get_subprocess_creationflags` and applied it to the aria2 daemon's
  `subprocess.Popen()` call.
- **`ctx_move()`** ("Move File" context-menu action) — added a `QFileDialog.getSaveFileName`
  fallback for when `choose_portal_save_path()` returns `None` (always the case on
  Windows), matching the fallback pattern already used successfully elsewhere in the
  codebase for the cookies-file pickers.

### `src/ui/dialogs/file_info.py`
- **`browse_save_path()`** ("Save File As") — same `QFileDialog` fallback pattern added.

### `src/ui/dialogs/options.py`
- **`browse_folder()`** (per-category default download folder) — same fallback
  pattern added, using `QFileDialog.getExistingDirectory`.

### `bengal-download-manager.spec`
- `console` and `upx` are now `not _is_windows` instead of hardcoded `True`/`True`.
  Linux/macOS behavior (console visible, UPX-compressed) is unchanged. On Windows,
  this removes the always-visible console window and avoids UPX compression, which is
  a well-documented source of Windows Defender/SmartScreen false positives for
  PyInstaller executables.

### `CMakeLists.txt`
- Added a `WINDOWED_FLAG` variable (`--windowed` on `WIN32`, empty otherwise) and
  applied it to the PyInstaller invocation — replaces the comment that previously just
  noted this was needed without doing it.

### New: `assets/bin/windows/{x86_64,arm64}/` + `README.md`
- Mirrors the existing `assets/bin/linux/<arch>/` convention that `find_aria2()`
  already searches. Left empty with a README explaining the convention — actually
  populating these with a real `aria2c.exe` requires either a real Windows build
  machine or CI, neither of which this sandbox has. If left empty, `ensure_aria2()`'s
  runtime download (above) is the fallback and is sufficient on its own.

---

## What was actually verified (and how)

This sandbox has no Windows OS and no network access, so nothing here was tested by
literally running the app on Windows. What *was* done:

- **Every edited Python file passes `python3 -m py_compile`** (syntax-valid).
- **`utils.py` was actually imported and exercised** on this Linux sandbox, confirming
  the Linux code paths are byte-for-byte unchanged (`get_data_dir()` etc. still return
  the original `~/.local/share/...`-style paths here).
- **The Windows branches were exercised by monkeypatching `platform.system()`** to
  return `"Windows"` (with `PyQt6` stubbed out just enough to import `media_downloader.py`,
  since it's not installed in this sandbox and there's no network to install it):
  - `get_data_dir()`/`get_config_dir()`/`get_cache_dir()` correctly picked up simulated
    `LOCALAPPDATA`/`APPDATA` values and built the expected folder structure (the path
    *separator* shown in this test is `/`, not `\`, because `os.path`'s Windows-vs-POSIX
    behavior is bound to the real OS at interpreter startup, not to what
    `platform.system()` reports — that part genuinely can't be simulated on Linux, only
    the branch/content logic).
  - `resolve_filename()` correctly turned a percent-encoded `Report%3A Q3%3F.pdf` into
    `Report_ Q3_.pdf`.
  - `ensure_aria2()`, with `urllib.request.urlopen` intercepted instead of hitting the
    network, walked all the way through to requesting the **exact expected URL**
    (`.../release-1.37.0/aria2-1.37.0-win-64bit-build1.zip`) before hitting the
    (expected, sandbox-only) network failure.
  - `media_downloader.py`'s `DEPENDENCY_TOOLS` produced the exact expected URL and
    `.exe` binary name for all 5 tools, for both simulated x86_64 and ARM64.
- **A full diff against the original upload** confirms exactly 7 files changed plus 1
  new directory — nothing else was touched, and test artifacts created during the
  verification above (`__pycache__`, some stray directories created by the path
  simulation) were found via that diff and removed before packaging.
- `CMakeLists.txt` was reviewed manually; no `cmake` binary is available in this
  sandbox to syntax-check it automatically.

**Not verified, and can't be from here:** the actual PyInstaller build on Windows, the
real Windows registry autostart behavior, real `QFileDialog` behavior, real console
window suppression, and real aria2/yt-dlp/ffmpeg download-and-run behavior. All of
these depend on a real Windows environment.

---

## What's left to do

1. **Build it on a real Windows machine or Windows CI runner**: `pip install -r requirements.txt`
   then either `pyinstaller bengal-download-manager.spec` or the CMake path. This is
   the step that actually proves the above works, and it cannot happen in this sandbox.
2. **Windows CI** (audit item #9): add a `windows-latest` job to `.github/workflows/`
   — not included here, since it's a separate, larger piece of work (new matrix entry,
   fixing the `build-binary` action's `.exe`-unaware rename step) and this patch set
   focused on the application code itself first, per the audit's suggested order.
3. **Windows test coverage** (audit item #10): the test suite still has zero
   Windows-specific tests and several `/tmp`-hardcoded fixtures that would need
   `tmp_path` instead to run cleanly on Windows CI. Not changed here — editing the test
   suite is a distinct piece of work from the application fixes, happy to do it next.
4. **Release packaging** (audit item #11): an actual installer (Inno Setup/NSIS/WiX)
   or MSIX package, and a decision on code-signing (unsigned EXEs always trigger a
   SmartScreen warning regardless of the UPX fix above).
