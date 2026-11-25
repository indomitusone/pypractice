"""Inspector estático AST ("Modo seguro") para advertir sobre operaciones de riesgo."""

import ast
from dataclasses import dataclass

RISKY_MODULES = {
    "subprocess": ("alto", "Permite ejecutar programas o comandos externos del sistema operativo."),
    "ctypes": ("alto", "Permite invocar librerías nativas y manipular memoria directamente."),
    "winreg": ("alto", "Permite leer o modificar el Registro de Windows."),
    "builtins": ("alto", "Permite acceder o alterar funciones integradas (eval, exec, __import__, open)."),
    "runpy": ("alto", "Permite localizar y ejecutar módulos o scripts Python de forma dinámica."),
    "pty": ("alto", "Permite abrir pseudoterminales y lanzar shells interactivas."),
    "code": ("alto", "Permite compilar y ejecutar código dinámico en intérpretes interactivos."),
    "pickle": ("alto", "Deserializar datos con pickle puede ejecutar código arbitrario."),
    "marshal": ("alto", "Deserializa bytecode interno de Python; inseguro con datos no confiables."),
    "shutil": ("moderado", "Incluye operaciones de archivos masivas como borrado recursivo (rmtree)."),
    "os": ("moderado", "Da acceso al sistema operativo, archivos, procesos y variables de entorno."),
    "socket": ("moderado", "Permite abrir conexiones de red o servidores locales."),
    "ssl": ("moderado", "Permite establecer conexiones de red cifradas."),
    "webbrowser": ("moderado", "Puede abrir URLs externas en el navegador del sistema."),
    "urllib": ("moderado", "Realiza peticiones a internet o direcciones de red."),
    "http": ("moderado", "Realiza conexiones HTTP de red."),
    "ftplib": ("moderado", "Permite abrir conexiones FTP salientes en red."),
    "smtplib": ("moderado", "Permite abrir conexiones SMTP y enviar correos en red."),
    "xmlrpc": ("moderado", "Permite realizar llamadas remotas o levantar servidores RPC."),
    "importlib": ("moderado", "Permite importar módulos de forma dinámica."),
}

RISKY_BUILTINS = {
    "eval": ("alto", "Ejecuta texto como expresión Python dinámica."),
    "exec": ("alto", "Ejecuta código Python arbitrario desde una cadena."),
    "__import__": ("alto", "Importa módulos dinámicamente saltándose las importaciones visibles."),
    "compile": ("moderado", "Compila código dinámico para su posterior ejecución."),
    "breakpoint": ("moderado", "Invoca un depurador interactivo en tiempo de ejecución."),
}

RISKY_ATTR_CALLS = {
    ("os", "system"): ("alto", "Ejecuta comandos directamente en la shell del sistema."),
    ("os", "popen"): ("alto", "Lanza comandos del sistema mediante tubería."),
    ("os", "startfile"): ("alto", "Abre archivos, ejecutables o URLs usando el shell de Windows."),
    ("os", "remove"): ("alto", "Elimina archivos del disco."),
    ("os", "unlink"): ("alto", "Elimina archivos del disco."),
    ("os", "rmdir"): ("alto", "Elimina carpetas del disco."),
    ("os", "removedirs"): ("alto", "Elimina carpetas de forma recursiva."),
    ("os", "kill"): ("alto", "Envía señales para detener procesos del sistema."),
    ("os", "replace"): ("moderado", "Sobrescribe o mueve archivos en disco."),
    ("os", "rename"): ("moderado", "Renombra o mueve archivos en disco."),
    ("os", "chmod"): ("moderado", "Modifica permisos de archivos en disco."),
    ("shutil", "rmtree"): ("alto", "Borra una carpeta completa y todo su contenido de forma irreversible."),
    ("shutil", "move"): ("moderado", "Mueve o sobrescribe archivos o carpetas en disco."),
    ("pathlib", "unlink"): ("alto", "Elimina un archivo mediante pathlib."),
    ("pickle", "load"): ("alto", "Deserializar objetos con pickle.load() puede ejecutar código arbitrario."),
    ("pickle", "loads"): ("alto", "Deserializar bytes con pickle.loads() puede ejecutar código arbitrario."),
}

