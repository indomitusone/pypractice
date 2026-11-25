# Security Policy & Threat Model

PyPractice is developed under the defensive engineering principles of **[Indomitus One](https://indomitusone.com/)**: minimal attack surface, zero runtime third-party dependencies, explicit trust boundaries, and transparent documentation of what security controls do—and do not—guarantee.

---

## Threat Model & Explicit Boundaries

PyPractice is a **local educational desktop workbench**, not an OS-level containment sandbox. Like IDLE, Thonny, or VS Code, any Python code executed by the user runs using the local Python interpreter with the **same OS privileges as the logged-in user**.

### What PyPractice Mitigates

| Control | Implementation | Purpose |
| :--- | :--- | :--- |
| **Environment sanitization** | `build_sanitized_env()` in `runner.py` | Prevents child scripts from inheriting host secrets, tokens, or API keys stored in environment variables. |
| **Isolated interpreter & CWD** | `python -I`, private `tempfile.TemporaryDirectory()` (`0700` on POSIX), safe `PATH` resolution | Prevents accidental writes to the installation folder, `PYTHONPATH` hijacking, or relative-CWD interpreter spoofing. |
| **DoS & orphan-process protection** | Configurable timeouts, bounded output queues, output-flood circuit breakers, Windows Job Objects (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`), and POSIX process groups (`os.killpg`) | Stops infinite loops, stdout floods, and spawned background child processes across the runner, debugger, and exercise evaluator. |
| **Debugger protocol integrity** | Per-session randomized `secrets.token_hex()` frame prefix & max frame cap in `debugger.py` | Prevents user `stdout` from spoofing internal debugger events or exhausting UI memory. |
| **Preventive AST Safe Mode** | `inspect_code_safety()` in `safety.py` | Warns and requires confirmation before running sensitive imports/calls (`subprocess`, `os`, `ctypes`, `winreg`, `shutil`, `socket`, `pickle`, `runpy`, `eval`, `exec`, dynamic reflection/dunders, external-path or write-mode `open()`). |

### What Is Explicitly Out of Scope

- **Not a malware sandbox**: Static AST analysis can always be bypassed by intentionally obfuscated Python code, and Safe Mode is advisory (the user can choose to proceed or disable it).
- **No filesystem or network isolation**: Executed code runs with local OS user permissions and can read user-accessible files or initiate network connections if executed.
- **Not designed for untrusted remote code execution**: Never wrap or expose PyPractice as a multi-tenant web service or remote code runner.

**Rule of thumb:** Only run or debug Python code that you wrote yourself or trust.

---

## Reporting a Vulnerability

If you discover a security issue in PyPractice itself (for example, command injection in interpreter discovery, path traversal in session/recovery handling, or privilege/process-lifecycle bugs), please report it responsibly:

- **Email:** [contacto@indomitusone.com](mailto:contacto@indomitusone.com)
- **GitHub:** Open a private Security Advisory on this repository if enabled.

Please include reproduction steps, affected OS/Python version, and impact details. Reports are handled directly with technical rigor and confidentiality.
