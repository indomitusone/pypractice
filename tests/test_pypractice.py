"""Pruebas automatizadas para PyPractice v0.2.1 (seguridad, diagnósticos, temas, runner, editor y UI)."""

import gc
import os
import queue
import sys
import tempfile
import time
import tkinter as tk
from tkinter import font as tkfont
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from debugger import StepDebugger
from diagnostics import analyze_runtime_stderr, check_syntax_detailed, sanitize_traceback_text
from editor import CodeEditor
from exercises import EXERCISES, evaluate_exercise, get_exercise_by_id
from main import APP_VERSION, PyPracticeApp
from runner import CodeRunner, build_sanitized_env, discover_interpreters, validate_interpreter
from safety import inspect_code_safety
from settings import SettingsManager
from templates import CHEATSHEET_SECTIONS, CODE_TEMPLATES, format_practice_export
from themes import THEMES, configure_ttk_styles, get_theme


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast_ratio(fg: str, bg: str) -> float:
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


class TestThemesWCAG(unittest.TestCase):
    def test_all_themes_meet_wcag_aa_for_key_controls(self):
        for name, t in THEMES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(_contrast_ratio(t["btn_run_fg"], t["btn_run_bg"]), 4.5)
                self.assertGreaterEqual(_contrast_ratio(t["btn_stop_fg"], t["btn_stop_bg"]), 4.5)
                self.assertGreaterEqual(_contrast_ratio(t["btn_debug_fg"], t["btn_debug_bg"]), 4.5)
                self.assertGreaterEqual(_contrast_ratio(t["editor_fg"], t["editor_bg"]), 7.0)
                self.assertGreaterEqual(_contrast_ratio(t["muted_fg"], t["panel_bg"]), 4.5)
                self.assertGreaterEqual(_contrast_ratio(t["gutter_fg"], t["gutter_bg"]), 4.5)
                self.assertGreaterEqual(_contrast_ratio(t["console_fg"], t["console_bg"]), 7.0)

    def test_high_contrast_meets_wcag_aaa(self):
        hc = get_theme("alto_contraste")
        self.assertGreaterEqual(_contrast_ratio(hc["editor_fg"], hc["editor_bg"]), 15.0)
        self.assertGreaterEqual(_contrast_ratio(hc["gutter_fg"], hc["gutter_bg"]), 15.0)


class TestSafetyAST(unittest.TestCase):
    def test_detects_dangerous_operations(self):
        code = (
            "import os\n"
            "import shutil as sh\n"
            "from subprocess import run\n"
            "sh.rmtree('carpeta')\n"
            "eval('1+1')\n"
            "open('salida.txt', 'w')\n"
        )
        warnings = inspect_code_safety(code)
        symbols = {w.symbol for w in warnings}
        self.assertIn("shutil.rmtree()", symbols)
        self.assertIn("eval()", symbols)
        self.assertIn('open(..., "w")', symbols)
        self.assertTrue(any(w.severity == "alto" for w in warnings))

    def test_detects_obfuscation_reflection_and_external_paths(self):
        code = (
            "import runpy\n"
            "import pickle\n"
            "m = 'w'\n"
            "open('nota.txt', m)\n"
            "open('../../etc/passwd', 'r')\n"
            "getattr(__builtins__, 'ev' + 'al')('1+1')\n"
            "().__class__.__base__.__subclasses__()\n"
            "from pathlib import Path\n"
            "Path('x.txt').open('w')\n"
        )
        warnings = inspect_code_safety(code)
        symbols = {w.symbol for w in warnings}
        self.assertIn("import runpy", symbols)
        self.assertIn("import pickle", symbols)
        self.assertIn("open(..., <modo dinámico>)", symbols)
        self.assertIn("open(<ruta externa>)", symbols)
        self.assertIn("__builtins__", symbols)
        self.assertIn(".__subclasses__", symbols)
        self.assertIn('.open(..., "w")', symbols)

    def test_safe_educational_code_has_no_warnings(self):
        code = "numeros = [1, 2, 3]\nprint(sum(numeros))\n"
        self.assertEqual(inspect_code_safety(code), [])


