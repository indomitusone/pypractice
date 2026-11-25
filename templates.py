"""Plantillas rápidas de inicio, chuleta de Python (cheatsheet) y exportación de prácticas."""

from datetime import datetime
from typing import Any, Optional

CODE_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "id": "hola_mundo",
        "name": "Hola Mundo y variables",
        "code": (
            '# Plantilla básica: variables y f-strings\n'
            'nombre = "Python"\n'
            'version = 3\n'
            'print(f"¡Hola, {nombre} {version}!")\n'
        ),
    },
    {
        "id": "input_interactivo",
        "name": "Entrada interactiva con input()",
        "code": (
            '# Plantilla: pedir datos al usuario\n'
            'nombre = input("¿Cómo te llamas? ")\n'
            'edad = int(input("¿Cuántos años tienes? "))\n'
            'print(f"Hola {nombre}, el año que viene tendrás {edad + 1} años.")\n'
        ),
    },
    {
        "id": "condicionales",
        "name": "Condicionales (if / elif / else)",
        "code": (
            "# Plantilla: toma de decisiones\n"
            "temperatura = 22\n\n"
            "if temperatura >= 30:\n"
            '    print("Hace calor")\n'
            "elif temperatura >= 18:\n"
            '    print("Temperatura agradable")\n'
            "else:\n"
            '    print("Hace frío")\n'
        ),
    },
    {
        "id": "bucles",
        "name": "Bucles (for y while)",
        "code": (
            "# Plantilla: repetición con for y while\n"
            'print("Tabla del 5:")\n'
            "for i in range(1, 6):\n"
            '    print(f"5 x {i} = {5 * i}")\n\n'
            "contador = 3\n"
            "while contador > 0:\n"
            '    print("Cuenta atrás:", contador)\n'
            "    contador -= 1\n"
            'print("¡Despegue!")\n'
        ),
    },
    {
        "id": "listas_diccionarios",
        "name": "Listas y diccionarios",
        "code": (
            "# Plantilla: colecciones de datos\n"
            'frutas = ["manzana", "pera", "naranja"]\n'
            'frutas.append("kiwi")\n'
            'print("Frutas:", frutas)\n\n'
            'estudiante = {"nombre": "Lucía", "curso": "1º", "nota": 9.2}\n'
            "for clave, valor in estudiante.items():\n"
            '    print(f"{clave}: {valor}")\n'
        ),
    },
    {
        "id": "funciones",
        "name": "Funciones con retorno",
        "code": (
            "# Plantilla: funciones reutilizables\n"
            "def calcular_promedio(valores):\n"
            "    if not valores:\n"
            "        return 0\n"
            "    return sum(valores) / len(valores)\n\n"
            "notas = [7.5, 8.0, 9.5]\n"
            'print(f"Promedio: {calcular_promedio(notas):.2f}")\n'
        ),
    },
    {
        "id": "try_except",
        "name": "Manejo de errores (try / except)",
        "code": (
            "# Plantilla: capturar errores de entrada sin romper el programa\n"
            "try:\n"
            '    numero = int(input("Introduce un número entero: "))\n'
            '    print(f"100 dividido entre {numero} es {100 / numero:.2f}")\n'
            "except ValueError:\n"
            '    print("Error: debes escribir un número entero válido.")\n'
            "except ZeroDivisionError:\n"
            '    print("Error: no se puede dividir entre cero.")\n'
        ),
    },
    {
        "id": "tkinter_basico",
        "name": "Ventana gráfica básica con Tkinter",
        "code": (
            "# Plantilla: aplicación gráfica con Tkinter\n"
            "# Consejo: selecciona 'Sin límite' en el selector de Límite para dejarla abierta.\n"
            "import tkinter as tk\n\n"
            "ventana = tk.Tk()\n"
            'ventana.title("Mi primera ventana")\n'
            'ventana.geometry("320x160")\n\n'
            "contador = tk.IntVar(value=0)\n"
            'etiqueta = tk.Label(ventana, text="Clics: 0", font=("Segoe UI", 14))\n'
            "etiqueta.pack(pady=20)\n\n"
            "def sumar():\n"
            "    contador.set(contador.get() + 1)\n"
            '    etiqueta.config(text=f"Clics: {contador.get()}")\n\n'
            'tk.Button(ventana, text="Sumar clic", command=sumar).pack()\n'
            "ventana.mainloop()\n"
        ),
    },
)