SUSPICIOUS_DUNDERS = frozenset(
    {
        "__subclasses__",
        "__builtins__",
        "__globals__",
        "__import__",
        "__loader__",
        "__spec__",
        "__code__",
        "__closure__",
        "__bases__",
        "__mro__",
    }
)

RISKY_REFLECTION_TARGETS = frozenset(
    set(RISKY_BUILTINS.keys())
    | {"open", "system", "popen", "startfile", "rmtree", "unlink", "remove", "removedirs", "rmdir", "kill"}
    | set(SUSPICIOUS_DUNDERS)
)

WRITE_OPEN_MODES = ("w", "a", "x", "+")


@dataclass(frozen=True)
class SafetyWarning:
    line: int
    severity: str  # "alto" | "moderado"
    symbol: str
    reason: str


class _SafetyVisitor(ast.NodeVisitor):
    def __init__(self):
        self.warnings: list[SafetyWarning] = []
        self.aliases: dict[str, str] = {}
        self.imported_symbols: dict[str, tuple[str, str]] = {}

    def _add(self, node, severity: str, symbol: str, reason: str):
        line = getattr(node, "lineno", 1) or 1
        item = SafetyWarning(line=line, severity=severity, symbol=symbol, reason=reason)
        if item not in self.warnings:
            self.warnings.append(item)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            root_mod = alias.name.split(".")[0]
            local_name = alias.asname or root_mod
            self.aliases[local_name] = root_mod
            if root_mod in RISKY_MODULES:
                sev, reason = RISKY_MODULES[root_mod]
                self._add(node, sev, f"import {alias.name}", reason)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            root_mod = node.module.split(".")[0]
            if root_mod in RISKY_MODULES:
                sev, reason = RISKY_MODULES[root_mod]
                self._add(node, sev, f"from {node.module} import ...", reason)
            for alias in node.names:
                local_name = alias.asname or alias.name
                self.imported_symbols[local_name] = (root_mod, alias.name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if node.id == "__builtins__":
            self._add(
                node,
                "alto",
                "__builtins__",
                "Accede directamente a las funciones integradas (posible evasión del Modo seguro).",
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr in SUSPICIOUS_DUNDERS:
            self._add(
                node,
                "alto",
                f".{node.attr}",
                "Accede a atributos internos de Python usados habitualmente para reflexión avanzada o evasión.",
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
            if name in RISKY_BUILTINS:
                sev, reason = RISKY_BUILTINS[name]
                self._add(node, sev, f"{name}()", reason)
            elif name == "open":
                self._inspect_open_call(node, path_idx=0, mode_idx=1)
            elif name in ("getattr", "setattr", "delattr"):
                self._inspect_reflection_call(node, name)
            elif name in self.imported_symbols:
                mod, orig = self.imported_symbols[name]
                if (mod, orig) in RISKY_ATTR_CALLS:
                    sev, reason = RISKY_ATTR_CALLS[(mod, orig)]
                    self._add(node, sev, f"{mod}.{orig}()", reason)
                elif (mod, orig) == ("io", "open"):
                    self._inspect_open_call(node, path_idx=0, mode_idx=1)
                elif mod == "os" and (orig.startswith("exec") or orig.startswith("spawn")):
                    self._add(node, "alto", f"os.{orig}()", "Ejecuta un proceso externo del sistema.")
        elif isinstance(func, ast.Attribute):
            attr = func.attr
            base = self.aliases.get(func.value.id, func.value.id) if isinstance(func.value, ast.Name) else None
            if base is not None:
                if (base, attr) in RISKY_ATTR_CALLS:
                    sev, reason = RISKY_ATTR_CALLS[(base, attr)]
                    self._add(node, sev, f"{base}.{attr}()", reason)
                elif base == "subprocess":
                    self._add(
                        node,
                        "alto",
                        f"subprocess.{attr}()",
                        "Ejecuta un proceso externo del sistema.",
                    )
                elif base == "os" and (attr.startswith("exec") or attr.startswith("spawn")):
                    self._add(
                        node,
                        "alto",
                        f"os.{attr}()",
                        "Ejecuta un proceso externo del sistema.",
                    )
                elif base == "io" and attr == "open":
                    self._inspect_open_call(node, path_idx=0, mode_idx=1)
            if attr == "open" and base not in ("io", "webbrowser"):
                # Cubre llamadas como pathlib.Path(...).open("w")
                self._inspect_open_call(node, path_idx=None, mode_idx=0, symbol_prefix=".open")
            if attr in ("unlink", "rmdir", "rmtree", "write_text", "write_bytes"):
                self._add(
                    node,
                    "moderado" if attr.startswith("write") else "alto",
                    f".{attr}()",
                    "Modifica o elimina archivos en disco.",
                )
        self.generic_visit(node)

    def _inspect_reflection_call(self, node: ast.Call, fn_name: str):
        if len(node.args) < 2:
            return
        target_arg = node.args[0]
        attr_arg = node.args[1]

        target_is_sensitive = False
        if isinstance(target_arg, ast.Name):
            resolved = self.aliases.get(target_arg.id, target_arg.id)
            if resolved in ("__builtins__", "builtins") or resolved in RISKY_MODULES:
                target_is_sensitive = True
        elif isinstance(target_arg, ast.Call) and isinstance(target_arg.func, ast.Name):
            if target_arg.func.id in ("globals", "locals", "vars", "__import__"):
                target_is_sensitive = True

        if isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str):
            if attr_arg.value in RISKY_REFLECTION_TARGETS or target_is_sensitive:
                self._add(
                    node,
                    "alto",
                    f'{fn_name}(..., "{attr_arg.value}")',
                    "Resuelve dinámicamente un símbolo u operación sensible mediante reflexión.",
                )
        elif target_is_sensitive:
            self._add(
                node,
                "alto",
                f"{fn_name}(..., <dinámico>)",
                "Accede dinámicamente a atributos de un módulo u objeto sensible (posible ofuscación).",
            )

    @staticmethod
    def _is_external_path(path_str: str) -> bool:
        s = path_str.strip()
        if not s:
            return False
        if s.startswith(("/", "\\", "~")):
            return True
        if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
            return True
        parts = s.replace("\\", "/").split("/")
        return ".." in parts

    def _inspect_open_call(
        self,
        node: ast.Call,
        *,
        path_idx: int | None = 0,
        mode_idx: int = 1,
        symbol_prefix: str = "open",
    ):
        if path_idx is not None and len(node.args) > path_idx:
            p_arg = node.args[path_idx]
            if isinstance(p_arg, ast.Constant) and isinstance(p_arg.value, str) and self._is_external_path(p_arg.value):
                self._add(
                    node,
                    "moderado",
                    f"{symbol_prefix}(<ruta externa>)",
                    "Accede a una ruta absoluta o externa al directorio temporal de trabajo.",
                )

        mode_node = None
        if len(node.args) > mode_idx:
            mode_node = node.args[mode_idx]
        for kw in node.keywords:
            if kw.arg == "mode":
                mode_node = kw.value

        if mode_node is None:
            return

        if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
            mode = mode_node.value
            if any(ch in mode for ch in WRITE_OPEN_MODES):
                self._add(
                    node,
                    "moderado",
                    f'{symbol_prefix}(..., "{mode}")',
                    "Abre un archivo en modo de escritura o creación.",
                )
        else:
            self._add(
                node,
                "moderado",
                f"{symbol_prefix}(..., <modo dinámico>)",
                "Abre un archivo con un modo calculado dinámicamente que podría escribir en disco.",
            )


def inspect_code_safety(code: str) -> list[SafetyWarning]:
    """Analiza el AST del código y devuelve advertencias de operaciones sensibles."""
    if not code or not code.strip():
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    visitor = _SafetyVisitor()
    visitor.visit(tree)
    # Ordenar primero por severidad ("alto" antes que "moderado") y luego por línea
    severity_rank = {"alto": 0, "moderado": 1}
    visitor.warnings.sort(key=lambda w: (severity_rank.get(w.severity, 9), w.line))
    return visitor.warnings
