"""Biblioteca de ejercicios progresivos de Python con pistas y evaluación automática aislada."""

from dataclasses import dataclass
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from typing import Any, Optional

from runner import _WindowsJobObject, build_sanitized_env, resolve_interpreter

MAX_EXERCISE_OUTPUT_BYTES = 262_144


def _terminate_eval_tree(proc: subprocess.Popen, job: Optional[_WindowsJobObject]) -> None:
    if job is not None:
        job.terminate_and_close()
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


def _run_isolated_subprocess(
    cmd: list[str],
    *,
    stdin_text: Optional[str] = None,
    timeout_seconds: int = 5,
    cwd: str,
    env: dict[str, str],
    max_output_bytes: int = MAX_EXERCISE_OUTPUT_BYTES,
) -> tuple[Optional[int], str, str, str]:
    """Ejecuta un subproceso aislado con Job Object / grupo de procesos, timeout y límite de salida.

    Devuelve (returncode, stdout, stderr, reason) donde reason es 'finished' | 'timeout' | 'output_limit'.
    """
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
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

    proc = subprocess.Popen(cmd, **popen_kwargs)
    job = _WindowsJobObject() if sys.platform == "win32" else None
    if job is not None:
        job.assign(proc)

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    lock = threading.Lock()
    state = {"total_bytes": 0, "truncated": False}

    def reader(stream, target: list[bytes]) -> None:
        try:
            fd = stream.fileno()
        except Exception:
            return
        while True:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            hit_limit = False
            with lock:
                if state["truncated"]:
                    continue
                remaining = max_output_bytes - state["total_bytes"]
                if len(data) > remaining:
                    if remaining > 0:
                        target.append(data[:remaining])
                    state["total_bytes"] = max_output_bytes
                    state["truncated"] = True
                    hit_limit = True
                else:
                    target.append(data)
                    state["total_bytes"] += len(data)
            if hit_limit:
                _terminate_eval_tree(proc, job)
                break

    t_out = threading.Thread(target=reader, args=(proc.stdout, stdout_chunks), daemon=True)
    t_err = threading.Thread(target=reader, args=(proc.stderr, stderr_chunks), daemon=True)
    t_out.start()
    t_err.start()

    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text.encode("utf-8"))
            proc.stdin.flush()
        except Exception:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    timed_out = False
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_eval_tree(proc, job)
    finally:
        if job is not None:
            job.terminate_and_close()

    t_out.join(timeout=1.0)
    t_err.join(timeout=1.0)
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except Exception:
                pass

    out_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    err_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    if state["truncated"]:
        return None, out_str, err_str, "output_limit"
    if timed_out:
        return None, out_str, err_str, "timeout"
    return proc.returncode, out_str, err_str, "finished"


@dataclass(frozen=True)
class Exercise:
    id: str
    level: str
    title: str
    description: str
    starter_code: str
    hints: tuple[str, ...]
    test_mode: str  # "function" | "stdio"
    function_name: str
    test_cases: tuple[dict[str, Any], ...]