CHEATSHEET_SECTIONS: tuple[dict[str, str], ...] = (
    {
        "category": "Variables y tipos",
        "summary": "Enteros (int), decimales (float), texto (str), booleanos (bool) y conversiones.",
        "snippet": (
            'edad = 20               # int\n'
            'precio = 19.95          # float\n'
            'nombre = "Ada"          # str\n'
            'activo = True           # bool\n'
            'numero = int("42")      # Conversión str -> int\n'
            'texto = str(100)        # Conversión int -> str'
        ),
    },
    {
        "category": "Entrada y salida",
        "summary": "Mostrar mensajes con print() y leer datos con input().",
        "snippet": (
            'nombre = input("Tu nombre: ")\n'
            'print(f"Hola, {nombre}")\n'
            'print("A", "B", "C", sep=" - ")'
        ),
    },
    {
        "category": "Operadores",
        "summary": "Aritméticos (+, -, *, /, //, %, **), comparación (==, !=, <, >=) y lógicos (and, or, not).",
        "snippet": (
            "division_exacta = 7 / 2     # 3.5\n"
            "division_entera = 7 // 2    # 3\n"
            "resto = 7 % 2               # 1\n"
            "potencia = 2 ** 3           # 8\n"
            "en_rango = 1 <= 5 and 5 <= 10"
        ),
    },
    {
        "category": "Cadenas de texto (str)",
        "summary": "Métodos útiles y slicing sobre textos.",
        "snippet": (
            'frase = "  Hola Python  "\n'
            "frase.strip()               # Quita espacios extremos\n"
            "frase.lower()               # Minúsculas\n"
            "frase.upper()               # Mayúsculas\n"
            'frase.replace("Python", "mundo")\n'
            "frase.split()               # Lista de palabras\n"
            '"Python"[::-1]              # Invertir texto -> "nohtyP"'
        ),
    },
    {
        "category": "Condicionales (if / elif / else)",
        "summary": "Ejecutar bloques según condiciones booleanas. Recuerda los ':' y 4 espacios.",
        "snippet": (
            "nota = 8\n"
            "if nota >= 9:\n"
            '    print("Sobresaliente")\n'
            "elif nota >= 5:\n"
            '    print("Aprobado")\n'
            "else:\n"
            '    print("Suspenso")'
        ),
    },
    {
        "category": "Bucles (for y while)",
        "summary": "Iterar sobre rangos/colecciones o repetir mientras se cumpla una condición.",
        "snippet": (
            "for i in range(5):          # 0, 1, 2, 3, 4\n"
            "    print(i)\n\n"
            "for idx, val in enumerate(['a', 'b']):\n"
            "    print(idx, val)\n\n"
            "x = 0\n"
            "while x < 3:\n"
            "    x += 1"
        ),
    },
    {
        "category": "Listas y diccionarios",
        "summary": "Estructuras mutables ordenadas (list) y pares clave-valor (dict).",
        "snippet": (
            "nums = [10, 20, 30]\n"
            "nums.append(40)             # Añadir al final\n"
            "len(nums)                   # Longitud -> 4\n"
            "pares = [n for n in nums if n % 20 == 0]\n\n"
            'persona = {"nombre": "Ana", "edad": 25}\n'
            'persona.get("ciudad", "Sin datos")'
        ),
    },
    {
        "category": "Funciones (def / return)",
        "summary": "Agrupar lógica reutilizable con parámetros opcionales y valor de retorno.",
        "snippet": (
            "def saludar(nombre, saludo='Hola'):\n"
            '    return f"{saludo}, {nombre}!"\n\n'
            'mensaje = saludar("Elena")'
        ),
    },
    {
        "category": "Errores (try / except)",
        "summary": "Capturar excepciones para evitar que el programa se detenga abruptamente.",
        "snippet": (
            "try:\n"
            '    n = int("abc")\n'
            "except ValueError as e:\n"
            '    print("No es un número:", e)'
        ),
    },
)


def format_practice_export(
    *,
    title: str,
    code: str,
    console_output: str,
    interpreter_label: str,
    fmt: str = "md",
    exercise_report: Optional[dict[str, Any]] = None,
) -> str:
    """Genera un informe limpio en Markdown (.md) o texto plano (.txt) listo para entregar."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    clean_out = console_output.strip() or "(Sin salida en consola)"

    if fmt == "md":
        lines = [
            f"# Práctica de Python — {title}",
            "",
            f"- **Fecha:** {now_str}",
            f"- **Intérprete:** {interpreter_label}",
            "- **Generado con:** PyPractice",
            "",
            "## Código fuente",
            "",
            "```python",
            code.rstrip("\n"),
            "```",
            "",
            "## Salida de consola",
            "",
            "```text",
            clean_out,
            "```",
        ]
        if exercise_report:
            ex_title = exercise_report.get("exercise_title", "Ejercicio")
            passed = exercise_report.get("passed", 0)
            total = exercise_report.get("total", 0)
            lines.extend(
                [
                    "",
                    f"## Evaluación automática — {ex_title}",
                    "",
                    f"**Resultado:** {passed}/{total} pruebas superadas.",
                    "",
                ]
            )
            for item in exercise_report.get("results", []):
                mark = "✔" if item.get("ok") else "✖"
                lines.append(f"- {mark} **{item.get('label')}**: esperado `{item.get('expected')}`, obtenido `{item.get('actual')}`")
        lines.append("")
        return "\n".join(lines)

    # Formato texto plano (.txt)
    sep = "=" * 64
    lines = [
        sep,
        f"PRÁCTICA DE PYTHON — {title}",
        f"Fecha: {now_str} | Intérprete: {interpreter_label}",
        sep,
        "",
        "--- CÓDIGO FUENTE ---",
        code.rstrip("\n"),
        "",
        "--- SALIDA DE CONSOLA ---",
        clean_out,
    ]
    if exercise_report:
        ex_title = exercise_report.get("exercise_title", "Ejercicio")
        passed = exercise_report.get("passed", 0)
        total = exercise_report.get("total", 0)
        lines.extend(["", f"--- EVALUACIÓN AUTOMÁTICA: {ex_title} ({passed}/{total}) ---"])
        for item in exercise_report.get("results", []):
            mark = "OK" if item.get("ok") else "FALLO"
            lines.append(f"[{mark}] {item.get('label')} -> esperado {item.get('expected')}, obtenido {item.get('actual')}")
    lines.append("")
    return "\n".join(lines)
