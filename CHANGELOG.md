# Changelog

All notable changes to **PyPractice** are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-11-25

### Fixed
- Guarded the X11 `<ISO_Left_Tab>` key binding in `editor.py` against `tk.TclError` so PyPractice starts cleanly on Windows Python 3.10 (Tcl/Tk < 8.6.13) while preserving `Shift+Tab` unindentation across Windows, macOS, and Linux.

## [0.2.0] - 2026-11-25

### Added
- Multi-tab Python desktop editor built 100% on the Python Standard Library and Tkinter, with `tokenize`-based lexical highlighting, synchronized line gutter, breakpoints (`F9`), bracket matching, auto-indent, comment toggling (`Ctrl+/`), and Find/Replace (`Ctrl+F` / `Ctrl+H`).
- Isolated subprocess execution engine (`runner.py`) with allowlisted environment sanitization, Python isolated mode (`-I`), private per-session temporary working directories, configurable timeouts, output-flood circuit breakers, and process-tree teardown (Windows Job Objects and POSIX process groups).
- Visual step-by-step debugger (`debugger.py`) based on `bdb` with randomized per-session protocol framing (`secrets.token_hex`), live local/global variable inspector, call stack view, breakpoints, and `input()` bridge.
- Preventive AST Safe Mode (`safety.py`) warning on sensitive modules, dynamic execution/reflection primitives, and external-path or write-mode file operations.
- Educational Spanish diagnostics (`diagnostics.py`) for syntax errors and runtime tracebacks with temporary path sanitization and one-click jump-to-line.
- Progressive exercise library (`exercises.py`) with 10 challenges across 5 difficulty levels, hints, progress persistence, and isolated automated grading (`function` and `stdio` modes).
- Starter templates, searchable Python cheatsheet (`F2`), and Markdown/plain-text practice export (`Ctrl+E`).
- Four accessibility themes verified by automated WCAG AA/AAA contrast tests: Dark, Light, High Contrast (AAA), and Color-Blind Safe (Okabe-Ito), plus zoom controls, font picker, and optional audio feedback.
- Persistent settings and automatic crash-recovery snapshots (`settings.py`), Windows PyInstaller packaging specification (`PyPractice.spec`, `build_windows.bat`), `SECURITY.md`, MIT `LICENSE`, automated `unittest` suite, and GitHub Actions CI.

### Fixed
- Guarded `ctypes.wintypes` and Win32 Job Object structures behind `sys.platform == "win32"` in `runner.py` for full POSIX (Linux/macOS) compatibility.
- Added `-X utf8` to isolated subprocess launches (`runner.py`, `debugger.py`, `exercises.py`) so UTF-8 I/O works reliably even under Python isolated mode (`-I`) on Windows.
- Normalized `PanedWindow.panes()` IDs to strings in `main.py` so toggling the Exercises and Variable Inspector panels cleanly hides them across modern Tcl/Tk versions, and widened the Exercises panel to prevent button clipping.
- Captured Tkinter `isolated_mode_var` on the main thread before launching the background exercise-evaluation worker in `main.py` to prevent cross-thread Tcl/Tk `RuntimeError`.
- Isolated `PyPracticeApp` instantiation in automated tests via optional `SettingsManager` injection to avoid reading or modifying the host user's configuration directory.
- Scoped PyInstaller `datas` in `PyPractice.spec` strictly to `assets/pypractice.ico` so README screenshots are not bundled into Windows builds.
