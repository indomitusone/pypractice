"""Diagnósticos educativos en español para errores de sintaxis y tracebacks en ejecución."""

import ast
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Diagnostic:
    kind: str  # "syntax" | "runtime"
    title: str
    line: Optional[int]
    explanation: str
    tip: str


def check_syntax_detailed(code: str) -> Optional[Diagnostic]:
    """Valida sintaxis con ast.parse y devuelve un Diagnostic amigable si hay error."""
    if not code or not code.strip():
        return None
    try:
        ast.parse(code)
        return None
    except TabError as exc:
        return Diagnostic(
            kind="syntax",
            title=f"Error de tabulación (Línea {exc.lineno}): mezcla de tabuladores y espacios",
            line=exc.lineno,
            explanation="Has mezclado tabuladores y espacios en la sangría del código.",
            tip="Usa siempre espacios (en PyPractice la tecla Tab inserta 4 espacios automáticamente).",
        )
    except IndentationError as exc:
        msg_lower = str(exc.msg).lower()
        if "expected an indented block" in msg_lower:
            explanation = "Falta sangría (espacios al principio) justo debajo de una línea terminada en ':'."
            tip = "Añade 4 espacios al inicio de esta línea (por ejemplo tras if, for, while o def)."
        elif "unexpected indent" in msg_lower:
            explanation = "Esta línea tiene espacios de más al principio sin pertenecer a un bloque."
            tip = "Alinea la línea con la instrucción anterior o pulsa Shift+Tab para quitar sangría."
        else:
            explanation = "La sangría de esta línea no coincide con ningún nivel anterior."
            tip = "Comprueba que todas las líneas del mismo bloque empiecen en la misma columna."
        return Diagnostic(
            kind="syntax",
            title=f"Error de sangría (Línea {exc.lineno}): {exc.msg}",
            line=exc.lineno,
            explanation=explanation,
            tip=tip,
        )
    except SyntaxError as exc:
        line = exc.lineno
        msg_lower = str(exc.msg).lower()
        explanation = "Hay un error de escritura que impide a Python entender esta línea."
        tip = "Revisa paréntesis, comillas, dos puntos ':' y la línea inmediatamente anterior."

        if "expected ':'" in msg_lower:
            explanation = "Falta el símbolo ':' al final de una cabecera de bloque."
            tip = "Añade ':' después de if, elif, else, for, while, def, class, try o except."
        elif "unterminated string literal" in msg_lower or "eol while scanning string" in msg_lower:
            explanation = "Se abrió una cadena de texto con comillas pero no se cerró en la misma línea."
            tip = "Añade la comilla de cierre (' o \") o usa comillas triples (\"\"\") para varias líneas."
        elif "unterminated triple-quoted" in msg_lower:
            explanation = "Una cadena multilínea (''' o \"\"\") quedó sin cerrar."
            tip = "Añade las tres comillas de cierre al final del texto."
        elif "was never closed" in msg_lower or "unexpected eof" in msg_lower:
            explanation = "El código termina antes de cerrar algún paréntesis '(', corchete '[' o llave '{'."
            tip = "Comprueba que cada símbolo de apertura tenga su cierre correspondiente."
        elif "unmatched" in msg_lower or "closing parenthesis" in msg_lower:
            explanation = "Hay un símbolo de cierre ')', ']' o '}' que no coincide con su apertura."
            tip = "Verifica el orden y tipo de paréntesis o corchetes en esta línea."
        elif "cannot assign" in msg_lower:
            explanation = "Intentas asignar un valor a algo que no es una variable válida."
            tip = "La variable siempre va a la izquierda de '='. Si quieres comparar, usa '=='."
        elif "maybe you meant '=='" in msg_lower:
            explanation = "Usaste '=' (asignación) en un lugar donde probablemente querías comparar."
            tip = "Cambia '=' por '==' dentro de condiciones if o while."
        elif "invalid decimal literal" in msg_lower:
            explanation = "Un número está mal escrito o una variable empieza por un dígito."
            tip = "Los nombres de variable no pueden empezar con un número (usa p. ej. nota_1 en vez de 1nota)."
        elif "f-string" in msg_lower:
            explanation = "Hay un error dentro de una f-string (cadena con formato f\"...\")."
            tip = "Revisa las llaves { } y evita usar las mismas comillas sin escapar dentro."
        elif "missing parentheses in call to 'print'" in msg_lower:
            explanation = "En Python 3, print es una función y necesita paréntesis."
            tip = 'Escribe print("hola") en lugar de print "hola".'
        elif "invalid syntax" in msg_lower:
            explanation = "La estructura de esta línea no es válida en Python."
            tip = "Mira también la línea anterior: a veces un paréntesis sin cerrar ahí provoca el fallo aquí."

        return Diagnostic(
            kind="syntax",
            title=f"Error de sintaxis (Línea {line}): {exc.msg}",
            line=line,
            explanation=explanation,
            tip=tip,
        )
    except Exception as exc:
        return Diagnostic(
            kind="syntax",
            title=f"Error al analizar el código: {exc}",
            line=None,
            explanation="Ocurrió un problema inesperado al revisar el código.",
            tip="Comprueba que el archivo contenga texto UTF-8 válido.",
        )


