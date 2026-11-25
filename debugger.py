"""Depurador visual paso a paso basado en bdb (biblioteca estándar) en subproceso aislado."""

import atexit
import codecs
import json
import os
import queue
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

from runner import (
    MAX_OUTPUT_CHARS_DEFAULT,
    QUEUE_MAX_ITEMS,
    _WindowsJobObject,
    build_sanitized_env,
    parse_script_args,
    resolve_interpreter,
)

DBG_PREFIX = "\x1f__PYPRACTICE_DBG__:"
DBG_CMD_PREFIX = "__DBG_CMD__:"
DBG_INPUT_PREFIX = "__DBG_INPUT__:"
MAX_DBG_FRAME_CHARS = 262_144

_DEBUG_DRIVER_CODE = r'''
import bdb
import builtins
import json
import os
import sys
import traceback

DBG_PREFIX = "\x1f__PYPRACTICE_DBG__:"
DBG_CMD_PREFIX = "__DBG_CMD__:"
DBG_INPUT_PREFIX = "__DBG_INPUT__:"

_real_stdout = sys.stdout
_real_stdin = sys.stdin


def _emit(payload):
    line = DBG_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n"
    _real_stdout.write(line)
    _real_stdout.flush()


def _safe_repr(val, max_len=120):
    try:
        r = repr(val)
    except Exception:
        r = f"<{type(val).__name__}>"
    if len(r) > max_len:
        r = r[: max_len - 1] + "…"
    return r


def _serialize_scope(mapping, scope_name):
    items = []
    for k, v in mapping.items():
        if k.startswith("__") and k.endswith("__"):
            continue
        tname = type(v).__name__
        if tname == "module":
            continue
        items.append({"name": str(k), "type": tname, "value": _safe_repr(v), "scope": scope_name})
    items.sort(key=lambda x: x["name"].lower())
    return items


class _EducationalDebugger(bdb.Bdb):
    def __init__(self, target_file, breakpoints):
        super().__init__()
        self.target_file = self.canonic(target_file)
        self.user_breakpoints = set(breakpoints)
        self.mode = "step"  # "step" | "next" | "continue"
        self.next_frame = None
        self.stopped = False

    def _is_user_frame(self, frame):
        return self.canonic(frame.f_code.co_filename) == self.target_file

    def _build_stack(self, frame):
        stack = []
        cur = frame
        while cur is not None:
            if self._is_user_frame(cur):
                fn = cur.f_code.co_name
                if fn == "<module>":
                    fn = "programa principal"
                stack.append({"function": fn, "line": cur.f_lineno})
            cur = cur.f_back
        return stack

    def user_line(self, frame):
        if self.stopped:
            self.set_quit()
            return
        if not self._is_user_frame(frame):
            return

        line = frame.f_lineno
        if self.mode == "continue" and line not in self.user_breakpoints:
            return
        if self.mode == "next" and self.next_frame is not None and frame is not self.next_frame:
            # Si estamos dentro de una función llamada desde next_frame, continuar salvo breakpoint
            cur = frame.f_back
            inside_call = False
            while cur is not None:
                if cur is self.next_frame:
                    inside_call = True
                    break
                cur = cur.f_back
            if inside_call and line not in self.user_breakpoints:
                return

        fn_name = frame.f_code.co_name
        if fn_name == "<module>":
            fn_name = "programa principal"

        loc_items = _serialize_scope(frame.f_locals, "local")
        glob_items = []
        if frame.f_locals is not frame.f_globals:
            local_names = {x["name"] for x in loc_items}
            glob_items = [x for x in _serialize_scope(frame.f_globals, "global") if x["name"] not in local_names]

        _emit({
            "event": "paused",
            "line": line,
            "function": fn_name,
            "variables": loc_items + glob_items,
            "stack": self._build_stack(frame),
        })

        while True:
            raw = _real_stdin.readline()
            if not raw:
                self.stopped = True
                self.set_quit()
                return
            cmd_line = raw.rstrip("\r\n")
            if cmd_line.startswith(DBG_CMD_PREFIX):
                cmd = cmd_line[len(DBG_CMD_PREFIX):].strip()
                if cmd == "step":
                    self.mode = "step"
                    self.next_frame = None
                    self.set_step()
                    return
                elif cmd == "next":
                    self.mode = "next"
                    self.next_frame = frame
                    self.set_step()
                    return
                elif cmd == "continue":
                    self.mode = "continue"
                    self.next_frame = None
                    self.set_step()
                    return
                elif cmd == "stop":
                    self.stopped = True
                    self.set_quit()
                    return

    def user_exception(self, frame, exc_info):
        if self._is_user_frame(frame):
            exc_type, exc_val, _tb = exc_info
            _emit({
                "event": "exception",
                "line": frame.f_lineno,
                "type": exc_type.__name__,
                "message": str(exc_val),
            })


def _debug_input(prompt=""):
    if prompt:
        _real_stdout.write(str(prompt))
        _real_stdout.flush()
    _emit({"event": "input_wait"})
    while True:
        raw = _real_stdin.readline()
        if not raw:
            raise EOFError("Entrada cerrada durante la depuración.")
        line = raw.rstrip("\r\n")
        if line.startswith(DBG_INPUT_PREFIX):
            return line[len(DBG_INPUT_PREFIX):]
        if line == DBG_CMD_PREFIX + "stop":
            raise KeyboardInterrupt()


def main():
    target_script = sys.argv[1]
    bp_json = sys.argv[2]
    user_args = sys.argv[3:]
    breakpoints = set(json.loads(bp_json)) if bp_json else set()

    with open(target_script, "r", encoding="utf-8") as fh:
        source = fh.read()

    sys.argv = [target_script] + user_args
    builtins.input = _debug_input

    dbg = _EducationalDebugger(target_script, breakpoints)
    user_globals = {
        "__name__": "__main__",
        "__file__": target_script,
        "__builtins__": builtins,
    }

    try:
        code_obj = compile(source, target_script, "exec")
        dbg.run(code_obj, user_globals, user_globals)
    except bdb.BdbQuit:
        pass
    except SystemExit:
        pass
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
'''