EXERCISES: tuple[Exercise, ...] = (
    Exercise(
        id="hola_saludo",
        level="Nivel 1 · Fundamentos",
        title="1. Saludo interactivo",
        description=(
            "Pide el nombre del usuario usando input() y muestra exactamente:\n"
            "Hola, <nombre>!\n\n"
            "Ejemplo: si se escribe Ana, la salida debe ser:\n"
            "Hola, Ana!"
        ),
        starter_code=(
            '# Pide el nombre con input() y muestra "Hola, <nombre>!"\n'
            'nombre = input()\n'
            'print(f"Hola, {nombre}!")\n'
        ),
        hints=(
            "Usa nombre = input() para leer el texto introducido por el usuario.",
            'Imprime el resultado con una f-string: print(f"Hola, {nombre}!").',
            "No añadas textos extra dentro de input() para que la salida coincida exactamente.",
        ),
        test_mode="stdio",
        function_name="",
        test_cases=(
            {"label": 'Entrada "Ana"', "stdin": "Ana\n", "expected": "Hola, Ana!"},
            {"label": 'Entrada "Carlos"', "stdin": "Carlos\n", "expected": "Hola, Carlos!"},
        ),
    ),
    Exercise(
        id="area_rectangulo",
        level="Nivel 1 · Fundamentos",
        title="2. Área de un rectángulo",
        description=(
            "Define una función area_rectangulo(base, altura) que devuelva el área "
            "de un rectángulo (base * altura)."
        ),
        starter_code=(
            "def area_rectangulo(base, altura):\n"
            "    # Devuelve el producto de base por altura\n"
            "    pass\n"
        ),
        hints=(
            "El área de un rectángulo es base * altura.",
            "Usa la palabra clave return para devolver el resultado en vez de solo imprimirlo.",
        ),
        test_mode="function",
        function_name="area_rectangulo",
        test_cases=(
            {"label": "area_rectangulo(5, 3)", "args": [5, 3], "expected": 15},
            {"label": "area_rectangulo(10, 0)", "args": [10, 0], "expected": 0},
            {"label": "area_rectangulo(2.5, 4)", "args": [2.5, 4], "expected": 10.0},
        ),
    ),
    Exercise(
        id="es_par",
        level="Nivel 2 · Condicionales",
        title="3. Número par o impar",
        description=(
            "Define una función es_par(n) que devuelva True si el número entero n es par "
            "y False si es impar."
        ),
        starter_code=(
            "def es_par(n):\n"
            "    # Comprueba el resto al dividir entre 2\n"
            "    pass\n"
        ),
        hints=(
            "El operador módulo (%) devuelve el resto de una división.",
            "Un número es par cuando n % 2 == 0.",
            "Puedes devolver directamente: return n % 2 == 0",
        ),
        test_mode="function",
        function_name="es_par",
        test_cases=(
            {"label": "es_par(4)", "args": [4], "expected": True},
            {"label": "es_par(7)", "args": [7], "expected": False},
            {"label": "es_par(0)", "args": [0], "expected": True},
            {"label": "es_par(-6)", "args": [-6], "expected": True},
        ),
    ),
    Exercise(
        id="clasificar_nota",
        level="Nivel 2 · Condicionales",
        title="4. Clasificador de notas",
        description=(
            "Define clasificar_nota(nota) para una nota de 0 a 10:\n"
            "- Menor que 0 o mayor que 10 -> 'Inválida'\n"
            "- Menor que 5 -> 'Suspenso'\n"
            "- De 5 a menos de 7 -> 'Aprobado'\n"
            "- De 7 a menos de 9 -> 'Notable'\n"
            "- De 9 a 10 -> 'Sobresaliente'"
        ),
        starter_code=(
            "def clasificar_nota(nota):\n"
            "    # Usa if / elif / else para clasificar la nota\n"
            "    pass\n"
        ),
        hints=(
            "Valida primero el rango inválido: if nota < 0 or nota > 10: return 'Inválida'.",
            "Después encadena elif nota < 5:, elif nota < 7:, elif nota < 9: y else:.",
        ),
        test_mode="function",
        function_name="clasificar_nota",
        test_cases=(
            {"label": "nota = 3.5", "args": [3.5], "expected": "Suspenso"},
            {"label": "nota = 5", "args": [5], "expected": "Aprobado"},
            {"label": "nota = 8.2", "args": [8.2], "expected": "Notable"},
            {"label": "nota = 9.5", "args": [9.5], "expected": "Sobresaliente"},
            {"label": "nota = 12", "args": [12], "expected": "Inválida"},
        ),
    ),
    Exercise(
        id="suma_hasta",
        level="Nivel 3 · Bucles",
        title="5. Suma acumulada hasta N",
        description=(
            "Define suma_hasta(n) que devuelva la suma de los enteros desde 1 hasta n.\n"
            "Si n <= 0, debe devolver 0."
        ),
        starter_code=(
            "def suma_hasta(n):\n"
            "    total = 0\n"
            "    # Recorre desde 1 hasta n acumulando en total\n"
            "    return total\n"
        ),
        hints=(
            "Usa range(1, n + 1) porque range no incluye el límite superior.",
            "Dentro del bucle for i in range(1, n + 1): haz total += i.",
        ),
        test_mode="function",
        function_name="suma_hasta",
        test_cases=(
            {"label": "suma_hasta(5)", "args": [5], "expected": 15},
            {"label": "suma_hasta(10)", "args": [10], "expected": 55},
            {"label": "suma_hasta(0)", "args": [0], "expected": 0},
            {"label": "suma_hasta(-3)", "args": [-3], "expected": 0},
        ),
    ),
    Exercise(
        id="contar_vocales",
        level="Nivel 3 · Bucles",
        title="6. Contador de vocales",
        description=(
            "Define contar_vocales(texto) que devuelva cuántas vocales contiene texto "
            "(cuenta mayúsculas, minúsculas y vocales con tilde: aeiouáéíóú)."
        ),
        starter_code=(
            "def contar_vocales(texto):\n"
            '    vocales = "aeiouáéíóú"\n'
            "    # Cuenta cuántas letras de texto.lower() están en vocales\n"
            "    pass\n"
        ),
        hints=(
            "Convierte primero el texto a minúsculas con texto.lower().",
            "Recorre cada letra y comprueba: if letra in vocales: contador += 1.",
        ),
        test_mode="function",
        function_name="contar_vocales",
        test_cases=(
            {"label": '"Python"', "args": ["Python"], "expected": 1},
            {"label": '"Programación"', "args": ["Programación"], "expected": 5},
            {"label": '"AEIOU"', "args": ["AEIOU"], "expected": 5},
            {"label": '"xyz"', "args": ["xyz"], "expected": 0},
        ),
    ),
    Exercise(
        id="filtrar_pares",
        level="Nivel 4 · Listas y Diccionarios",
        title="7. Filtrar números pares",
        description=(
            "Define filtrar_pares(numeros) que reciba una lista de enteros y devuelva "
            "una nueva lista conservando solo los números pares en el mismo orden."
        ),
        starter_code=(
            "def filtrar_pares(numeros):\n"
            "    resultado = []\n"
            "    # Añade a resultado solo los números pares\n"
            "    return resultado\n"
        ),
        hints=(
            "Recorre cada n en numeros y usa resultado.append(n) si n % 2 == 0.",
            "También puedes resolverlo en una línea con comprensión de listas: [n for n in numeros if n % 2 == 0].",
        ),
        test_mode="function",
        function_name="filtrar_pares",
        test_cases=(
            {"label": "[1, 2, 3, 4, 5, 6]", "args": [[1, 2, 3, 4, 5, 6]], "expected": [2, 4, 6]},
            {"label": "[1, 3, 5]", "args": [[1, 3, 5]], "expected": []},
            {"label": "[0, -2, 7, 8]", "args": [[0, -2, 7, 8]], "expected": [0, -2, 8]},
        ),
    ),
    Exercise(
        id="contar_palabras",
        level="Nivel 4 · Listas y Diccionarios",
        title="8. Frecuencia de palabras",
        description=(
            "Define contar_palabras(frase) que devuelva un diccionario donde cada clave "
            "sea una palabra en minúsculas y su valor sea cuántas veces aparece."
        ),
        starter_code=(
            "def contar_palabras(frase):\n"
            "    conteo = {}\n"
            "    # Separa las palabras con frase.lower().split()\n"
            "    return conteo\n"
        ),
        hints=(
            "frase.lower().split() divide por espacios y normaliza a minúsculas.",
            "Para cada palabra: conteo[palabra] = conteo.get(palabra, 0) + 1.",
        ),
        test_mode="function",
        function_name="contar_palabras",
        test_cases=(
            {
                "label": '"hola mundo hola"',
                "args": ["hola mundo hola"],
                "expected": {"hola": 2, "mundo": 1},
            },
            {
                "label": '"Python python JAVA"',
                "args": ["Python python JAVA"],
                "expected": {"python": 2, "java": 1},
            },
            {"label": '"" (vacío)', "args": [""], "expected": {}},
        ),
    ),
    Exercise(
        id="es_palindromo",
        level="Nivel 5 · Retos",
        title="9. Detector de palíndromos",
        description=(
            "Define es_palindromo(texto) que devuelva True si texto se lee igual "
            "de izquierda a derecha que al revés (ignorando espacios y mayúsculas)."
        ),
        starter_code=(
            "def es_palindromo(texto):\n"
            "    # Limpia espacios, pasa a minúsculas y compara con su inversa\n"
            "    pass\n"
        ),
        hints=(
            'Elimina espacios con texto.replace(" ", "").lower().',
            "En Python puedes invertir una cadena con limpio[::-1].",
        ),
        test_mode="function",
        function_name="es_palindromo",
        test_cases=(
            {"label": '"radar"', "args": ["radar"], "expected": True},
            {"label": '"Anita lava la tina"', "args": ["Anita lava la tina"], "expected": True},
            {"label": '"Python"', "args": ["Python"], "expected": False},
        ),
    ),
    Exercise(
        id="fibonacci",
        level="Nivel 5 · Retos",
        title="10. Secuencia de Fibonacci",
        description=(
            "Define fibonacci(n) que devuelva una lista con los primeros n números "
            "de la sucesión de Fibonacci empezando en 0, 1, 1, 2, 3, 5...\n"
            "Si n <= 0 devuelve []."
        ),
        starter_code=(
            "def fibonacci(n):\n"
            "    # Devuelve una lista con los primeros n términos\n"
            "    pass\n"
        ),
        hints=(
            "Usa dos variables a, b = 0, 1 y repite n veces: añade a a la lista y actualiza a, b = b, a + b.",
            "Comprueba los casos borde n = 0 -> [] y n = 1 -> [0].",
        ),
        test_mode="function",
        function_name="fibonacci",
        test_cases=(
            {"label": "fibonacci(0)", "args": [0], "expected": []},
            {"label": "fibonacci(1)", "args": [1], "expected": [0]},
            {"label": "fibonacci(6)", "args": [6], "expected": [0, 1, 1, 2, 3, 5]},
            {"label": "fibonacci(8)", "args": [8], "expected": [0, 1, 1, 2, 3, 5, 8, 13]},
        ),
    ),
)