def sanitize_traceback_text(stderr_text: str, script_path: Optional[str]) -> str:
    """Reemplaza la ruta temporal interna del runner por 'Tu código' en la salida de error."""
    if not stderr_text:
        return stderr_text
    cleaned = stderr_text
    if script_path:
        cleaned = cleaned.replace(script_path, "Tu código")
        cleaned = cleaned.replace(script_path.replace("\\", "/"), "Tu código")
    cleaned = re.sub(r'File "[^"]*pypractice[^"]*\.py"', 'File "Tu código"', cleaned)
    return cleaned


_RUNTIME_EXPLANATIONS = {
    "NameError": (
        "Estás usando un nombre o variable que Python no reconoce.",
        "Comprueba mayúsculas/minúsculas, posibles faltas de ortografía o si olvidaste definir la variable antes.",
    ),
    "UnboundLocalError": (
        "Intentas leer una variable local dentro de una función antes de asignarle un valor.",
        "Inicializa la variable antes de usarla o declara 'global nombre' si querías modificar una variable externa.",
    ),
    "TypeError": (
        "Estás combinando o pasando tipos de datos incompatibles (por ejemplo, sumar texto y número).",
        "Usa int(), float() o str() para convertir tipos, y revisa cuántos argumentos espera cada función.",
    ),
    "ValueError": (
        "El tipo de dato es correcto, pero su valor no es válido para la operación.",
        "Por ejemplo, int('hola') falla porque 'hola' no representa un número. Valida la entrada antes de convertirla.",
    ),
    "ZeroDivisionError": (
        "Intentaste dividir un número entre cero.",
        "Comprueba con un 'if divisor != 0:' antes de hacer divisiones '/' o módulos '%'.",
    ),
    "IndexError": (
        "Intentaste acceder a una posición que no existe en una lista, tupla o cadena.",
        "Recuerda que los índices empiezan en 0 y el último elemento es len(lista) - 1.",
    ),
    "KeyError": (
        "Buscaste una clave que no existe dentro de un diccionario.",
        "Usa diccionario.get(clave, valor_por_defecto) o comprueba 'if clave in diccionario:'.",
    ),
    "AttributeError": (
        "El objeto no tiene el método o atributo que estás intentando usar.",
        "Revisa el tipo del objeto con type(x) y comprueba si el nombre del método está bien escrito.",
    ),
    "ModuleNotFoundError": (
        "Python no encuentra el módulo que intentas importar.",
        "Comprueba el nombre del módulo o verifica que esté instalado en el intérprete/venv seleccionado.",
    ),
    "ImportError": (
        "El módulo existe, pero no se pudo importar el elemento solicitado.",
        "Revisa el nombre después de 'import' y asegúrate de no tener un archivo propio con el mismo nombre.",
    ),
    "FileNotFoundError": (
        "El archivo o carpeta que intentas abrir no existe en esa ruta.",
        "Comprueba el nombre exacto, la extensión y el directorio de trabajo actual.",
    ),
    "PermissionError": (
        "El sistema no permite leer, escribir o borrar ese archivo o carpeta.",
        "Verifica que el archivo no esté abierto en otro programa ni protegido por el sistema.",
    ),
    "RecursionError": (
        "Una función se llamó a sí misma demasiadas veces sin alcanzar un caso base.",
        "Asegúrate de tener una condición de parada clara que reduzca el problema en cada llamada.",
    ),
    "OverflowError": (
        "El resultado de un cálculo numérico es demasiado grande para representarse.",
        "Revisa si un bucle o potencia está creciendo sin control.",
    ),
    "MemoryError": (
        "El programa intentó reservar más memoria de la disponible.",
        "Revisa bucles o estructuras (listas/cadenas) que crezcan indefinidamente.",
    ),
    "AssertionError": (
        "Falló una comprobación 'assert' porque su condición resultó falsa.",
        "Imprime los valores implicados justo antes del assert para ver qué produjo el resultado inesperado.",
    ),
    "StopIteration": (
        "Se llamó a next() sobre un iterador que ya no tenía más elementos.",
        "Usa un bucle 'for' o proporciona un valor por defecto en next(iterador, None).",
    ),
    "EOFError": (
        "El programa esperaba una entrada con input(), pero el flujo de entrada se cerró.",
        "Asegúrate de escribir los datos solicitados en la consola y pulsar Enter.",
    ),
    "KeyboardInterrupt": (
        "La ejecución fue interrumpida manualmente.",
        "Puedes volver a ejecutar el programa cuando estés listo.",
    ),
}