class TestDiagnostics(unittest.TestCase):
    def test_syntax_and_indentation_errors(self):
        diag = check_syntax_detailed("if True\n    print('hola')\n")
        self.assertIsNotNone(diag)
        self.assertEqual(diag.line, 1)
        self.assertIn(":", diag.explanation)

        diag_ind = check_syntax_detailed("def f():\nprint('sin sangria')\n")
        self.assertIsNotNone(diag_ind)
        self.assertEqual(diag_ind.line, 2)

    def test_runtime_traceback_sanitization_and_translation(self):
        fake_path = r"C:\Users\Demo\AppData\Local\Temp\pypractice_abc\run_1.py"
        stderr = (
            "Traceback (most recent call last):\n"
            f'  File "{fake_path}", line 3, in <module>\n'
            "    print(variable_inexistente)\n"
            "NameError: name 'variable_inexistente' is not defined\n"
        )
        cleaned = sanitize_traceback_text(stderr, fake_path)
        self.assertNotIn("pypractice_abc", cleaned)
        self.assertIn('File "Tu código"', cleaned)

        diag = analyze_runtime_stderr(stderr, fake_path)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.line, 3)
        self.assertIn("variable_inexistente", diag.explanation)


class TestSettingsAndRecovery(unittest.TestCase):
    def test_settings_persistence_and_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            sm = SettingsManager(base_dir=Path(tmp))
            sm.set("theme", "claro")
            sm.add_recent_file(__file__)
            sm.save_recovery_session([{"filepath": None, "content": "print(123)", "dirty": True}])

            sm2 = SettingsManager(base_dir=Path(tmp))
            self.assertEqual(sm2.get("theme"), "claro")
            self.assertEqual(len(sm2.get("recent_files")), 1)
            recovered = sm2.load_recovery_session()
            self.assertEqual(recovered[0]["content"], "print(123)")
            sm2.clear_recovery_session()
            self.assertEqual(sm2.load_recovery_session(), [])