_HARNESS_FUNCTION_TEMPLATE = """
import json, sys, traceback

user_ns = {}
with open(sys.argv[1], "r", encoding="utf-8") as fh:
    user_code = fh.read()

results = []
try:
    exec(compile(user_code, "Tu código", "exec"), user_ns)
except Exception as exc:
    print(json.dumps({"fatal": f"{type(exc).__name__}: {exc}", "results": []}, ensure_ascii=False))
    sys.exit(0)

fn_name = sys.argv[2]
fn = user_ns.get(fn_name)
if not callable(fn):
    print(json.dumps({
        "fatal": f"No se encontró la función '{fn_name}'. Asegúrate de definirla con 'def {fn_name}(...):'.",
        "results": [],
    }, ensure_ascii=False))
    sys.exit(0)

with open(sys.argv[3], "r", encoding="utf-8") as fh:
    cases = json.load(fh)

for case in cases:
    label = case["label"]
    args = case["args"]
    expected = case["expected"]
    try:
        actual = fn(*args)
        ok = actual == expected
        results.append({
            "label": label,
            "ok": ok,
            "expected": repr(expected),
            "actual": repr(actual),
            "error": None,
        })
    except Exception as exc:
        results.append({
            "label": label,
            "ok": False,
            "expected": repr(expected),
            "actual": None,
            "error": f"{type(exc).__name__}: {exc}",
        })

print(json.dumps({"fatal": None, "results": results}, ensure_ascii=False))
"""