def analyze_runtime_stderr(stderr_text: str, script_path: Optional[str] = None) -> Optional[Diagnostic]:
    """Analiza stderr tras una ejecución fallida y extrae línea y explicación amigable."""
    if not stderr_text or not stderr_text.strip():
        return None

    cleaned = sanitize_traceback_text(stderr_text, script_path)
    lines = [ln.strip() for ln in cleaned.strip().splitlines() if ln.strip()]
    if not lines:
        return None

    # Extraer última línea de error tipo "NameError: name 'x' is not defined"
    exc_type = None
    exc_detail = ""
    for line in reversed(lines):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Interrupt))(?::\s*(.*))?$", line)
        if match:
            exc_type = match.group(1)
            exc_detail = match.group(2) or ""
            break

    if not exc_type:
        return None

    # Buscar la última línea del traceback que apunte al código del usuario
    user_line = None
    user_matches = re.findall(r'File "Tu código", line (\d+)', cleaned)
    if user_matches:
        user_line = int(user_matches[-1])
    else:
        any_matches = re.findall(r'File "[^"]+", line (\d+)', cleaned)
        if any_matches:
            user_line = int(any_matches[-1])

    explanation, tip = _RUNTIME_EXPLANATIONS.get(
        exc_type,
        (
            f"Ocurrió una excepción '{exc_type}' durante la ejecución del programa.",
            "Lee el mensaje final del error y revisa la línea señalada en el editor.",
        ),
    )

    # Personalizar mensajes frecuentes con los datos concretos del traceback
    if exc_type == "NameError":
        m = re.search(r"name '([^']+)' is not defined", exc_detail)
        if m:
            explanation = f"La variable o función '{m.group(1)}' no está definida todavía en este punto."
    elif exc_type == "ModuleNotFoundError":
        m = re.search(r"No module named '([^']+)'", exc_detail)
        if m:
            explanation = f"No se encontró el módulo '{m.group(1)}' en el intérprete de Python actual."
    elif exc_type == "KeyError" and exc_detail:
        explanation = f"La clave {exc_detail} no existe dentro del diccionario."
    elif exc_type == "AttributeError":
        m = re.search(r"'([^']+)' object has no attribute '([^']+)'", exc_detail)
        if m:
            explanation = f"Un objeto de tipo '{m.group(1)}' no tiene el atributo o método '{m.group(2)}'."

    loc = f" (Línea {user_line})" if user_line is not None else ""
    detail_suffix = f": {exc_detail}" if exc_detail else ""
    return Diagnostic(
        kind="runtime",
        title=f"{exc_type}{loc}{detail_suffix}",
        line=user_line,
        explanation=explanation,
        tip=tip,
    )