class TestSecureRunner(unittest.TestCase):
    def _collect_until_exit(self, runner: CodeRunner, max_wait: float = 8.0):
        stdout_parts = []
        stderr_parts = []
        system_parts = []
        exit_info = None
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            try:
                ev, payload, _rid = runner.output_queue.get(timeout=0.05)
            except queue.Empty:
                if exit_info is not None:
                    break
                continue
            if ev == "stdout":
                stdout_parts.append(payload)
            elif ev == "stderr":
                stderr_parts.append(payload)
            elif ev == "system":
                system_parts.append(payload)
            elif ev == "exit":
                exit_info = payload
                break
        return "".join(stdout_parts), "".join(stderr_parts), "".join(system_parts), exit_info

    def test_env_sanitization_and_cwd_isolation(self):
        os.environ["PYPRACTICE_FAKE_SECRET_TOKEN"] = "super_secret_123"
        try:
            runner = CodeRunner()
            try:
                code = (
                    "import os, sys\n"
                    "print('SECRET_PRESENT=', 'PYPRACTICE_FAKE_SECRET_TOKEN' in os.environ)\n"
                    "print('CWD=', os.getcwd())\n"
                    "print('ARGS=', sys.argv[1:])\n"
                    "print('UTF8=Lucía — año 2026')\n"
                )
                ok, err = runner.run_code(code, script_args='hola "mundo 2"', timeout_seconds=10)
                self.assertTrue(ok, err)
                out, err_s, _sys, info = self._collect_until_exit(runner)
                self.assertIsNotNone(info)
                self.assertEqual(info["returncode"], 0)
                self.assertIn("SECRET_PRESENT= False", out)
                self.assertIn("pypractice_", out)
                self.assertIn("ARGS= ['hola', 'mundo 2']", out)
                self.assertIn("UTF8=Lucía — año 2026", out)
                self.assertEqual(err_s, "")
            finally:
                session_dir = runner._session_dir
                runner.cleanup()
                self.assertFalse(os.path.exists(session_dir))
        finally:
            os.environ.pop("PYPRACTICE_FAKE_SECRET_TOKEN", None)

    def test_interactive_input(self):
        runner = CodeRunner()
        try:
            code = "nombre = input('Nombre: ')\nprint('Hola', nombre)\n"
            ok, err = runner.run_code(code, timeout_seconds=10)
            self.assertTrue(ok, err)

            # Esperar a que llegue el prompt sin salto de línea
            prompt = ""
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and "Nombre:" not in prompt:
                try:
                    ev, payload, _ = runner.output_queue.get(timeout=0.05)
                    if ev == "stdout":
                        prompt += payload
                except queue.Empty:
                    pass
            self.assertIn("Nombre:", prompt)

            self.assertTrue(runner.send_input("Ada"))
            out, _err, _sys, info = self._collect_until_exit(runner)
            self.assertEqual(info["returncode"], 0)
            self.assertIn("Hola Ada", out)
        finally:
            runner.cleanup()

    def test_timeout_enforced(self):
        runner = CodeRunner()
        try:
            ok, err = runner.run_code("import time\ntime.sleep(30)\n", timeout_seconds=1)
            self.assertTrue(ok, err)
            _out, _err, _sys, info = self._collect_until_exit(runner, max_wait=6.0)
            self.assertIsNotNone(info)
            self.assertEqual(info["reason"], "timeout")
            self.assertFalse(runner.is_running)
        finally:
            runner.cleanup()

    def test_output_flood_circuit_breaker(self):
        runner = CodeRunner(max_output_chars=5000)
        try:
            ok, err = runner.run_code("while True:\n    print('A' * 500)\n", timeout_seconds=10)
            self.assertTrue(ok, err)
            out, _err, sys_msg, info = self._collect_until_exit(runner, max_wait=6.0)
            self.assertIsNotNone(info)
            self.assertEqual(info["reason"], "output_limit")
            self.assertLessEqual(len(out), 5000)
            self.assertIn("Salida truncada", sys_msg)
            self.assertFalse(runner.is_running)
        finally:
            runner.cleanup()

    def test_interpreter_discovery_and_validation(self):
        ok, ver = validate_interpreter(sys.executable)
        self.assertTrue(ok)
        self.assertTrue(ver.lower().startswith("python"))
        found = discover_interpreters()
        self.assertGreaterEqual(len(found), 1)
        env = build_sanitized_env(tempfile.gettempdir())
        self.assertEqual(env["PYTHONUTF8"], "1")

    def test_tkinter_works_in_sanitized_subprocess(self):
        runner = CodeRunner()
        try:
            code = "import tkinter as tk\nr = tk.Tk()\nr.withdraw()\nprint('TK_OK')\nr.destroy()\n"
            ok, err = runner.run_code(code, timeout_seconds=10)
            self.assertTrue(ok, err)
            out, err_s, _sys, info = self._collect_until_exit(runner)
            self.assertIsNotNone(info)
            self.assertEqual(info["returncode"], 0, err_s)
            self.assertIn("TK_OK", out)
        finally:
            runner.cleanup()