def get_exercise_by_id(ex_id: str) -> Optional[Exercise]:
    for ex in EXERCISES:
        if ex.id == ex_id:
            return ex
    return None


def evaluate_exercise(
    exercise: Exercise,
    user_code: str,
    *,
    interpreter_path: str = "",
    use_isolated_mode: bool = True,
    timeout_seconds: int = 5,
) -> dict[str, Any]:
    """Evalúa la solución del usuario en un subproceso aislado y devuelve el informe estructurado."""
    python_cmd, err_msg = resolve_interpreter(interpreter_path)
    if not python_cmd:
        return {"ok": False, "passed": 0, "total": len(exercise.test_cases), "fatal": err_msg, "results": []}

    with tempfile.TemporaryDirectory(prefix="pypractice_eval_") as tmp_dir:
        work_dir = os.path.join(tmp_dir, "work")
        sand_tmp = os.path.join(tmp_dir, "tmp")
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(sand_tmp, exist_ok=True)
        if sys.platform != "win32":
            try:
                os.chmod(tmp_dir, 0o700)
            except OSError:
                pass
        env = build_sanitized_env(sand_tmp)

        user_file = os.path.join(tmp_dir, "user_solution.py")
        with open(user_file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(user_code)

        if exercise.test_mode == "stdio":
            results = []
            for case in exercise.test_cases:
                cmd = [python_cmd]
                if use_isolated_mode:
                    cmd.append("-I")
                cmd.extend(["-X", "utf8", user_file])
                rc, stdout_s, stderr_s, reason = _run_isolated_subprocess(
                    cmd,
                    stdin_text=case["stdin"],
                    timeout_seconds=timeout_seconds,
                    cwd=work_dir,
                    env=env,
                )
                if reason == "timeout":
                    results.append(
                        {
                            "label": case["label"],
                            "ok": False,
                            "expected": repr(case["expected"]),
                            "actual": None,
                            "error": f"Tiempo límite excedido ({timeout_seconds} s)",
                        }
                    )
                elif reason == "output_limit":
                    results.append(
                        {
                            "label": case["label"],
                            "ok": False,
                            "expected": repr(case["expected"]),
                            "actual": None,
                            "error": "Salida excesiva detenida por límite de seguridad.",
                        }
                    )
                elif rc != 0:
                    err_line = (stderr_s.strip().splitlines() or ["Error de ejecución"])[-1]
                    results.append(
                        {
                            "label": case["label"],
                            "ok": False,
                            "expected": repr(case["expected"]),
                            "actual": None,
                            "error": err_line,
                        }
                    )
                else:
                    actual = stdout_s.strip()
                    expected = case["expected"].strip()
                    results.append(
                        {
                            "label": case["label"],
                            "ok": actual == expected,
                            "expected": repr(expected),
                            "actual": repr(actual),
                            "error": None,
                        }
                    )
            passed = sum(1 for r in results if r["ok"])
            return {
                "ok": passed == len(results) and len(results) > 0,
                "passed": passed,
                "total": len(results),
                "fatal": None,
                "results": results,
            }

        # Modo función
        harness_file = os.path.join(tmp_dir, "harness.py")
        cases_file = os.path.join(tmp_dir, "cases.json")
        with open(harness_file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_HARNESS_FUNCTION_TEMPLATE)
        with open(cases_file, "w", encoding="utf-8") as fh:
            json.dump(list(exercise.test_cases), fh, ensure_ascii=False)

        cmd = [python_cmd]
        if use_isolated_mode:
            cmd.append("-I")
        cmd.extend(["-X", "utf8", harness_file, user_file, exercise.function_name, cases_file])

        rc, stdout_s, stderr_s, reason = _run_isolated_subprocess(
            cmd,
            stdin_text=None,
            timeout_seconds=timeout_seconds,
            cwd=work_dir,
            env=env,
        )
        if reason == "timeout":
            return {
                "ok": False,
                "passed": 0,
                "total": len(exercise.test_cases),
                "fatal": f"Tiempo límite excedido ({timeout_seconds} s). Comprueba que no haya un bucle infinito.",
                "results": [],
            }
        if reason == "output_limit":
            return {
                "ok": False,
                "passed": 0,
                "total": len(exercise.test_cases),
                "fatal": "Salida excesiva detenida por el límite de seguridad del evaluador.",
                "results": [],
            }

        # La última línea JSON de stdout es el informe (el código del alumno podría haber hecho print antes)
        payload = None
        for line in reversed(stdout_s.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        if payload is None:
            err_detail = stderr_s.strip() or stdout_s.strip() or "No se pudo evaluar la solución."
            return {
                "ok": False,
                "passed": 0,
                "total": len(exercise.test_cases),
                "fatal": err_detail,
                "results": [],
            }

        results = payload.get("results", [])
        fatal = payload.get("fatal")
        passed = sum(1 for r in results if r.get("ok"))
        return {
            "ok": fatal is None and passed == len(exercise.test_cases) and len(results) == len(exercise.test_cases),
            "passed": passed,
            "total": len(exercise.test_cases),
            "fatal": fatal,
            "results": results,
        }
