"""Ejecutor aislado y seguro de código Python en subprocesos con control de recursos."""

import atexit
import codecs
import os
import queue
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

MAX_OUTPUT_CHARS_DEFAULT = 500_000
MAX_STDERR_CAPTURE_CHARS = 65_536
QUEUE_MAX_ITEMS = 2048

_WINDOWS_ALLOWED_ENV = (
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "PATH",
    "SYSTEMDRIVE",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "TCL_LIBRARY",
    "TK_LIBRARY",
)

_POSIX_ALLOWED_ENV = (
    "PATH",
    "DISPLAY",
    "XAUTHORITY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
)


if sys.platform == "win32":
    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


class _WindowsJobObject:
    """Agrupa un subproceso y todos sus hijos en un Job Object de Windows para evitar huérfanos."""

    _JOB_OBJECT_EXTENDED_LIMIT_INFO = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        try:
            k32 = ctypes.windll.kernel32
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            k32.SetInformationJobObject.restype = wintypes.BOOL
            k32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.LPVOID,
                wintypes.DWORD,
            ]
            k32.AssignProcessToJobObject.restype = wintypes.BOOL
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.TerminateJobObject.restype = wintypes.BOOL
            k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k32.CloseHandle.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [wintypes.HANDLE]

            handle = k32.CreateJobObjectW(None, None)
            if not handle:
                return
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = k32.SetInformationJobObject(
                handle,
                self._JOB_OBJECT_EXTENDED_LIMIT_INFO,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if ok:
                self.handle = handle
            else:
                k32.CloseHandle(handle)
        except Exception:
            self.handle = None

    def assign(self, process: subprocess.Popen) -> bool:
        if not self.handle or sys.platform != "win32":
            return False
        try:
            proc_handle = getattr(process, "_handle", None)
            if not proc_handle:
                return False
            return bool(ctypes.windll.kernel32.AssignProcessToJobObject(self.handle, int(proc_handle)))
        except Exception:
            return False

    def terminate_and_close(self) -> None:
        if not self.handle or sys.platform != "win32":
            return
        handle = self.handle
        self.handle = None
        try:
            ctypes.windll.kernel32.TerminateJobObject(handle, 1)
        except Exception:
            pass
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass


def parse_script_args(raw_args: str) -> list[str]:
    """Divide argumentos de línea de comandos preservando rutas Windows y quitando comillas externas."""
    if not raw_args or not raw_args.strip():
        return []
    if sys.platform == "win32":
        tokens = shlex.split(raw_args, posix=False)
        cleaned: list[str] = []
        for tok in tokens:
            if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"):
                cleaned.append(tok[1:-1])
            else:
                cleaned.append(tok)
        return cleaned
    return shlex.split(raw_args, posix=True)


def build_sanitized_env(sandbox_tmp_dir: str) -> dict[str, str]:
    """Construye un entorno mínimo sin exponer secretos/tokens del host."""
    allowlist = _WINDOWS_ALLOWED_ENV if sys.platform == "win32" else _POSIX_ALLOWED_ENV
    env: dict[str, str] = {}
    for key in allowlist:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    env["TEMP"] = sandbox_tmp_dir
    env["TMP"] = sandbox_tmp_dir
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if sys.platform != "win32":
        env["HOME"] = sandbox_tmp_dir
    return env


def _is_windows_shim_path(path: str) -> bool:
    """Detecta shims de Microsoft Store / Python Install Manager que no son el binario real."""
    if sys.platform != "win32":
        return False
    norm = os.path.normcase(os.path.abspath(path))
    return (
        f"{os.sep}microsoft{os.sep}windowsapps{os.sep}" in norm
        or f"{os.sep}python{os.sep}bin{os.sep}" in norm
    )


def validate_interpreter(executable_path: str) -> tuple[bool, str]:
    """Comprueba que una ruta apunta a un intérprete real de Python y devuelve su versión."""
    if not executable_path:
        return False, "No se especificó ninguna ruta de intérprete."
    abs_path = os.path.abspath(executable_path)
    if not os.path.isfile(abs_path):
        return False, f"El archivo no existe: {abs_path}"

    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    tmp_dir = tempfile.gettempdir()
    env = build_sanitized_env(tmp_dir)
    if sys.platform == "win32" and "LOCALAPPDATA" in os.environ:
        # Evita que shims de pymanager intenten instalar Python en CWD al consultar --version
        env["LOCALAPPDATA"] = os.environ["LOCALAPPDATA"]
    try:
        res = subprocess.run(
            [abs_path, "--version"],
            capture_output=True,
            text=True,
            timeout=4,
            cwd=tmp_dir,
            creationflags=creation_flags,
            env=env,
        )
        out = (res.stdout or res.stderr or "").strip()
        if res.returncode == 0 and out.lower().startswith("python"):
            return True, out
        return False, out or "El ejecutable no respondió como un intérprete de Python válido."
    except Exception as exc:
        return False, str(exc)


def _safe_path_without_cwd() -> str:
    """Devuelve el PATH del sistema excluyendo entradas relativas o el directorio actual."""
    cwd_norm = os.path.normcase(os.path.abspath(os.getcwd()))
    safe_dirs = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or entry in (".", "./", ".\\") or not os.path.isabs(entry):
            continue
        if os.path.normcase(os.path.abspath(entry)) == cwd_norm:
            continue
        if os.path.isdir(entry):
            safe_dirs.append(entry)
    return os.pathsep.join(safe_dirs)


def discover_interpreters() -> list[tuple[str, str]]:
    """Detecta intérpretes Python confiables en el sistema (evitando rutas relativas/CWD y shims)."""
    candidates: list[str] = []

    if not getattr(sys, "frozen", False) and sys.executable:
        candidates.append(os.path.abspath(sys.executable))

    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    safe_path = _safe_path_without_cwd()

    if sys.platform == "win32":
        # Buscar lanzador py.exe (SYSTEMROOT o PATH confiable) para listar instalaciones reales
        sys_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        py_candidates = [os.path.join(sys_root, "py.exe")]
        found_py = shutil.which("py", path=safe_path)
        if found_py:
            py_candidates.append(found_py)

        for py_launcher in py_candidates:
            if not os.path.isfile(py_launcher):
                continue
            try:
                res = subprocess.run(
                    [py_launcher, "-0p"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    cwd=tempfile.gettempdir(),
                    creationflags=creation_flags,
                )
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        parts = line.strip().split()
                        if parts:
                            for idx, token in enumerate(parts):
                                if len(token) >= 2 and token[1] == ":":
                                    cand = " ".join(parts[idx:]).strip()
                                    if os.path.isfile(cand):
                                        candidates.append(os.path.abspath(cand))
                                    break
                break
            except Exception:
                pass

    for name in ("python3", "python"):
        found = shutil.which(name, path=safe_path)
        if found and not _is_windows_shim_path(found):
            candidates.append(os.path.abspath(found))

    seen: set[str] = set()
    valid: list[tuple[str, str]] = []
    for cand in candidates:
        norm = os.path.normcase(cand)
        if norm in seen:
            continue
        seen.add(norm)
        ok, version = validate_interpreter(cand)
        if ok:
            valid.append((cand, version))
    return valid


def resolve_interpreter(preferred_path: str = "") -> tuple[Optional[str], str]:
    """Resuelve el mejor intérprete disponible con validación segura."""
    if preferred_path:
        ok, version = validate_interpreter(preferred_path)
        if ok:
            return os.path.abspath(preferred_path), version

    if not getattr(sys, "frozen", False) and sys.executable:
        ok, version = validate_interpreter(sys.executable)
        if ok:
            return os.path.abspath(sys.executable), version

    discovered = discover_interpreters()
    if discovered:
        return discovered[0]

    return None, "No se encontró Python instalado"


class CodeRunner:
    """Gestiona la ejecución segura, interactiva y acotada de scripts Python."""

    def __init__(self, max_output_chars: int = MAX_OUTPUT_CHARS_DEFAULT):
        self.process: Optional[subprocess.Popen] = None
        self._job: Optional[_WindowsJobObject] = None
        self.output_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX_ITEMS)
        self.max_output_chars = max_output_chars

        self._lock = threading.Lock()
        self._run_counter = 0
        self._active_run_id = 0
        self._stop_reason: Optional[str] = None
        self._output_chars_count = 0
        self._output_truncated = False
        self._stderr_chunks: list[str] = []
        self._stderr_len = 0
        self.current_script_path: Optional[str] = None

        # Directorio temporal privado para toda la sesión
        self._temp_dir_obj = tempfile.TemporaryDirectory(prefix="pypractice_")
        self._session_dir = self._temp_dir_obj.name
        self._workspace_dir = os.path.join(self._session_dir, "workspace")
        self._sandbox_tmp = os.path.join(self._session_dir, "tmp")
        os.makedirs(self._workspace_dir, exist_ok=True)
        os.makedirs(self._sandbox_tmp, exist_ok=True)
        if sys.platform != "win32":
            try:
                os.chmod(self._session_dir, 0o700)
            except OSError:
                pass

        atexit.register(self.cleanup)

    @property
    def is_running(self) -> bool:
        proc = self.process
        return proc is not None and proc.poll() is None

    def _put_event(self, event_type: str, payload, run_id: int, drop_if_full: bool = False) -> None:
        item = (event_type, payload, run_id)
        try:
            if drop_if_full:
                self.output_queue.put_nowait(item)
            else:
                self.output_queue.put(item, timeout=0.5)
        except queue.Full:
            pass

    def _drain_queue(self) -> None:
        while True:
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break

    def run_code(
        self,
        code: str,
        *,
        interpreter_path: str = "",
        script_args: str = "",
        timeout_seconds: int = 60,
        use_isolated_mode: bool = True,
        working_dir: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Escribe el código en un archivo temporal privado y lo ejecuta en un subproceso aislado."""
        self.stop(emit_event=False)
        self._drain_queue()

        python_cmd, version_or_err = resolve_interpreter(interpreter_path)
        if not python_cmd:
            msg = (
                "✖ ERROR: No se encontró un intérprete de Python válido en el sistema.\n"
                f"Detalle: {version_or_err}\n"
                "Puedes seleccionar manualmente la ruta de python.exe en Ejecutar > Seleccionar intérprete..."
            )
            with self._lock:
                self._run_counter += 1
                run_id = self._run_counter
                self._active_run_id = run_id
            self._put_event("stderr", msg + "\n", run_id)
            self._put_event(
                "exit",
                {
                    "returncode": -1,
                    "elapsed": 0.0,
                    "reason": "no_interpreter",
                    "script_path": None,
                    "stderr_full": msg,
                },
                run_id,
            )
            return False, msg

        try:
            parsed_args = parse_script_args(script_args)
        except ValueError as exc:
            err = f"✖ Error en los argumentos de ejecución (sys.argv): {exc}"
            return False, err

        with self._lock:
            self._run_counter += 1
            run_id = self._run_counter
            self._active_run_id = run_id
            self._stop_reason = None
            self._output_chars_count = 0
            self._output_truncated = False
            self._stderr_chunks = []
            self._stderr_len = 0

        script_path = os.path.join(self._session_dir, f"run_{run_id}.py")
        with open(script_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(code)
        self.current_script_path = script_path

        cmd = [python_cmd]
        if use_isolated_mode:
            cmd.append("-I")
        cmd.extend(["-X", "utf8", "-u", script_path])
        cmd.extend(parsed_args)

        cwd = working_dir if (working_dir and os.path.isdir(working_dir)) else self._workspace_dir
        env = build_sanitized_env(self._sandbox_tmp)

        popen_kwargs: dict = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": cwd,
            "env": env,
            "bufsize": 0,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            popen_kwargs["start_new_session"] = True

        start_time = time.monotonic()
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as exc:
            err_msg = f"✖ No se pudo iniciar Python ({python_cmd}): {exc}\n"
            self._put_event("stderr", err_msg, run_id)
            self._put_event(
                "exit",
                {
                    "returncode": -1,
                    "elapsed": 0.0,
                    "reason": "launch_error",
                    "script_path": script_path,
                    "stderr_full": err_msg,
                },
                run_id,
            )
            return False, err_msg

        job = _WindowsJobObject() if sys.platform == "win32" else None
        if job is not None:
            job.assign(proc)

        with self._lock:
            self.process = proc
            self._job = job

        t_out = threading.Thread(
            target=self._read_stream, args=(proc.stdout, "stdout", run_id), daemon=True
        )
        t_err = threading.Thread(
            target=self._read_stream, args=(proc.stderr, "stderr", run_id), daemon=True
        )
        t_out.start()
        t_err.start()

        threading.Thread(
            target=self._monitor_process,
            args=(proc, run_id, start_time, timeout_seconds, script_path, t_out, t_err),
            daemon=True,
        ).start()

        return True, None

    def _terminate_tree(self, proc: Optional[subprocess.Popen], job: Optional[_WindowsJobObject]) -> None:
        if job is not None:
            job.terminate_and_close()

        if proc is None:
            return

        if proc.poll() is None:
            if sys.platform == "win32":
                try:
                    proc.terminate()
                    proc.wait(timeout=0.35)
                except Exception:
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            capture_output=True,
                            timeout=2,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=0.35)
                except Exception:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass

        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except Exception:
                    pass

    def stop(self, reason: str = "stopped", emit_event: bool = True) -> None:
        """Detiene el proceso activo y todo su árbol de procesos hijos."""
        with self._lock:
            proc = self.process
            job = self._job
            if proc is not None and self._stop_reason is None:
                self._stop_reason = reason
            if not emit_event:
                self._active_run_id = -1
            self.process = None
            self._job = None

        self._terminate_tree(proc, job)

    def send_input(self, text: str) -> bool:
        """Envía texto UTF-8 al stdin del proceso activo."""
        proc = self.process
        if proc and proc.poll() is None and proc.stdin:
            try:
                data = (text + "\n").encode("utf-8")
                proc.stdin.write(data)
                proc.stdin.flush()
                return True
            except Exception:
                return False
        return False

    def _read_stream(self, stream, stream_type: str, run_id: int) -> None:
        """Lee la salida por bloques no bloqueantes (soporta input() sin salto de línea y evita DoS)."""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            fd = stream.fileno()
        except Exception:
            return

        while True:
            try:
                raw = os.read(fd, 4096)
            except OSError:
                break
            if not raw:
                break

            chunk = decoder.decode(raw, final=False)
            if not chunk:
                continue

            should_stop_limit = False
            with self._lock:
                if run_id != self._active_run_id:
                    break

                if stream_type == "stderr" and self._stderr_len < MAX_STDERR_CAPTURE_CHARS:
                    take = chunk[: MAX_STDERR_CAPTURE_CHARS - self._stderr_len]
                    self._stderr_chunks.append(take)
                    self._stderr_len += len(take)

                if self._output_truncated:
                    continue

                remaining = self.max_output_chars - self._output_chars_count
                if len(chunk) > remaining:
                    chunk = chunk[: max(0, remaining)]
                    self._output_chars_count = self.max_output_chars
                    self._output_truncated = True
                    self._stop_reason = "output_limit"
                    should_stop_limit = True
                else:
                    self._output_chars_count += len(chunk)

            if chunk:
                self._put_event(stream_type, chunk, run_id, drop_if_full=True)

            if should_stop_limit:
                self._put_event(
                    "system",
                    f"\n⚠ [Salida truncada: se superó el límite de seguridad de {self.max_output_chars:,} caracteres. Proceso detenido.]\n",
                    run_id,
                )
                self.stop(reason="output_limit", emit_event=True)
                break

        tail = decoder.decode(b"", final=True)
        if tail and not self._output_truncated and run_id == self._active_run_id:
            self._put_event(stream_type, tail, run_id, drop_if_full=True)

    def _monitor_process(
        self,
        proc: subprocess.Popen,
        run_id: int,
        start_time: float,
        timeout_seconds: int,
        script_path: str,
        t_out: threading.Thread,
        t_err: threading.Thread,
    ) -> None:
        timed_out = False
        try:
            if timeout_seconds and timeout_seconds > 0:
                proc.wait(timeout=timeout_seconds)
            else:
                proc.wait()
        except subprocess.TimeoutExpired:
            timed_out = True
            self.stop(reason="timeout", emit_event=True)
        except Exception:
            pass

        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)

        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except Exception:
                    pass

        elapsed = max(0.0, time.monotonic() - start_time)
        with self._lock:
            reason = self._stop_reason or ("timeout" if timed_out else "finished")
            stderr_full = "".join(self._stderr_chunks)
            is_current = run_id == self._active_run_id
            if self.process is proc:
                self.process = None
            job = self._job
            if self.process is None:
                self._job = None

        if job is not None:
            job.terminate_and_close()

        # Limpiar el script temporal ejecutado
        try:
            if os.path.isfile(script_path):
                os.remove(script_path)
        except OSError:
            pass

        if is_current:
            self._put_event(
                "exit",
                {
                    "returncode": None if timed_out else proc.returncode,
                    "elapsed": elapsed,
                    "reason": reason,
                    "timeout": timeout_seconds,
                    "script_path": script_path,
                    "stderr_full": stderr_full,
                },
                run_id,
            )

    def cleanup(self) -> None:
        """Detiene cualquier subproceso y borra el directorio temporal de sesión."""
        try:
            self.stop(emit_event=False)
        except Exception:
            pass
        try:
            self._temp_dir_obj.cleanup()
        except Exception:
            pass