class TestEditorAndAppSmoke(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        gc.collect()
        try:
            self.root.destroy()
        except Exception:
            pass
        gc.collect()

    def test_editor_tokenize_highlighting_and_smart_features(self):
        theme = get_theme("oscuro")
        configure_ttk_styles(self.root, theme)
        cfont = tkfont.Font(family="Consolas", size=12)
        ed = CodeEditor(self.root, theme=theme, code_font=cfont)
        sample = 'def saludo(x):\n    msg = "color #fff"\n    # comentario real\n    return len(msg)\n'
        ed.set_code(sample)

        # '#' dentro de la cadena NO debe quedar marcado como comentario
        comment_ranges = ed.text.tag_ranges("comment")
        comment_texts = [
            ed.text.get(comment_ranges[i], comment_ranges[i + 1])
            for i in range(0, len(comment_ranges), 2)
        ]
        self.assertEqual(comment_texts, ["# comentario real"])

        string_ranges = ed.text.tag_ranges("string")
        string_texts = [
            ed.text.get(string_ranges[i], string_ranges[i + 1])
            for i in range(0, len(string_ranges), 2)
        ]
        self.assertIn('"color #fff"', string_texts)

        # Buscar y reemplazar
        ed.show_search_bar(replace=True)
        ed.search_var.set("msg")
        ed._refresh_search()
        self.assertEqual(len(ed._search_matches), 2)
        ed.replace_var.set("texto")
        ed.replace_all()
        self.assertNotIn("msg", ed.get_code())
        self.assertIn("texto", ed.get_code())

        # Comentar / descomentar
        ed.goto_line(2)
        ed.toggle_comment()
        self.assertTrue(ed.text.get("2.0", "2.end").lstrip().startswith("# "))
        ed.toggle_comment()
        self.assertFalse(ed.text.get("2.0", "2.end").lstrip().startswith("#"))

    def test_app_instantiation_themes_and_console_protection(self):
        self.assertEqual(APP_VERSION, "0.2.1")
        with tempfile.TemporaryDirectory() as tmp:
            app = PyPracticeApp(self.root, settings=SettingsManager(base_dir=Path(tmp)))
            try:
                for tname in THEMES:
                    app.apply_theme(tname, persist=False)
                app.adjust_zoom(2)
                app.reset_zoom()

                # Verificar protección de input_start en consola y paneles opcionales
                app.console_write("Nombre: ")
                idx_input = app.console.index("input_start")
                self.assertEqual(idx_input, "1.8")
                app.console.insert("end", "Elena", "stdin")
                self.assertEqual(app.console.get("input_start", "end-1c"), "Elena")

                app.toggle_exercises_panel()
                self.assertTrue(app.show_exercises_var.get())
                self.assertIn(str(app.exercises_panel), [str(p) for p in app.paned.panes()])
                app.toggle_exercises_panel()
                self.assertFalse(app.show_exercises_var.get())
                self.assertNotIn(str(app.exercises_panel), [str(p) for p in app.paned.panes()])

                app.toggle_inspector_panel()
                self.assertTrue(app.show_inspector_var.get())
                self.assertIn(str(app.inspector_panel), [str(p) for p in app.right_paned.panes()])
                app.toggle_inspector_panel()
                self.assertFalse(app.show_inspector_var.get())
                self.assertNotIn(str(app.inspector_panel), [str(p) for p in app.right_paned.panes()])
                app.new_from_template(CODE_TEMPLATES[0])
                ed = app.get_active_editor()
                self.assertIn("Python", ed.get_code())
                self.assertTrue(ed.toggle_breakpoint(1))
                self.assertIn(1, ed.breakpoints)
            finally:
                app.cancel_timers()
                app.runner.cleanup()
                app.debugger.cleanup()
                del app
                gc.collect()


class TestExercisesDebuggerAndTemplates(unittest.TestCase):
    def test_evaluate_function_and_stdio_exercises(self):
        ex_fn = get_exercise_by_id("es_par")
        self.assertIsNotNone(ex_fn)
        res_ok = evaluate_exercise(ex_fn, "def es_par(n):\n    return n % 2 == 0\n")
        self.assertTrue(res_ok["ok"])
        self.assertEqual(res_ok["passed"], len(ex_fn.test_cases))

        res_fail = evaluate_exercise(ex_fn, "def es_par(n):\n    return False\n")
        self.assertFalse(res_fail["ok"])
        self.assertLess(res_fail["passed"], res_fail["total"])

        ex_io = get_exercise_by_id("hola_saludo")
        res_io = evaluate_exercise(ex_io, 'n = input()\nprint(f"Hola, {n}!")\n')
        self.assertTrue(res_io["ok"])
        self.assertEqual(len(EXERCISES), 10)

    def test_step_debugger_tracks_lines_variables_and_input(self):
        dbg = StepDebugger()
        try:
            code = (
                "x = 10\n"
                "nombre = input('N: ')\n"
                "y = x + 5\n"
                "print(nombre, y)\n"
            )
            ok, err = dbg.start(code)
            self.assertTrue(ok, err)

            paused_lines = []
            seen_vars = {}
            stdout_buf = ""
            exit_info = None
            deadline = time.monotonic() + 10.0

            while time.monotonic() < deadline and exit_info is None:
                try:
                    ev_type, payload, _rid = dbg.event_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if ev_type == "stdout":
                    stdout_buf += payload
                elif ev_type == "debug":
                    if payload.get("event") == "paused":
                        paused_lines.append(payload["line"])
                        for v in payload.get("variables", []):
                            seen_vars[v["name"]] = v["value"]
                        dbg.send_command("next")
                    elif payload.get("event") == "input_wait":
                        dbg.send_input("Eva")
                elif ev_type == "exit":
                    exit_info = payload

            self.assertIsNotNone(exit_info)
            self.assertEqual(exit_info["returncode"], 0)
            self.assertIn(1, paused_lines)
            self.assertIn(3, paused_lines)
            self.assertEqual(seen_vars.get("x"), "10")
            self.assertEqual(seen_vars.get("y"), "15")
            self.assertIn("Eva 15", stdout_buf)
        finally:
            dbg.cleanup()

    def test_step_debugger_enforces_output_limit_and_resists_frame_spoofing(self):
        from debugger import DBG_PREFIX

        # 1) Resistencia a falsificación del prefijo antiguo en stdout
        dbg_spoof = StepDebugger()
        try:
            spoof_code = f'print({DBG_PREFIX!r} + \'{{"event": "paused", "line": 999}}\')\n'
            ok, err = dbg_spoof.start(spoof_code)
            self.assertTrue(ok, err)
            seen_lines = []
            exit_info = None
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and exit_info is None:
                try:
                    ev_type, payload, _ = dbg_spoof.event_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if ev_type == "debug" and payload.get("event") == "paused":
                    seen_lines.append(payload["line"])
                    dbg_spoof.send_command("continue")
                elif ev_type == "exit":
                    exit_info = payload
            self.assertIsNotNone(exit_info)
            self.assertNotIn(999, seen_lines)
        finally:
            dbg_spoof.cleanup()

        # 2) Límite de salida en depuración ante bucle infinito de impresión
        dbg_flood = StepDebugger(max_output_chars=2500)
        try:
            flood_code = "while True:\n    print('X' * 400)\n"
            ok, err = dbg_flood.start(flood_code)
            self.assertTrue(ok, err)
            out_len = 0
            sys_msgs = []
            exit_info = None
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and exit_info is None:
                try:
                    ev_type, payload, _ = dbg_flood.event_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if ev_type == "stdout":
                    out_len += len(payload)
                elif ev_type == "system":
                    sys_msgs.append(payload)
                elif ev_type == "debug" and payload.get("event") == "paused":
                    dbg_flood.send_command("continue")
                elif ev_type == "exit":
                    exit_info = payload
            self.assertIsNotNone(exit_info)
            self.assertEqual(exit_info["reason"], "output_limit")
            self.assertLessEqual(out_len, 2500)
            self.assertTrue(any("Salida truncada" in m for m in sys_msgs))
        finally:
            dbg_flood.cleanup()

    def test_exercise_evaluator_stops_output_flood(self):
        ex_fn = get_exercise_by_id("es_par")
        self.assertIsNotNone(ex_fn)
        flood_solution = "def es_par(n):\n    while True:\n        print('A' * 1000)\n"
        res = evaluate_exercise(ex_fn, flood_solution, timeout_seconds=5)
        self.assertFalse(res["ok"])
        self.assertTrue(res["fatal"] is not None)
        self.assertIn("límite", res["fatal"].lower())

    def test_templates_cheatsheet_and_export_formats(self):
        self.assertGreaterEqual(len(CODE_TEMPLATES), 8)
        self.assertGreaterEqual(len(CHEATSHEET_SECTIONS), 8)

        md = format_practice_export(
            title="practica1.py",
            code="print(42)",
            console_output="42",
            interpreter_label="Python 3.14.0",
            fmt="md",
            exercise_report={
                "exercise_title": "3. Número par o impar",
                "passed": 4,
                "total": 4,
                "results": [{"label": "es_par(4)", "ok": True, "expected": "True", "actual": "True"}],
            },
        )
        self.assertIn("```python", md)
        self.assertIn("Evaluación automática", md)

        txt = format_practice_export(
            title="practica1.py",
            code="print(42)",
            console_output="42",
            interpreter_label="Python 3.14.0",
            fmt="txt",
        )
        self.assertIn("--- CÓDIGO FUENTE ---", txt)
        self.assertIn("print(42)", txt)


if __name__ == "__main__":
    unittest.main()