class StepDebugger:
    """Controlador del depurador paso a paso en un subproceso seguro e independiente."""

    def __init__(self, max_output_chars: int = MAX_OUTPUT_CHARS_DEFAULT):
        self.process: Optional[subprocess.Popen] = None
        self._job: Optional[_WindowsJobObject] = None
        self.event_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX_ITEMS)
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
        self.waiting_for_input: bool = False
        self.paused: bool = False

        # Prefijo único por sesión para impedir inyección de tramas falsas desde stdout del script
        self._dbg_prefix = f"\x1f__PYPRACTICE_DBG_{secrets.token_hex(8)}__:"

        self._temp_dir_obj = tempfile.TemporaryDirectory(prefix="pypractice_dbg_")
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

        self._driver_path = os.path.join(self._session_dir, "_driver.py")
        driver_source = _DEBUG_DRIVER_CODE.replace(
            'DBG_PREFIX = "\\x1f__PYPRACTICE_DBG__:"',
            f"DBG_PREFIX = {self._dbg_prefix!r}",
            1,
        )
        with open(self._driver_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(driver_source)

        atexit.register(self.cleanup)

    @property
    def is_running(self) -> bool:
        proc = self.process
        return proc is not None and proc.poll() is None

    def _put(self, ev_type: str, payload, run_id: int) -> None:
        try:
            self.event_queue.put((ev_type, payload, run_id), timeout=0.5)
        except queue.Full:
            pass

    def _drain_queue(self) -> None:
        while True:
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break

    def start(
        self,
        code: str,
        *,
        breakpoints: Optional[set[int]] = None,
        interpreter_path: str = "",
        script_args: str = "",
        use_isolated_mode: bool = True,
    ) -> tuple[bool, Optional[str]]:
        self.stop(emit_event=False)
        self._drain_queue()

        python_cmd, err = resolve_interpreter(interpreter_path)
        if not python_cmd:
            return False, f"✖ No se encontró Python para depurar: {err}"

        try:
            parsed_args = parse_script_args(script_args)
        except ValueError as exc:
            return False, f"✖ Error en los argumentos (sys.argv): {exc}"

        with self._lock:
            self._run_counter += 1
            run_id = self._run_counter
            self._active_run_id = run_id
            self._stop_reason = None
            self._output_chars_count = 0
            self._output_truncated = False
            self._stderr_chunks = []
            self._stderr_len = 0
            self.waiting_for_input = False
            self.paused = False

        script_path = os.path.join(self._session_dir, f"debug_{run_id}.py")
        with open(script_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(code)
        self.current_script_path = script_path

        bp_json = json.dumps(sorted(breakpoints or ()))
        cmd = [python_cmd]
        if use_isolated_mode:
            cmd.append("-I")
        cmd.extend(["-X", "utf8", "-u", self._driver_path, script_path, bp_json])
        cmd.extend(parsed_args)

        popen_kwargs: dict = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": self._workspace_dir,
            "env": build_sanitized_env(self._sandbox_tmp),
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
            return False, f"✖ No se pudo iniciar el depurador: {exc}"

        job = _WindowsJobObject() if sys.platform == "win32" else None
        if job is not None:
            job.assign(proc)

        with self._lock:
            self.process = proc
            self._job = job

        t_out = threading.Thread(target=self._read_stdout, args=(proc.stdout, run_id), daemon=True)
        t_err = threading.Thread(target=self._read_stderr, args=(proc.stderr, run_id), daemon=True)
        t_out.start()
        t_err.start()

        threading.Thread(
            target=self._monitor,
            args=(proc, run_id, start_time, script_path, t_out, t_err),
            daemon=True,
        ).start()

        return True, None

    def send_command(self, cmd: str) -> bool:
        """Envía 'next', 'step', 'continue' o 'stop' al depurador pausado."""
        proc = self.process
        if proc and proc.poll() is None and proc.stdin:
            try:
                self.paused = False
                proc.stdin.write(f"{DBG_CMD_PREFIX}{cmd}\n".encode("utf-8"))
                proc.stdin.flush()
                return True
            except Exception:
                return False
        return False

    def send_input(self, text: str) -> bool:
        """Envía entrada de usuario para input() durante la depuración."""
        proc = self.process
        if proc and proc.poll() is None and proc.stdin:
            try:
                self.waiting_for_input = False
                proc.stdin.write(f"{DBG_INPUT_PREFIX}{text}\n".encode("utf-8"))
                proc.stdin.flush()
                return True
            except Exception:
                return False
        return False

    def stop(self, reason: str = "stopped", emit_event: bool = True) -> None:
        with self._lock:
            proc = self.process
            job = self._job
            if proc is not None and self._stop_reason is None:
                self._stop_reason = reason
            if not emit_event:
                self._active_run_id = -1
            self.process = None
            self._job = None
            self.paused = False
            self.waiting_for_input = False

        if job is not None:
            job.terminate_and_close()
        if proc is not None:
            if proc.poll() is None:
                if sys.platform == "win32":
                    try:
                        proc.terminate()
                        proc.wait(timeout=0.3)
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
                        proc.wait(timeout=0.3)
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

    def _emit_user_stream(self, stream_type: str, chunk: str, run_id: int) -> bool:
        """Emite salida de usuario respetando max_output_chars. Devuelve False si se superó el límite."""
        if not chunk:
            return True
        should_stop_limit = False
        with self._lock:
            if run_id != self._active_run_id:
                return False
            if self._output_truncated:
                return False
            remaining = self.max_output_chars - self._output_chars_count
            if len(chunk) > remaining:
                chunk = chunk[: max(0, remaining)]
                self._output_chars_count = self.max_output_chars
                self._output_truncated = True
                if self._stop_reason is None:
                    self._stop_reason = "output_limit"
                should_stop_limit = True
            else:
                self._output_chars_count += len(chunk)

        if chunk:
            self._put(stream_type, chunk, run_id)

        if should_stop_limit:
            self._put(
                "system",
                f"\n⚠ [Salida truncada en depuración: se superó el límite de seguridad de {self.max_output_chars:,} caracteres. Proceso detenido.]\n",
                run_id,
            )
            self.stop(reason="output_limit", emit_event=True)
            return False
        return True

    def _read_stdout(self, stream, run_id: int) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            fd = stream.fileno()
        except Exception:
            return

        prefix = self._dbg_prefix
        buf = ""
        stop_reading = False
        while not stop_reading:
            try:
                raw = os.read(fd, 4096)
            except OSError:
                break
            if not raw:
                break
            buf += decoder.decode(raw, final=False)

            while True:
                idx = buf.find(prefix)
                if idx == -1:
                    # Emitir texto normal inmediatamente si no empieza un posible prefijo parcial
                    keep = 0
                    for k in range(1, len(prefix)):
                        if buf.endswith(prefix[:k]):
                            keep = k
                            break
                    emit_part = buf[: len(buf) - keep] if keep else buf
                    if emit_part and not self._emit_user_stream("stdout", emit_part, run_id):
                        stop_reading = True
                        buf = ""
                        break
                    buf = buf[len(buf) - keep :] if keep else ""
                    break

                if idx > 0:
                    if not self._emit_user_stream("stdout", buf[:idx], run_id):
                        stop_reading = True
                        buf = ""
                        break
                    buf = buf[idx:]

                nl = buf.find("\n")
                if nl == -1:
                    if len(buf) > MAX_DBG_FRAME_CHARS:
                        self.stop(reason="output_limit", emit_event=True)
                        stop_reading = True
                        buf = ""
                    break
                frame_str = buf[len(prefix) : nl]
                buf = buf[nl + 1 :]
                try:
                    ev = json.loads(frame_str)
                    if ev.get("event") == "paused":
                        self.paused = True
                    elif ev.get("event") == "input_wait":
                        self.waiting_for_input = True
                    self._put("debug", ev, run_id)
                except json.JSONDecodeError:
                    pass

        if not stop_reading:
            tail = buf + decoder.decode(b"", final=True)
            if tail:
                self._emit_user_stream("stdout", tail, run_id)

    def _read_stderr(self, stream, run_id: int) -> None:
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
            if chunk:
                with self._lock:
                    if self._stderr_len < 65_536:
                        take = chunk[: 65_536 - self._stderr_len]
                        self._stderr_chunks.append(take)
                        self._stderr_len += len(take)
                if not self._emit_user_stream("stderr", chunk, run_id):
                    break

    def _monitor(
        self,
        proc: subprocess.Popen,
        run_id: int,
        start_time: float,
        script_path: str,
        t_out: threading.Thread,
        t_err: threading.Thread,
    ) -> None:
        try:
            proc.wait()
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
            reason = self._stop_reason or "finished"
            stderr_full = "".join(self._stderr_chunks)
            is_current = run_id == self._active_run_id
            if self.process is proc:
                self.process = None
            job = self._job
            if self.process is None:
                self._job = None
            self.paused = False
            self.waiting_for_input = False

        if job is not None:
            job.terminate_and_close()
        try:
            if os.path.isfile(script_path):
                os.remove(script_path)
        except OSError:
            pass

        if is_current:
            self._put(
                "exit",
                {
                    "returncode": proc.returncode,
                    "elapsed": elapsed,
                    "reason": reason,
                    "script_path": script_path,
                    "stderr_full": stderr_full,
                },
                run_id,
            )

    def cleanup(self) -> None:
        try:
            self.stop(emit_event=False)
        except Exception:
            pass
        try:
            self._temp_dir_obj.cleanup()
        except Exception:
            pass
