"""PyPractice v0.2.0 — Entorno educativo, seguro y accesible para aprender Python."""

import ctypes
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk
from typing import Any, Optional

from debugger import StepDebugger
from diagnostics import Diagnostic, analyze_runtime_stderr, sanitize_traceback_text
from editor import CodeEditor
from exercises import EXERCISES, Exercise, evaluate_exercise
from runner import CodeRunner, discover_interpreters, resolve_interpreter, validate_interpreter
from safety import SafetyWarning, inspect_code_safety
from settings import SettingsManager
from templates import CHEATSHEET_SECTIONS, CODE_TEMPLATES, format_practice_export
from themes import THEMES, configure_ttk_styles, get_theme

APP_VERSION = "0.2.0"
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 28
DEFAULT_FONT_SIZE = 12
AUTOSAVE_INTERVAL_MS = 15_000

TIMEOUT_OPTIONS = [
    ("Sin límite", 0),
    ("10 s", 10),
    ("30 s", 30),
    ("60 s", 60),
    ("120 s", 120),
]


def enable_windows_dpi_awareness() -> None:
    """Activa High-DPI nativo en Windows para texto e interfaz nítidos en pantallas escaladas."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class AccessibleToolTip:
    """Tooltip accesible: responde a ratón y foco de teclado y nunca se recorta fuera de pantalla."""

    def __init__(self, widget, text: str, theme_getter):
        self.widget = widget
        self.text = text
        self.theme_getter = theme_getter
        self.tooltip_window: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None

        self.widget.bind("<Enter>", self._schedule_show, add="+")
        self.widget.bind("<Leave>", self.hide, add="+")
        self.widget.bind("<FocusIn>", self._schedule_show, add="+")
        self.widget.bind("<FocusOut>", self.hide, add="+")
        self.widget.bind("<Destroy>", self.hide, add="+")

    def _schedule_show(self, _event=None) -> None:
        self.hide()
        self._after_id = self.widget.after(220, self.show)

    def show(self, _event=None) -> None:
        if self.tooltip_window or not self.widget.winfo_exists():
            return
        theme = self.theme_getter()
        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.configure(bg=theme["tooltip_border"])

        label = tk.Label(
            tw,
            text=self.text,
            justify="left",
            wraplength=420,
            bg=theme["tooltip_bg"],
            fg=theme["tooltip_fg"],
            font=("Segoe UI", 10),
            padx=10,
            pady=6,
        )
        label.pack(padx=1, pady=1)

        tw.update_idletasks()
        tip_w = tw.winfo_reqwidth()
        tip_h = tw.winfo_reqheight()
        screen_w = tw.winfo_screenwidth()
        screen_h = tw.winfo_screenheight()

        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6

        if x + tip_w > screen_w - 8:
            x = max(8, screen_w - tip_w - 8)
        if y + tip_h > screen_h - 8:
            y = max(8, self.widget.winfo_rooty() - tip_h - 6)

        tw.wm_geometry(f"+{x}+{y}")
        self.tooltip_window = tw

    def hide(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self.tooltip_window is not None:
            try:
                self.tooltip_window.destroy()
            except Exception:
                pass
            self.tooltip_window = None


class PyPracticeApp:
    @staticmethod
    def get_resource_path(relative_path: str) -> str:
        """Obtiene la ruta absoluta al recurso en desarrollo y en PyInstaller."""
        try:
            base_path = sys._MEIPASS  # type: ignore[attr-defined]
        except Exception:
            base_path = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_path, relative_path)

    def __init__(self, root: tk.Tk, *, settings: Optional[SettingsManager] = None):
        self.root = root
        self.settings = settings if settings is not None else SettingsManager()
        self.theme_name: str = self.settings.get("theme", "oscuro")
        self.theme: dict = get_theme(self.theme_name)

        self._font_family: str = self.settings.get("font_family", "Consolas")
        self._font_size: int = max(
            MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(self.settings.get("font_size", DEFAULT_FONT_SIZE)))
        )
        self.code_font = tkfont.Font(family=self._font_family, size=self._font_size)

        self.safe_mode_var = tk.BooleanVar(value=bool(self.settings.get("safe_mode", True)))
        self.isolated_mode_var = tk.BooleanVar(value=bool(self.settings.get("use_isolated_mode", True)))
        self.audio_feedback_var = tk.BooleanVar(value=bool(self.settings.get("audio_feedback", False)))
        self.show_exercises_var = tk.BooleanVar(value=False)
        self.show_inspector_var = tk.BooleanVar(value=False)
        self.theme_var = tk.StringVar(value=self.theme_name)
        self.timeout_label_var = tk.StringVar()

        self.interpreter_path: str = str(self.settings.get("interpreter_path", ""))
        self.script_args: str = str(self.settings.get("script_args", ""))
        self.interpreter_label: str = "Detectando..."

        self.editors: dict[str, CodeEditor] = {}
        self._untitled_counter = 0
        self._running_editor: Optional[CodeEditor] = None
        self._current_live_diag: Optional[Diagnostic] = None
        self._queue_job: Optional[str] = None
        self._autosave_job: Optional[str] = None

        # Estado de ejercicios y depuración
        self._selected_exercise: Exercise = EXERCISES[0]
        self._hint_index: int = 0
        self._last_exercise_report: Optional[dict[str, Any]] = None
        self._eval_queue: queue.Queue = queue.Queue()
        self._prev_debug_vars: dict[tuple[str, str], str] = {}

        self.runner = CodeRunner()
        self.debugger = StepDebugger()

        self._setup_window()
        self.style = configure_ttk_styles(self.root, self.theme)
        self._setup_menu()
        self._setup_ui()
        self._bind_global_shortcuts()
        self.apply_theme(self.theme_name, persist=False)

        self._refresh_interpreter_info()
        self._restore_or_create_initial_tab()
        self._update_run_controls(running=False, debugging=False)
        self._refresh_exercises_list()
        self._display_selected_exercise()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.process_queue()
        self._schedule_autosave()

    @property
    def _is_busy(self) -> bool:
        return self.runner.is_running or self.debugger.is_running

    # --- Ventana y menú ---
    def _setup_window(self) -> None:
        self.root.title(f"PyPractice v{APP_VERSION}")

        window_width = 1280
        window_height = 790
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        if screen_width < 1320 or screen_height < 830:
            window_width = max(880, int(screen_width * 0.92))
            window_height = max(580, int(screen_height * 0.88))

        center_x = max(0, int((screen_width - window_width) / 2))
        center_y = max(0, int((screen_height - window_height) / 2))

        self.root.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")
        self.root.minsize(860, 540)

        icon_path = self.get_resource_path(os.path.join("assets", "pypractice.ico"))
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(default=icon_path)
            except Exception:
                pass

    def _setup_menu(self) -> None:
        self.menubar = tk.Menu(self.root)

        # Archivo
        self.menu_file = tk.Menu(self.menubar, tearoff=0)
        self.menu_file.add_command(label="Nuevo archivo", accelerator="Ctrl+N", command=self.new_tab)

        self.menu_templates = tk.Menu(self.menu_file, tearoff=0)
        for tpl in CODE_TEMPLATES:
            self.menu_templates.add_command(
                label=tpl["name"],
                command=lambda t=tpl: self.new_from_template(t),
            )
        self.menu_file.add_cascade(label="Nuevo desde plantilla", menu=self.menu_templates)

        self.menu_file.add_command(label="Abrir...", accelerator="Ctrl+O", command=self.open_file)
        self.menu_recent = tk.Menu(self.menu_file, tearoff=0)
        self.menu_file.add_cascade(label="Abrir recientes", menu=self.menu_recent)
        self.menu_file.add_separator()
        self.menu_file.add_command(label="Guardar", accelerator="Ctrl+S", command=self.save_file)
        self.menu_file.add_command(label="Guardar como...", accelerator="Ctrl+Shift+S", command=self.save_file_as)
        self.menu_file.add_command(
            label="Exportar práctica (.md / .txt)...", accelerator="Ctrl+E", command=self.export_practice
        )
        self.menu_file.add_command(label="Cerrar pestaña", accelerator="Ctrl+W", command=self.close_current_tab)
        self.menu_file.add_separator()
        self.menu_file.add_command(label="Salir", accelerator="Alt+F4", command=self.on_close)
        self.menubar.add_cascade(label="Archivo", menu=self.menu_file)

        # Editar
        self.menu_edit = tk.Menu(self.menubar, tearoff=0)
        self.menu_edit.add_command(label="Deshacer", accelerator="Ctrl+Z", command=lambda: self._emit_editor("<<Undo>>"))
        self.menu_edit.add_command(label="Rehacer", accelerator="Ctrl+Y", command=lambda: self._emit_editor("<<Redo>>"))
        self.menu_edit.add_separator()
        self.menu_edit.add_command(label="Cortar", accelerator="Ctrl+X", command=lambda: self._emit_editor("<<Cut>>"))
        self.menu_edit.add_command(label="Copiar", accelerator="Ctrl+C", command=lambda: self._emit_editor("<<Copy>>"))
        self.menu_edit.add_command(label="Pegar", accelerator="Ctrl+V", command=lambda: self._emit_editor("<<Paste>>"))
        self.menu_edit.add_separator()
        self.menu_edit.add_command(label="Buscar...", accelerator="Ctrl+F", command=lambda: self._show_search(False))
        self.menu_edit.add_command(label="Reemplazar...", accelerator="Ctrl+H", command=lambda: self._show_search(True))
        self.menu_edit.add_command(label="Comentar / Descomentar", accelerator="Ctrl+/", command=self._toggle_comment)
        self.menu_edit.add_command(label="Ir a la línea...", accelerator="Ctrl+G", command=self.goto_line_dialog)
        self.menubar.add_cascade(label="Editar", menu=self.menu_edit)

        # Ejecutar y Depurar
        self.menu_run = tk.Menu(self.menubar, tearoff=0)
        self.menu_run.add_command(label="Ejecutar", accelerator="F5", command=self.run_code)
        self.menu_run.add_command(label="Depurar paso a paso", accelerator="F10", command=self.debug_start_or_next)
        self.menu_run.add_command(label="Entrar en función (Step Into)", accelerator="F11", command=self.debug_step_into)
        self.menu_run.add_command(label="Continuar hasta punto de ruptura", accelerator="F8", command=self.debug_continue)
        self.menu_run.add_command(label="Punto de ruptura en línea actual", accelerator="F9", command=self.toggle_current_breakpoint)
        self.menu_run.add_command(label="Detener", accelerator="Shift+F5", command=self.stop_code)
        self.menu_run.add_command(label="Limpiar consola", accelerator="Ctrl+L", command=self.clear_console)
        self.menu_run.add_separator()
        self.menu_run.add_checkbutton(
            label="Modo seguro (análisis AST preventivo)",
            variable=self.safe_mode_var,
            command=self._on_safe_mode_changed,
        )
        self.menu_run.add_checkbutton(
            label="Modo aislado de Python (-I)",
            variable=self.isolated_mode_var,
            command=self._on_isolated_mode_changed,
        )
        self.menu_run.add_separator()
        self.menu_run.add_command(label="Configurar intérprete / argumentos...", command=self.open_interpreter_dialog)
        self.menubar.add_cascade(label="Ejecutar", menu=self.menu_run)

        # Ver y Accesibilidad
        self.menu_view = tk.Menu(self.menubar, tearoff=0)
        self.menu_view.add_checkbutton(
            label="Panel de ejercicios",
            accelerator="Ctrl+Shift+E",
            variable=self.show_exercises_var,
            command=self.sync_exercises_panel,
        )
        self.menu_view.add_checkbutton(
            label="Inspector de variables",
            accelerator="Ctrl+Shift+V",
            variable=self.show_inspector_var,
            command=self.sync_inspector_panel,
        )
        self.menu_view.add_separator()
        self.menu_theme = tk.Menu(self.menu_view, tearoff=0)
        for key, tdata in THEMES.items():
            self.menu_theme.add_radiobutton(
                label=tdata["label"],
                variable=self.theme_var,
                value=key,
                command=lambda k=key: self.apply_theme(k, persist=True),
            )
        self.menu_view.add_cascade(label="Tema de color", menu=self.menu_theme)
        self.menu_view.add_separator()
        self.menu_view.add_command(label="Aumentar texto", accelerator="Ctrl++", command=lambda: self.adjust_zoom(1))
        self.menu_view.add_command(label="Reducir texto", accelerator="Ctrl+-", command=lambda: self.adjust_zoom(-1))
        self.menu_view.add_command(label="Restablecer zoom (100 %)", accelerator="Ctrl+0", command=self.reset_zoom)
        self.menu_view.add_command(label="Elegir tipografía...", command=self.open_font_dialog)
        self.menu_view.add_separator()
        self.menu_view.add_command(label="Alternar foco Editor / Consola", accelerator="F6", command=self.toggle_panel_focus)
        self.menu_view.add_checkbutton(
            label="Aviso sonoro al terminar o fallar",
            variable=self.audio_feedback_var,
            command=lambda: self.settings.set("audio_feedback", self.audio_feedback_var.get()),
        )
        self.menubar.add_cascade(label="Ver", menu=self.menu_view)

        # Ayuda
        self.menu_help = tk.Menu(self.menubar, tearoff=0)
        self.menu_help.add_command(label="Chuleta rápida de Python", accelerator="F2", command=self.show_cheatsheet_dialog)
        self.menu_help.add_command(label="Atajos de teclado", accelerator="F1", command=self.show_shortcuts_dialog)
        self.menu_help.add_separator()
        self.menu_help.add_command(label="Acerca de PyPractice", command=self.show_about_dialog)
        self.menubar.add_cascade(label="Ayuda", menu=self.menu_help)

        self.root.config(menu=self.menubar)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        self.menu_recent.delete(0, tk.END)
        recents = self.settings.get("recent_files", [])
        if not recents:
            self.menu_recent.add_command(label="(Sin archivos recientes)", state="disabled")
            return
        for path in recents:
            label = f"{os.path.basename(path)} — {path}"
            self.menu_recent.add_command(label=label, command=lambda p=path: self.open_file_path(p))

    # --- Construcción de la UI principal ---
    def _setup_ui(self) -> None:
        # Barra superior adaptable
        self.top_frame = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(10, 6))
        self.top_frame.pack(side="top", fill="x")

        self.title_lbl = ttk.Label(
            self.top_frame,
            text="PyPractice",
            style="Toolbar.TLabel",
            font=("Segoe UI", 12, "bold"),
        )
        self.title_lbl.pack(side="left", padx=(0, 10))

        self.btn_new = ttk.Button(self.top_frame, text="Nuevo", style="Neutral.TButton", command=self.new_tab)
        self.btn_new.pack(side="left", padx=2)
        AccessibleToolTip(self.btn_new, "Nueva pestaña vacía (Ctrl+N)", lambda: self.theme)

        self.btn_open = ttk.Button(self.top_frame, text="Abrir", style="Neutral.TButton", command=self.open_file)
        self.btn_open.pack(side="left", padx=2)
        AccessibleToolTip(self.btn_open, "Abrir archivo .py (Ctrl+O)", lambda: self.theme)

        self.btn_save = ttk.Button(self.top_frame, text="Guardar", style="Neutral.TButton", command=self.save_file)
        self.btn_save.pack(side="left", padx=2)
        AccessibleToolTip(self.btn_save, "Guardar archivo actual (Ctrl+S)", lambda: self.theme)

        self.btn_exercises = ttk.Button(
            self.top_frame, text="Ejercicios", style="Neutral.TButton", command=self.toggle_exercises_panel
        )
        self.btn_exercises.pack(side="left", padx=(8, 2))
        AccessibleToolTip(self.btn_exercises, "Abrir/ocultar biblioteca de retos guiados (Ctrl+Shift+E)", lambda: self.theme)

        self.btn_cheatsheet = ttk.Button(
            self.top_frame, text="Chuleta", style="Neutral.TButton", command=self.show_cheatsheet_dialog
        )
        self.btn_cheatsheet.pack(side="left", padx=2)
        AccessibleToolTip(self.btn_cheatsheet, "Guía rápida de sintaxis Python (F2)", lambda: self.theme)

        # Controles derechos
        self.run_btn = ttk.Button(self.top_frame, text="▶ Ejecutar", style="Run.TButton", command=self.run_code)
        self.run_btn.pack(side="right", padx=(4, 0))
        AccessibleToolTip(self.run_btn, "Ejecutar código actual (F5 o Ctrl+Enter)", lambda: self.theme)

        self.debug_btn = ttk.Button(
            self.top_frame, text="🐞 Paso a paso", style="Debug.TButton", command=self.debug_start_or_next
        )
        self.debug_btn.pack(side="right", padx=4)
        AccessibleToolTip(
            self.debug_btn,
            "Iniciar depuración visual paso a paso o avanzar a la siguiente línea (F10)",
            lambda: self.theme,
        )

        self.stop_btn = ttk.Button(self.top_frame, text="■ Detener", style="Stop.TButton", command=self.stop_code)
        self.stop_btn.pack(side="right", padx=4)
        AccessibleToolTip(self.stop_btn, "Detener ejecución o depuración activa (Shift+F5)", lambda: self.theme)

        self.clear_btn = ttk.Button(self.top_frame, text="Limpiar", style="Neutral.TButton", command=self.clear_console)
        self.clear_btn.pack(side="right", padx=4)
        AccessibleToolTip(self.clear_btn, "Limpiar consola (Ctrl+L)", lambda: self.theme)

        timeout_box = ttk.Frame(self.top_frame, style="Toolbar.TFrame")
        timeout_box.pack(side="right", padx=(4, 8))
        ttk.Label(timeout_box, text="Límite:", style="Toolbar.TLabel", font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        saved_timeout = int(self.settings.get("timeout_seconds", 60))
        selected_lbl = next((lbl for lbl, sec in TIMEOUT_OPTIONS if sec == saved_timeout), "60 s")
        self.timeout_label_var.set(selected_lbl)
        self.timeout_combo = ttk.Combobox(
            timeout_box,
            textvariable=self.timeout_label_var,
            values=[lbl for lbl, _ in TIMEOUT_OPTIONS],
            state="readonly",
            width=9,
            style="App.TCombobox",
        )
        self.timeout_combo.pack(side="left")
        self.timeout_combo.bind("<<ComboboxSelected>>", self._on_timeout_changed)
        AccessibleToolTip(
            self.timeout_combo,
            "Tiempo máximo de ejecución para evitar bucles infinitos. Usa 'Sin límite' para programas Tkinter.",
            lambda: self.theme,
        )

        self.chk_safe = ttk.Checkbutton(
            self.top_frame,
            text="Modo seguro",
            variable=self.safe_mode_var,
            command=self._on_safe_mode_changed,
            style="Toolbar.TCheckbutton",
        )
        self.chk_safe.pack(side="right", padx=(4, 8))
        AccessibleToolTip(
            self.chk_safe,
            "Analiza el AST antes de ejecutar y pide confirmación ante operaciones de riesgo.",
            lambda: self.theme,
        )

        # Área principal dividida
        self.paned = tk.PanedWindow(
            self.root,
            orient="horizontal",
            sashwidth=6,
            sashrelief="flat",
            borderwidth=0,
        )
        self.paned.pack(expand=True, fill="both", padx=8, pady=(6, 4))

        # --- Panel opcional izquierdo: Biblioteca de ejercicios ---
        self._build_exercises_panel()

        # --- Panel central: Editor con pestañas ---
        self.editor_panel = ttk.Frame(self.paned, style="Panel.TFrame")
        self.paned.add(self.editor_panel, minsize=360, stretch="always")

        editor_header = ttk.Frame(self.editor_panel, style="Panel.TFrame", padding=(8, 6, 8, 2))
        editor_header.pack(fill="x")
        ttk.Label(editor_header, text="CÓDIGO", style="PanelHeader.TLabel").pack(side="left")

        self.btn_close_tab = ttk.Button(
            editor_header, text="Cerrar pestaña", style="Neutral.TButton", command=self.close_current_tab
        )
        self.btn_close_tab.pack(side="right")

        self.btn_export = ttk.Button(
            editor_header, text="Exportar", style="Neutral.TButton", command=self.export_practice
        )
        self.btn_export.pack(side="right", padx=(0, 4))
        AccessibleToolTip(self.btn_export, "Exportar código y salida en .md o .txt (Ctrl+E)", lambda: self.theme)

        self.live_diag_frame = tk.Frame(self.editor_panel, padx=8, pady=4)
        self.live_diag_lbl = tk.Label(
            self.live_diag_frame, text="", anchor="w", justify="left", font=("Segoe UI", 9, "bold")
        )
        self.live_diag_lbl.pack(side="left", fill="x", expand=True)
        self.live_diag_btn = ttk.Button(
            self.live_diag_frame,
            text="[ i ] Ver ayuda",
            style="Neutral.TButton",
            command=self._show_live_diagnostic_details,
        )
        self.live_diag_btn.pack(side="right", padx=(6, 0))

        self.notebook = ttk.Notebook(self.editor_panel, style="Editor.TNotebook")
        self.notebook.pack(expand=True, fill="both", padx=6, pady=(2, 6))
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.notebook.bind("<Button-2>", self._on_tab_middle_click)

        # --- Panel derecho: Consola + Inspector de variables (vertical) ---
        self.right_paned = tk.PanedWindow(
            self.paned,
            orient="vertical",
            sashwidth=6,
            sashrelief="flat",
            borderwidth=0,
        )
        self.paned.add(self.right_paned, minsize=340, stretch="always")

        self.console_panel = ttk.Frame(self.right_paned, style="Panel.TFrame")
        self.right_paned.add(self.console_panel, minsize=220, stretch="always")

        console_header = ttk.Frame(self.console_panel, style="Panel.TFrame", padding=(8, 6, 8, 2))
        console_header.pack(fill="x")
        ttk.Label(console_header, text="CONSOLA", style="PanelHeader.TLabel").pack(side="left")

        self.args_badge = ttk.Label(console_header, text="", style="PanelHeader.TLabel")
        self.args_badge.pack(side="left", padx=(10, 0))
        self._update_args_badge()

        self.btn_copy_console = ttk.Button(
            console_header, text="Copiar salida", style="Neutral.TButton", command=self.copy_console_output
        )
        self.btn_copy_console.pack(side="right")

        # Barra de controles de depuración (visible durante depuración paso a paso)
        self.debug_bar = ttk.Frame(self.console_panel, style="Toolbar.TFrame", padding=(8, 4))
        self.debug_loc_var = tk.StringVar(value="🐞 Depuración en pausa")
        ttk.Label(self.debug_bar, textvariable=self.debug_loc_var, style="Toolbar.TLabel", font=("Segoe UI", 9, "bold")).pack(
            side="left", padx=(0, 8)
        )
        self.btn_dbg_next = ttk.Button(
            self.debug_bar, text="⏭ Siguiente (F10)", style="Debug.TButton", command=self.debug_next
        )
        self.btn_dbg_next.pack(side="left", padx=2)
        self.btn_dbg_into = ttk.Button(
            self.debug_bar, text="⤵ Entrar (F11)", style="Neutral.TButton", command=self.debug_step_into
        )
        self.btn_dbg_into.pack(side="left", padx=2)
        self.btn_dbg_cont = ttk.Button(
            self.debug_bar, text="▶ Continuar (F8)", style="Neutral.TButton", command=self.debug_continue
        )
        self.btn_dbg_cont.pack(side="left", padx=2)

        console_body = ttk.Frame(self.console_panel, style="Panel.TFrame")
        console_body.pack(expand=True, fill="both", padx=6, pady=(2, 6))
        console_body.grid_rowconfigure(0, weight=1)
        console_body.grid_columnconfigure(0, weight=1)

        self.console = tk.Text(
            console_body,
            font=self.code_font,
            undo=False,
            borderwidth=0,
            highlightthickness=2,
            wrap="word",
            padx=8,
            pady=6,
        )
        self.console.grid(row=0, column=0, sticky="nsew")

        self.console_vscroll = ttk.Scrollbar(
            console_body, orient="vertical", command=self.console.yview, style="App.Vertical.TScrollbar"
        )
        self.console_vscroll.grid(row=0, column=1, sticky="ns")
        self.console.config(yscrollcommand=self.console_vscroll.set)

        self.console.bind("<Return>", self.on_console_enter)
        self.console.bind("<BackSpace>", self.on_console_backspace)
        self.console.bind("<Delete>", self.on_console_delete)
        self.console.bind("<Key>", self.on_console_key)
        self.console.bind("<<Paste>>", self.on_console_paste)
        self.console.bind("<<Cut>>", lambda _e: "break")

        self.console.mark_set("input_start", "1.0")
        self.console.mark_gravity("input_start", "left")

        # Inspector de variables (añadido dinámicamente al right_paned)
        self._build_inspector_panel()

        # --- Barra de estado inferior ---
        self.status_bar = ttk.Frame(self.root, style="Status.TFrame")
        self.status_bar.pack(side="bottom", fill="x")

        self.status_state_var = tk.StringVar(value="● Listo")
        self.status_pos_var = tk.StringVar(value="Lín 1, Col 1")
        self.status_zoom_var = tk.StringVar(value="100 %")
        self.status_python_var = tk.StringVar(value="Python...")

        self.lbl_status_state = ttk.Label(self.status_bar, textvariable=self.status_state_var, style="Status.TLabel")
        self.lbl_status_state.pack(side="left")

        self.lbl_status_zoom = ttk.Label(self.status_bar, textvariable=self.status_zoom_var, style="Status.TLabel")
        self.lbl_status_zoom.pack(side="right")

        self.lbl_status_pos = ttk.Label(self.status_bar, textvariable=self.status_pos_var, style="Status.TLabel")
        self.lbl_status_pos.pack(side="right")

        self.btn_status_python = ttk.Button(
            self.status_bar,
            textvariable=self.status_python_var,
            style="Neutral.TButton",
            command=self.open_interpreter_dialog,
        )
        self.btn_status_python.pack(side="right", padx=6, pady=2)
        AccessibleToolTip(
            self.btn_status_python,
            "Intérprete Python activo. Haz clic para cambiar de intérprete/venv o configurar argumentos.",
            lambda: self.theme,
        )

    def _build_exercises_panel(self) -> None:
        self.exercises_panel = ttk.Frame(self.paned, style="Panel.TFrame")

        header = ttk.Frame(self.exercises_panel, style="Panel.TFrame", padding=(8, 6, 8, 2))
        header.pack(fill="x")
        ttk.Label(header, text="EJERCICIOS", style="PanelHeader.TLabel").pack(side="left")
        self.ex_progress_var = tk.StringVar(value="0/10")
        ttk.Label(header, textvariable=self.ex_progress_var, style="PanelHeader.TLabel").pack(side="left", padx=(8, 0))
        ttk.Button(header, text="✕", width=3, style="Neutral.TButton", command=self.toggle_exercises_panel).pack(side="right")

        list_wrap = ttk.Frame(self.exercises_panel, style="Panel.TFrame", padding=(6, 2))
        list_wrap.pack(fill="x")
        self.ex_listbox = tk.Listbox(
            list_wrap,
            height=9,
            exportselection=False,
            font=("Segoe UI", 9),
            borderwidth=0,
            highlightthickness=2,
        )
        self.ex_listbox.pack(side="left", fill="x", expand=True)
        ex_sb = ttk.Scrollbar(list_wrap, orient="vertical", command=self.ex_listbox.yview, style="App.Vertical.TScrollbar")
        ex_sb.pack(side="right", fill="y")
        self.ex_listbox.config(yscrollcommand=ex_sb.set)
        self.ex_listbox.bind("<<ListboxSelect>>", self._on_exercise_selected)

        self.ex_detail = tk.Text(
            self.exercises_panel,
            height=9,
            wrap="word",
            font=("Segoe UI", 9),
            borderwidth=0,
            highlightthickness=1,
            padx=8,
            pady=6,
            state="disabled",
        )
        self.ex_detail.pack(fill="both", expand=True, padx=6, pady=(4, 4))

        ex_actions = ttk.Frame(self.exercises_panel, style="Panel.TFrame", padding=(6, 2))
        ex_actions.pack(fill="x")
        ttk.Button(ex_actions, text="Cargar plantilla", style="Neutral.TButton", command=self.load_selected_exercise).pack(
            side="left", padx=(0, 4)
        )
        self.btn_ex_hint = ttk.Button(ex_actions, text="Pista", style="Neutral.TButton", command=self.show_next_hint)
        self.btn_ex_hint.pack(side="left", padx=(0, 4))
        self.btn_ex_check = ttk.Button(
            ex_actions, text="✔ Comprobar", style="Run.TButton", command=self.check_selected_exercise
        )
        self.btn_ex_check.pack(side="right")

        self.ex_results = tk.Text(
            self.exercises_panel,
            height=7,
            wrap="word",
            font=("Consolas", 9),
            borderwidth=0,
            highlightthickness=1,
            padx=8,
            pady=6,
            state="disabled",
        )
        self.ex_results.pack(fill="both", expand=False, padx=6, pady=(4, 6))

    def _build_inspector_panel(self) -> None:
        self.inspector_panel = ttk.Frame(self.right_paned, style="Panel.TFrame")

        header = ttk.Frame(self.inspector_panel, style="Panel.TFrame", padding=(8, 4, 8, 2))
        header.pack(fill="x")
        ttk.Label(header, text="INSPECTOR DE VARIABLES", style="PanelHeader.TLabel").pack(side="left")
        ttk.Button(header, text="✕", width=3, style="Neutral.TButton", command=self.toggle_inspector_panel).pack(side="right")

        self.stack_var = tk.StringVar(value="Pila: sin depuración activa")
        ttk.Label(self.inspector_panel, textvariable=self.stack_var, style="PanelHeader.TLabel", padding=(8, 0, 8, 4)).pack(
            fill="x"
        )

        tree_wrap = ttk.Frame(self.inspector_panel, style="Panel.TFrame", padding=(6, 0, 6, 6))
        tree_wrap.pack(expand=True, fill="both")
        tree_wrap.grid_rowconfigure(0, weight=1)
        tree_wrap.grid_columnconfigure(0, weight=1)

        self.vars_tree = ttk.Treeview(
            tree_wrap,
            columns=("name", "type", "value", "scope"),
            show="headings",
            style="App.Treeview",
            height=6,
        )
        self.vars_tree.heading("name", text="Variable")
        self.vars_tree.heading("type", text="Tipo")
        self.vars_tree.heading("value", text="Valor")
        self.vars_tree.heading("scope", text="Ámbito")
        self.vars_tree.column("name", width=110, anchor="w")
        self.vars_tree.column("type", width=70, anchor="w")
        self.vars_tree.column("value", width=170, anchor="w")
        self.vars_tree.column("scope", width=65, anchor="w")
        self.vars_tree.grid(row=0, column=0, sticky="nsew")

        v_sb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.vars_tree.yview, style="App.Vertical.TScrollbar")
        v_sb.grid(row=0, column=1, sticky="ns")
        self.vars_tree.configure(yscrollcommand=v_sb.set)

    def _bind_global_shortcuts(self) -> None:
        self.root.bind_all("<F5>", lambda _e: (self.run_code(), "break")[1])
        self.root.bind_all("<Control-Return>", lambda _e: (self.run_code(), "break")[1])
        self.root.bind_all("<Shift-F5>", lambda _e: (self.stop_code(), "break")[1])
        self.root.bind_all("<F6>", lambda _e: (self.toggle_panel_focus(), "break")[1])
        self.root.bind_all("<F8>", lambda _e: (self.debug_continue(), "break")[1])
        self.root.bind_all("<F9>", lambda _e: (self.toggle_current_breakpoint(), "break")[1])
        self.root.bind_all("<F10>", lambda _e: (self.debug_start_or_next(), "break")[1])
        self.root.bind_all("<F11>", lambda _e: (self.debug_step_into(), "break")[1])
        self.root.bind_all("<F1>", lambda _e: (self.show_shortcuts_dialog(), "break")[1])
        self.root.bind_all("<F2>", lambda _e: (self.show_cheatsheet_dialog(), "break")[1])

        self.root.bind_all("<Control-l>", lambda _e: (self.clear_console(), "break")[1])
        self.root.bind_all("<Control-L>", lambda _e: (self.clear_console(), "break")[1])
        self.root.bind_all("<Control-n>", lambda _e: (self.new_tab(), "break")[1])
        self.root.bind_all("<Control-N>", lambda _e: (self.new_tab(), "break")[1])
        self.root.bind_all("<Control-o>", lambda _e: (self.open_file(), "break")[1])
        self.root.bind_all("<Control-O>", lambda _e: (self.open_file(), "break")[1])
        self.root.bind_all("<Control-s>", lambda _e: (self.save_file(), "break")[1])
        self.root.bind_all("<Control-S>", lambda _e: (self.save_file(), "break")[1])
        self.root.bind_all("<Control-Shift-S>", lambda _e: (self.save_file_as(), "break")[1])
        self.root.bind_all("<Control-e>", lambda _e: (self.export_practice(), "break")[1])
        self.root.bind_all("<Control-Shift-E>", lambda _e: (self.toggle_exercises_panel(), "break")[1])
        self.root.bind_all("<Control-Shift-V>", lambda _e: (self.toggle_inspector_panel(), "break")[1])
        self.root.bind_all("<Control-w>", lambda _e: (self.close_current_tab(), "break")[1])
        self.root.bind_all("<Control-W>", lambda _e: (self.close_current_tab(), "break")[1])
        self.root.bind_all("<Control-g>", lambda _e: (self.goto_line_dialog(), "break")[1])
        self.root.bind_all("<Control-G>", lambda _e: (self.goto_line_dialog(), "break")[1])

        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.root.bind_all(seq, lambda _e: (self.adjust_zoom(1), "break")[1])
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.root.bind_all(seq, lambda _e: (self.adjust_zoom(-1), "break")[1])
        self.root.bind_all("<Control-0>", lambda _e: (self.reset_zoom(), "break")[1])
        self.root.bind_all("<Control-MouseWheel>", self._on_ctrl_wheel)

    # --- Paneles opcionales ---
    def toggle_exercises_panel(self) -> None:
        self.show_exercises_var.set(not self.show_exercises_var.get())
        self.sync_exercises_panel()

    def sync_exercises_panel(self) -> None:
        panes = [str(p) for p in self.paned.panes()]
        ex_id = str(self.exercises_panel)
        if self.show_exercises_var.get():
            if ex_id not in panes:
                self.paned.add(self.exercises_panel, before=self.editor_panel, minsize=330, width=350)
            self._refresh_exercises_list()
        else:
            if ex_id in panes:
                self.paned.forget(self.exercises_panel)

    def toggle_inspector_panel(self) -> None:
        self.show_inspector_var.set(not self.show_inspector_var.get())
        self.sync_inspector_panel()

    def sync_inspector_panel(self) -> None:
        panes = [str(p) for p in self.right_paned.panes()]
        insp_id = str(self.inspector_panel)
        if self.show_inspector_var.get():
            if insp_id not in panes:
                self.right_paned.add(self.inspector_panel, minsize=180, height=230)
        else:
            if insp_id in panes:
                self.right_paned.forget(self.inspector_panel)

    # --- Temas y tipografía ---
    def apply_theme(self, theme_name: str, *, persist: bool = True) -> None:
        self.theme_name = theme_name if theme_name in THEMES else "oscuro"
        self.theme = get_theme(self.theme_name)
        self.theme_var.set(self.theme_name)

        configure_ttk_styles(self.root, self.theme)
        self.root.configure(bg=self.theme["app_bg"])
        self.paned.configure(bg=self.theme["border"])
        self.right_paned.configure(bg=self.theme["border"])

        self.live_diag_frame.configure(bg=self.theme["error_line_bg"])
        self.live_diag_lbl.configure(bg=self.theme["error_line_bg"], fg=self.theme["error_fg"])

        self.ex_listbox.configure(
            bg=self.theme["console_bg"],
            fg=self.theme["editor_fg"],
            selectbackground=self.theme["selection_bg"],
            selectforeground=self.theme["selection_fg"],
            highlightbackground=self.theme["border"],
            highlightcolor=self.theme["focus_ring"],
        )
        for txt_w in (self.ex_detail, self.ex_results):
            txt_w.configure(
                bg=self.theme["console_bg"],
                fg=self.theme["editor_fg"],
                highlightbackground=self.theme["border"],
                highlightcolor=self.theme["focus_ring"],
            )
        self.ex_results.tag_configure("ok", foreground=self.theme["success_fg"])
        self.ex_results.tag_configure("fail", foreground=self.theme["error_fg"])
        self.ex_results.tag_configure("hint", foreground=self.theme["warning_fg"])

        self.console.configure(
            bg=self.theme["console_bg"],
            fg=self.theme["console_fg"],
            insertbackground=self.theme["cursor"],
            selectbackground=self.theme["selection_bg"],
            selectforeground=self.theme["selection_fg"],
            highlightthickness=self.theme.get("focus_thickness", 2),
            highlightbackground=self.theme["border"],
            highlightcolor=self.theme["focus_ring"],
        )
        self.console.tag_configure("error", foreground=self.theme["error_fg"])
        self.console.tag_configure("warning", foreground=self.theme["warning_fg"])
        self.console.tag_configure("success", foreground=self.theme["success_fg"])
        self.console.tag_configure("system", foreground=self.theme["system_fg"])
        self.console.tag_configure(
            "stdin", foreground=self.theme["stdin_fg"], font=(self._font_family, self._font_size, "bold")
        )
        self.console.tag_configure("help_box", foreground=self.theme["tooltip_fg"], background=self.theme["tooltip_bg"])

        for ed in self.editors.values():
            ed.apply_theme(self.theme)

        if persist:
            self.settings.set("theme", self.theme_name)

    def adjust_zoom(self, delta: int) -> None:
        new_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, self._font_size + delta))
        if new_size != self._font_size:
            self._font_size = new_size
            self._apply_font_change()

    def reset_zoom(self) -> None:
        self._font_size = DEFAULT_FONT_SIZE
        self._apply_font_change()

    def _on_ctrl_wheel(self, event) -> str:
        if event.delta > 0:
            self.adjust_zoom(1)
        elif event.delta < 0:
            self.adjust_zoom(-1)
        return "break"

    def _apply_font_change(self) -> None:
        self.code_font.configure(family=self._font_family, size=self._font_size)
        self.console.tag_configure("stdin", font=(self._font_family, self._font_size, "bold"))
        pct = round((self._font_size / DEFAULT_FONT_SIZE) * 100)
        self.status_zoom_var.set(f"{pct} %")
        self.settings.set("font_family", self._font_family, persist=False)
        self.settings.set("font_size", self._font_size, persist=True)

    # --- Pestañas, archivos, plantillas y exportación ---
    def get_active_editor(self) -> Optional[CodeEditor]:
        tab_id = self.notebook.select()
        return self.editors.get(tab_id)

    def new_tab(self, *, content: str = "", filepath: Optional[str] = None, dirty: bool = False) -> CodeEditor:
        editor = CodeEditor(
            self.notebook,
            theme=self.theme,
            code_font=self.code_font,
            on_cursor_change=self._on_editor_cursor_change,
            on_modified_change=lambda _d: self._refresh_tab_titles(),
            on_live_diagnostic=self._on_live_diagnostic,
        )
        editor.filepath = filepath

        if filepath:
            title = os.path.basename(filepath)
        else:
            self._untitled_counter += 1
            title = f"Sin título {self._untitled_counter}.py"

        self.notebook.add(editor, text=title)
        tab_id = str(editor)
        self.editors[tab_id] = editor
        self.notebook.select(editor)

        if content:
            editor.set_code(content, mark_dirty=dirty)
        else:
            editor.set_dirty(dirty)

        self._refresh_tab_titles()
        editor.text.focus_set()
        return editor

    def new_from_template(self, template: dict[str, str]) -> None:
        active = self.get_active_editor()
        if active and not active.filepath and not active.is_dirty and not active.get_code().strip():
            active.set_code(template["code"], mark_dirty=True)
            active.text.focus_set()
        else:
            self.new_tab(content=template["code"], dirty=True)
        self.status_state_var.set(f"✔ Plantilla cargada: {template['name']}")

    def export_practice(self) -> None:
        ed = self.get_active_editor()
        if not ed:
            return
        tab_name = os.path.basename(ed.filepath) if ed.filepath else self.notebook.tab(str(ed), "text").rstrip(" ●")
        base_stem = os.path.splitext(tab_name)[0] or "practica"
        path = filedialog.asksaveasfilename(
            title="Exportar práctica (código + salida)",
            defaultextension=".md",
            initialfile=f"{base_stem}_entrega.md",
            filetypes=[("Markdown (*.md)", "*.md"), ("Texto plano (*.txt)", "*.txt")],
        )
        if not path:
            return
        fmt = "txt" if path.lower().endswith(".txt") else "md"
        content = format_practice_export(
            title=tab_name,
            code=ed.get_code(),
            console_output=self.console.get("1.0", "end-1c"),
            interpreter_label=self.interpreter_label,
            fmt=fmt,
            exercise_report=self._last_exercise_report,
        )
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            self.status_state_var.set(f"✔ Práctica exportada: {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Error al exportar", f"No se pudo escribir el archivo:\n{exc}")

    def _refresh_tab_titles(self) -> None:
        for tab_id, ed in self.editors.items():
            if ed.filepath:
                base = os.path.basename(ed.filepath)
            else:
                current_text = self.notebook.tab(tab_id, "text").rstrip(" ●")
                base = current_text or "Sin título.py"
            display = f"{base} ●" if ed.is_dirty else base
            self.notebook.tab(tab_id, text=display)

        active = self.get_active_editor()
        if active:
            name = (
                os.path.basename(active.filepath)
                if active.filepath
                else self.notebook.tab(str(active), "text").rstrip(" ●")
            )
            star = " *" if active.is_dirty else ""
            self.root.title(f"{name}{star} — PyPractice v{APP_VERSION}")
        else:
            self.root.title(f"PyPractice v{APP_VERSION}")

    def _on_tab_changed(self, _event=None) -> None:
        ed = self.get_active_editor()
        if ed:
            line, col = ed.get_cursor_position()
            self.status_pos_var.set(f"Lín {line}, Col {col}")
            self._on_live_diagnostic(ed.check_syntax())
        self._refresh_tab_titles()

    def _on_tab_middle_click(self, event) -> None:
        try:
            idx = self.notebook.index(f"@{event.x},{event.y}")
            tab_id = self.notebook.tabs()[idx]
            self.close_tab(tab_id)
        except Exception:
            pass

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Abrir archivo Python",
            filetypes=[("Archivos Python", "*.py"), ("Todos los archivos", "*.*")],
        )
        if path:
            self.open_file_path(path)

    def open_file_path(self, path: str) -> None:
        abs_path = os.path.abspath(path)
        if not os.path.isfile(abs_path):
            messagebox.showerror("Archivo no encontrado", f"No existe el archivo:\n{abs_path}")
            return

        for tab_id, ed in self.editors.items():
            if ed.filepath and os.path.normcase(os.path.abspath(ed.filepath)) == os.path.normcase(abs_path):
                self.notebook.select(tab_id)
                ed.text.focus_set()
                return

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as exc:
            messagebox.showerror("Error al abrir", f"No se pudo leer el archivo:\n{exc}")
            return

        active = self.get_active_editor()
        if active and not active.filepath and not active.is_dirty and not active.get_code().strip():
            active.filepath = abs_path
            active.set_code(content, mark_dirty=False)
            self._refresh_tab_titles()
            active.text.focus_set()
        else:
            self.new_tab(content=content, filepath=abs_path, dirty=False)

        self.settings.add_recent_file(abs_path)
        self._rebuild_recent_menu()

    def save_file(self, editor: Optional[CodeEditor] = None) -> bool:
        ed = editor or self.get_active_editor()
        if not ed:
            return False
        if not ed.filepath:
            return self.save_file_as(ed)
        return self._write_editor_to_path(ed, ed.filepath)

    def save_file_as(self, editor: Optional[CodeEditor] = None) -> bool:
        ed = editor or self.get_active_editor()
        if not ed:
            return False
        initial = os.path.basename(ed.filepath) if ed.filepath else "practica.py"
        path = filedialog.asksaveasfilename(
            title="Guardar archivo Python",
            defaultextension=".py",
            initialfile=initial,
            filetypes=[("Archivos Python", "*.py"), ("Todos los archivos", "*.*")],
        )
        if not path:
            return False
        return self._write_editor_to_path(ed, path)

    def _write_editor_to_path(self, ed: CodeEditor, path: str) -> bool:
        try:
            abs_path = os.path.abspath(path)
            with open(abs_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(ed.get_code())
            ed.filepath = abs_path
            ed.set_dirty(False)
            self._refresh_tab_titles()
            self.settings.add_recent_file(abs_path)
            self._rebuild_recent_menu()
            self.status_state_var.set(f"✔ Guardado: {os.path.basename(abs_path)}")
            return True
        except Exception as exc:
            messagebox.showerror("Error al guardar", f"No se pudo guardar el archivo:\n{exc}")
            return False

    def close_current_tab(self) -> None:
        tab_id = self.notebook.select()
        if tab_id:
            self.close_tab(tab_id)

    def close_tab(self, tab_id: str) -> bool:
        ed = self.editors.get(tab_id)
        if not ed:
            return True

        if ed.is_dirty:
            name = os.path.basename(ed.filepath) if ed.filepath else self.notebook.tab(tab_id, "text").rstrip(" ●")
            resp = messagebox.askyesnocancel(
                "Cambios sin guardar",
                f"¿Deseas guardar los cambios en '{name}' antes de cerrar?",
            )
            if resp is None:
                return False
            if resp is True and not self.save_file(ed):
                return False

        self.notebook.forget(tab_id)
        del self.editors[tab_id]
        ed.destroy()

        if not self.editors:
            self.new_tab()
        else:
            self._refresh_tab_titles()
        return True

    # --- Recuperación automática ---
    def _restore_or_create_initial_tab(self) -> None:
        recovered = self.settings.load_recovery_session()
        if recovered and any(t.get("content", "").strip() for t in recovered):
            if messagebox.askyesno(
                "Recuperación automática",
                "Se detectaron borradores de una sesión anterior que no se cerró correctamente.\n¿Deseas recuperarlos?",
            ):
                for item in recovered:
                    fp = item.get("filepath")
                    if fp and not os.path.isfile(fp):
                        fp = None
                    self.new_tab(content=item.get("content", ""), filepath=fp, dirty=bool(item.get("dirty", True)))
                self.settings.clear_recovery_session()
                return
            self.settings.clear_recovery_session()
        self.new_tab()

    def _schedule_autosave(self) -> None:
        self._save_recovery_snapshot()
        self._autosave_job = self.root.after(AUTOSAVE_INTERVAL_MS, self._schedule_autosave)

    def _save_recovery_snapshot(self) -> None:
        tabs_state = []
        has_dirty = False
        for ed in self.editors.values():
            if ed.is_dirty:
                has_dirty = True
            tabs_state.append({"filepath": ed.filepath, "content": ed.get_code(), "dirty": ed.is_dirty})
        if has_dirty:
            self.settings.save_recovery_session(tabs_state)
        else:
            self.settings.clear_recovery_session()

    # --- Biblioteca de ejercicios ---
    def _refresh_exercises_list(self) -> None:
        completed = set(self.settings.get("completed_exercises", []))
        self.ex_progress_var.set(f"{len(completed)}/{len(EXERCISES)} completados")

        current_idx = 0
        for i, ex in enumerate(EXERCISES):
            if ex.id == self._selected_exercise.id:
                current_idx = i
                break

        self.ex_listbox.delete(0, tk.END)
        for ex in EXERCISES:
            prefix = "✔ " if ex.id in completed else "○ "
            self.ex_listbox.insert(tk.END, f"{prefix}{ex.title}")
        self.ex_listbox.selection_set(current_idx)

    def _on_exercise_selected(self, _event=None) -> None:
        sel = self.ex_listbox.curselection()
        if not sel:
            return
        self._selected_exercise = EXERCISES[sel[0]]
        self._hint_index = 0
        self._display_selected_exercise()

    def _display_selected_exercise(self) -> None:
        ex = self._selected_exercise
        self.ex_detail.config(state="normal")
        self.ex_detail.delete("1.0", tk.END)
        self.ex_detail.insert("1.0", f"{ex.level}\n{ex.title}\n\n{ex.description}")
        self.ex_detail.config(state="disabled")
        self.btn_ex_hint.config(text=f"Pista (0/{len(ex.hints)})")

    def load_selected_exercise(self) -> None:
        ex = self._selected_exercise
        active = self.get_active_editor()
        if active and not active.filepath and not active.is_dirty and not active.get_code().strip():
            active.set_code(ex.starter_code, mark_dirty=True)
            active.text.focus_set()
        else:
            self.new_tab(content=ex.starter_code, dirty=True)
        self._set_exercise_results_text(f"Plantilla cargada para: {ex.title}\nEscribe tu solución y pulsa '✔ Comprobar'.", "hint")

    def show_next_hint(self) -> None:
        ex = self._selected_exercise
        if not ex.hints:
            return
        idx = min(self._hint_index, len(ex.hints) - 1)
        hint_text = ex.hints[idx]
        self._hint_index = min(self._hint_index + 1, len(ex.hints))
        self.btn_ex_hint.config(text=f"Pista ({self._hint_index}/{len(ex.hints)})")
        self._set_exercise_results_text(f"💡 Pista {idx + 1}/{len(ex.hints)}:\n{hint_text}", "hint")

    def _set_exercise_results_text(self, text: str, tag: Optional[str] = None) -> None:
        self.ex_results.config(state="normal")
        self.ex_results.delete("1.0", tk.END)
        self.ex_results.insert("1.0", text, tag)
        self.ex_results.config(state="disabled")

    def check_selected_exercise(self) -> None:
        ed = self.get_active_editor()
        if not ed:
            return
        code = ed.get_code()
        diag = ed.check_syntax()
        if diag is not None:
            self._set_exercise_results_text(f"✖ {diag.title}\n{diag.explanation}", "fail")
            return
        if self.safe_mode_var.get():
            warnings = inspect_code_safety(code)
            if warnings and not self._confirm_safety_warnings(warnings):
                self._set_exercise_results_text("⚠ Comprobación cancelada por el Modo seguro.", "hint")
                return

        ex = self._selected_exercise
        use_isolated = self.isolated_mode_var.get()
        interp_path = self.interpreter_path
        self.btn_ex_check.state(["disabled"])
        self._set_exercise_results_text(f"Evaluando '{ex.title}'...", None)

        def worker():
            report = evaluate_exercise(
                ex,
                code,
                interpreter_path=interp_path,
                use_isolated_mode=use_isolated,
                timeout_seconds=5,
            )
            report["exercise_id"] = ex.id
            report["exercise_title"] = ex.title
            self._eval_queue.put(report)

        threading.Thread(target=worker, daemon=True).start()

    def _render_exercise_report(self, report: dict[str, Any]) -> None:
        self.btn_ex_check.state(["!disabled"])
        self._last_exercise_report = report

        self.ex_results.config(state="normal")
        self.ex_results.delete("1.0", tk.END)

        fatal = report.get("fatal")
        if fatal:
            self.ex_results.insert(tk.END, f"✖ {fatal}\n", "fail")
        else:
            passed = report.get("passed", 0)
            total = report.get("total", 0)
            header_tag = "ok" if report.get("ok") else "fail"
            icon = "✔" if report.get("ok") else "✖"
            self.ex_results.insert(tk.END, f"{icon} Resultado: {passed}/{total} pruebas superadas\n", header_tag)
            for item in report.get("results", []):
                if item.get("ok"):
                    self.ex_results.insert(tk.END, f"  ✔ {item['label']}\n", "ok")
                elif item.get("error"):
                    self.ex_results.insert(tk.END, f"  ✖ {item['label']} -> {item['error']}\n", "fail")
                else:
                    self.ex_results.insert(
                        tk.END,
                        f"  ✖ {item['label']}\n     Esperado: {item['expected']} | Obtenido: {item['actual']}\n",
                        "fail",
                    )

            if report.get("ok"):
                self.settings.mark_exercise_completed(report["exercise_id"])
                self._refresh_exercises_list()
                self.status_state_var.set(f"✔ ¡Ejercicio completado: {report['exercise_title']}!")
                self._maybe_bell()

        self.ex_results.config(state="disabled")

    # --- Eventos auxiliares del editor ---
    def _emit_editor(self, virtual_event: str) -> None:
        ed = self.get_active_editor()
        if ed:
            ed.text.event_generate(virtual_event)

    def _show_search(self, replace: bool) -> None:
        ed = self.get_active_editor()
        if ed:
            ed.show_search_bar(replace=replace)

    def _toggle_comment(self) -> None:
        ed = self.get_active_editor()
        if ed:
            ed.toggle_comment()

    def toggle_current_breakpoint(self) -> None:
        ed = self.get_active_editor()
        if ed:
            added = ed.toggle_breakpoint()
            line, _ = ed.get_cursor_position()
            self.status_state_var.set(f"● Punto de ruptura {'añadido' if added else 'quitado'} en línea {line}")

    def goto_line_dialog(self) -> None:
        ed = self.get_active_editor()
        if not ed:
            return
        line = simpledialog.askinteger("Ir a la línea", "Número de línea:", parent=self.root, minvalue=1)
        if line is not None:
            ed.goto_line(line)

    def _on_editor_cursor_change(self, line: int, col: int) -> None:
        self.status_pos_var.set(f"Lín {line}, Col {col}")

    def _on_live_diagnostic(self, diag: Optional[Diagnostic]) -> None:
        self._current_live_diag = diag
        if diag is None:
            self.live_diag_frame.pack_forget()
            return
        self.live_diag_lbl.config(text=f"✖ {diag.title}")
        if not self.live_diag_frame.winfo_ismapped():
            self.live_diag_frame.pack(fill="x", padx=6, pady=(2, 2), before=self.notebook)

    def _show_live_diagnostic_details(self) -> None:
        if self._current_live_diag:
            self._show_diagnostic_dialog(self._current_live_diag)

    def toggle_panel_focus(self) -> None:
        focused = self.root.focus_get()
        ed = self.get_active_editor()
        if focused == self.console and ed:
            ed.text.focus_set()
        else:
            self.console.focus_set()

    # --- Ejecución, depuración paso a paso y consola ---
    def _get_selected_timeout(self) -> int:
        lbl = self.timeout_label_var.get()
        for opt_lbl, sec in TIMEOUT_OPTIONS:
            if opt_lbl == lbl:
                return sec
        return 60

    def _on_timeout_changed(self, _event=None) -> None:
        self.settings.set("timeout_seconds", self._get_selected_timeout())

    def _on_safe_mode_changed(self) -> None:
        self.settings.set("safe_mode", self.safe_mode_var.get())

    def _on_isolated_mode_changed(self) -> None:
        self.settings.set("use_isolated_mode", self.isolated_mode_var.get())

    def _update_args_badge(self) -> None:
        if self.script_args.strip():
            self.args_badge.config(text=f"argv: {self.script_args.strip()}")
        else:
            self.args_badge.config(text="")

    def _refresh_interpreter_info(self) -> None:
        exe, version_or_err = resolve_interpreter(self.interpreter_path)
        if exe:
            short_dir = os.path.basename(os.path.dirname(exe))
            self.interpreter_label = f"{version_or_err} ({short_dir})"
        else:
            self.interpreter_label = "⚠ Python no encontrado"
        self.status_python_var.set(self.interpreter_label)

    def _update_run_controls(self, *, running: bool, debugging: bool = False) -> None:
        active = running or debugging
        self.stop_btn.state(["!disabled"] if active else ["disabled"])
        self.run_btn.config(text="↻ Reiniciar" if running else "▶ Ejecutar")
        self.debug_btn.config(text="⏭ Siguiente" if debugging else "🐞 Paso a paso")

        if debugging:
            if not self.debug_bar.winfo_ismapped():
                self.debug_bar.pack(fill="x", padx=6, pady=(2, 2), before=self.console.master)
        else:
            self.debug_bar.pack_forget()
            self._clear_all_debug_lines()

    def _clear_all_debug_lines(self) -> None:
        for ed in self.editors.values():
            ed.set_debug_line(None)

    def _validate_before_launch(self, ed: CodeEditor) -> Optional[str]:
        code = ed.get_code()
        if not code.strip():
            self.console_write("ℹ El editor está vacío. Escribe algo de Python y pulsa F5 o F10.\n", "system")
            self.status_state_var.set("● Listo (editor vacío)")
            return None

        diag = ed.check_syntax()
        if diag is not None:
            self.console_write(f"✖ No se pudo ejecutar. {diag.title}\n", "error")
            self._insert_console_diagnostic_controls(diag)
            self.status_state_var.set(f"✖ Error de sintaxis (Línea {diag.line or '?'})")
            self._maybe_bell()
            return None

        if self.safe_mode_var.get():
            warnings = inspect_code_safety(code)
            if warnings and not self._confirm_safety_warnings(warnings):
                self.console_write("⚠ Ejecución cancelada por el Modo seguro.\n", "warning")
                self.status_state_var.set("⚠ Cancelado por Modo seguro")
                return None

        return code

    def run_code(self) -> None:
        ed = self.get_active_editor()
        if not ed:
            return

        self.runner.stop(emit_event=False)
        self.debugger.stop(emit_event=False)
        self._clear_all_debug_lines()
        self._running_editor = ed
        self.clear_console()

        code = self._validate_before_launch(ed)
        if code is None:
            self._update_run_controls(running=False, debugging=False)
            return

        timeout_sec = self._get_selected_timeout()
        limit_txt = f" | Límite: {timeout_sec} s" if timeout_sec > 0 else " | Sin límite"
        self.console_write(f"--- Iniciando ejecución{limit_txt} ---\n", "system")
        self.status_state_var.set("▶ Ejecutando...")
        self._update_run_controls(running=True, debugging=False)

        ok, err = self.runner.run_code(
            code,
            interpreter_path=self.interpreter_path,
            script_args=self.script_args,
            timeout_seconds=timeout_sec,
            use_isolated_mode=self.isolated_mode_var.get(),
        )
        if not ok and err:
            self.console_write(err + "\n", "error")
            self.status_state_var.set("✖ Error al iniciar")
            self._update_run_controls(running=False, debugging=False)
            self._maybe_bell()

    def debug_start_or_next(self) -> None:
        if self.debugger.is_running:
            self.debug_next()
            return

        ed = self.get_active_editor()
        if not ed:
            return

        self.runner.stop(emit_event=False)
        self.debugger.stop(emit_event=False)
        self._clear_all_debug_lines()
        self._running_editor = ed
        self._prev_debug_vars.clear()
        self.clear_console()

        code = self._validate_before_launch(ed)
        if code is None:
            self._update_run_controls(running=False, debugging=False)
            return

        # Abrir automáticamente el Inspector de variables
        if not self.show_inspector_var.get():
            self.show_inspector_var.set(True)
            self.sync_inspector_panel()
        self._clear_variables_tree()

        self.console_write("--- 🐞 Iniciando depuración paso a paso (F10: siguiente · F11: entrar · F8: continuar) ---\n", "system")
        self.status_state_var.set("🐞 Depurando paso a paso...")
        self._update_run_controls(running=False, debugging=True)

        ok, err = self.debugger.start(
            code,
            breakpoints=set(ed.breakpoints),
            interpreter_path=self.interpreter_path,
            script_args=self.script_args,
            use_isolated_mode=self.isolated_mode_var.get(),
        )
        if not ok and err:
            self.console_write(err + "\n", "error")
            self.status_state_var.set("✖ Error al iniciar depurador")
            self._update_run_controls(running=False, debugging=False)

    def debug_next(self) -> None:
        if self.debugger.is_running and self.debugger.paused:
            self.debugger.send_command("next")

    def debug_step_into(self) -> None:
        if self.debugger.is_running and self.debugger.paused:
            self.debugger.send_command("step")

    def debug_continue(self) -> None:
        if self.debugger.is_running and self.debugger.paused:
            self.debugger.send_command("continue")

    def stop_code(self) -> None:
        if self.runner.is_running:
            self.runner.stop(reason="stopped", emit_event=True)
            self.status_state_var.set("■ Deteniendo...")
        if self.debugger.is_running:
            self.debugger.stop(reason="stopped", emit_event=True)
            self.status_state_var.set("■ Deteniendo depuración...")

    def _clear_variables_tree(self) -> None:
        for item in self.vars_tree.get_children():
            self.vars_tree.delete(item)
        self.stack_var.set("Pila: esperando pausa...")

    def _handle_debug_event(self, ev: dict) -> None:
        etype = ev.get("event")
        if etype == "paused":
            line = ev.get("line")
            fn = ev.get("function", "programa principal")
            self.debug_loc_var.set(f"➤ Línea {line} · {fn}")
            self.status_state_var.set(f"🐞 En pausa · Línea {line} ({fn})")

            if self._running_editor and self._running_editor.winfo_exists():
                self.notebook.select(self._running_editor)
                self._running_editor.set_debug_line(line)

            stack = ev.get("stack", [])
            if stack:
                chain = " → ".join(f"{s['function']} (L{s['line']})" for s in reversed(stack))
                self.stack_var.set(f"Pila: {chain}")

            # Actualizar tabla de variables marcando cambios con ★
            for item in self.vars_tree.get_children():
                self.vars_tree.delete(item)
            new_snapshot: dict[tuple[str, str], str] = {}
            for var in ev.get("variables", []):
                key = (var["scope"], var["name"])
                val = var["value"]
                changed = key in self._prev_debug_vars and self._prev_debug_vars[key] != val
                is_new = bool(self._prev_debug_vars) and key not in self._prev_debug_vars
                display_name = f"★ {var['name']}" if (changed or is_new) else var["name"]
                self.vars_tree.insert("", tk.END, values=(display_name, var["type"], val, var["scope"]))
                new_snapshot[key] = val
            self._prev_debug_vars = new_snapshot

        elif etype == "input_wait":
            self.debug_loc_var.set("⌨ Esperando entrada en consola...")
            self.status_state_var.set("⌨ Esperando input() en la consola...")
            self.console.focus_set()
            self.console.mark_set("insert", "end")

        elif etype == "exception":
            line = ev.get("line")
            self.console_write(f"\n⚠ Excepción en línea {line}: {ev.get('type')}: {ev.get('message')}\n", "warning")

    def clear_console(self) -> None:
        self.console.delete("1.0", tk.END)
        self.console.mark_set("input_start", "1.0")

    def copy_console_output(self) -> None:
        content = self.console.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status_state_var.set("✔ Salida de consola copiada al portapapeles")

    def console_write(self, text: str, tag: Optional[str] = None) -> None:
        if not text:
            return
        self.console.mark_gravity("input_start", "right")
        self.console.insert("input_start", text, tag)
        self.console.mark_gravity("input_start", "left")
        self.console.see("end")

    def _insert_console_diagnostic_controls(self, diag: Diagnostic) -> None:
        row = ttk.Frame(self.console, style="Panel.TFrame")

        btn_help = ttk.Button(
            row,
            text="[ i ] Ver explicación",
            style="Neutral.TButton",
            command=lambda d=diag: self._show_diagnostic_dialog(d),
        )
        btn_help.pack(side="left", padx=(0, 6))
        AccessibleToolTip(btn_help, f"{diag.explanation}\n\nConsejo: {diag.tip}", lambda: self.theme)

        if diag.line is not None:
            btn_goto = ttk.Button(
                row,
                text=f"↪ Ir a la línea {diag.line}",
                style="Neutral.TButton",
                command=lambda ln=diag.line: self._jump_to_error_line(ln),
            )
            btn_goto.pack(side="left")

        self.console.mark_gravity("input_start", "right")
        self.console.window_create("input_start", window=row)
        self.console.mark_gravity("input_start", "left")
        self.console_write(f"\n  ℹ {diag.explanation}\n  💡 {diag.tip}\n", "system")

    def _jump_to_error_line(self, line: int) -> None:
        ed = (
            self._running_editor
            if (self._running_editor and self._running_editor.winfo_exists())
            else self.get_active_editor()
        )
        if ed:
            self.notebook.select(ed)
            ed.goto_line(line)

    # --- Protección de la consola interactiva ---
    def _console_accepts_input(self) -> bool:
        return self.runner.is_running or self.debugger.waiting_for_input

    def on_console_enter(self, _event) -> Optional[str]:
        if self._console_accepts_input():
            text_to_send = self.console.get("input_start", "end-1c")
            self.console.tag_add("stdin", "input_start", "end-1c")
            if self.debugger.waiting_for_input:
                self.debugger.send_input(text_to_send)
            else:
                self.runner.send_input(text_to_send)
            self.console.insert("end", "\n")
            self.console.mark_set("input_start", "end-1c")
            self.console.mark_set("insert", "end")
            self.console.see("end")
            return "break"
        return "break"

    def on_console_backspace(self, _event) -> Optional[str]:
        if self._console_selection_touches_protected():
            return "break"
        if self.console.compare("insert", "<=", "input_start"):
            return "break"
        return None

    def on_console_delete(self, _event) -> Optional[str]:
        if self._console_selection_touches_protected():
            return "break"
        if self.console.compare("insert", "<", "input_start"):
            return "break"
        return None

    def on_console_paste(self, _event) -> str:
        if not self._console_accepts_input():
            return "break"
        try:
            clip = self.root.clipboard_get()
        except tk.TclError:
            return "break"
        if self.console.compare("insert", "<", "input_start"):
            self.console.mark_set("insert", "end")
        self.console.insert("insert", clip, "stdin")
        self.console.see("end")
        return "break"

    def on_console_key(self, event) -> Optional[str]:
        nav_keys = {
            "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
            "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
            "Tab", "ISO_Left_Tab", "Escape", "F1", "F2", "F5", "F6", "F8", "F9", "F10", "F11",
        }
        if event.keysym in nav_keys:
            return None
        if event.state & 0x4:
            return None

        if not self._console_accepts_input():
            if event.char:
                return "break"
            return None

        if event.char and ord(event.char) >= 32:
            if self._console_selection_touches_protected():
                self.console.tag_remove("sel", "1.0", tk.END)
            if self.console.compare("insert", "<", "input_start"):
                self.console.mark_set("insert", "end")
            self.console.insert("insert", event.char, "stdin")
            self.console.see("end")
            return "break"
        return None

    def _console_selection_touches_protected(self) -> bool:
        try:
            sel_start = self.console.index("sel.first")
            return self.console.compare(sel_start, "<", "input_start")
        except tk.TclError:
            return False

    # --- Procesamiento de colas (runner, depurador y evaluador de ejercicios) ---
    def process_queue(self) -> None:
        processed = 0
        while processed < 150:
            try:
                event_type, payload, _run_id = self.runner.output_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if event_type == "stdout":
                self.console_write(payload)
            elif event_type == "stderr":
                cleaned = sanitize_traceback_text(payload, self.runner.current_script_path)
                self.console_write(cleaned, "error")
            elif event_type == "system":
                self.console_write(payload, "warning")
            elif event_type == "exit":
                self._handle_process_exit(payload, from_debugger=False)

        dbg_processed = 0
        while dbg_processed < 100:
            try:
                event_type, payload, _run_id = self.debugger.event_queue.get_nowait()
            except queue.Empty:
                break
            dbg_processed += 1
            if event_type == "stdout":
                self.console_write(payload)
            elif event_type == "stderr":
                cleaned = sanitize_traceback_text(payload, self.debugger.current_script_path)
                self.console_write(cleaned, "error")
            elif event_type == "system":
                self.console_write(payload, "warning")
            elif event_type == "debug":
                self._handle_debug_event(payload)
            elif event_type == "exit":
                self._handle_process_exit(payload, from_debugger=True)

        while True:
            try:
                report = self._eval_queue.get_nowait()
            except queue.Empty:
                break
            self._render_exercise_report(report)

        self._queue_job = self.root.after(35, self.process_queue)

    def _handle_process_exit(self, info: dict, *, from_debugger: bool = False) -> None:
        self._update_run_controls(running=False, debugging=False)
        if from_debugger:
            self.stack_var.set("Pila: depuración finalizada")

        reason = info.get("reason", "finished")
        rc = info.get("returncode")
        elapsed = info.get("elapsed", 0.0)
        stderr_full = info.get("stderr_full", "")
        script_path = info.get("script_path")
        label = "Depuración" if from_debugger else "Ejecución"

        if reason == "stopped":
            self.console_write(f"\n--- ■ {label} detenida por el usuario ({elapsed:.2f} s) ---\n", "warning")
            self.status_state_var.set(f"■ Detenido · {elapsed:.2f} s")
            return

        if reason == "timeout":
            t_sec = info.get("timeout", 0)
            self.console_write(
                f"\n--- ⏱ Tiempo límite excedido ({t_sec} s). Posible bucle infinito o espera prolongada. ---\n",
                "warning",
            )
            self.status_state_var.set(f"⏱ Límite excedido ({t_sec} s)")
            self._maybe_bell()
            return

        if reason == "output_limit":
            self.status_state_var.set(f"⚠ Salida truncada · {elapsed:.2f} s")
            self._maybe_bell()
            return

        if rc == 0:
            self.console_write(f"\n--- ✔ {label} finalizada correctamente (código 0 · {elapsed:.2f} s) ---\n", "success")
            self.status_state_var.set(f"✔ Finalizado (0) · {elapsed:.2f} s")
        else:
            self.console_write(f"\n--- ✖ {label} finalizada con error (código {rc} · {elapsed:.2f} s) ---\n", "error")
            self.status_state_var.set(f"✖ Error ({rc}) · {elapsed:.2f} s")

            diag = analyze_runtime_stderr(stderr_full, script_path)
            if diag is not None:
                if diag.line is not None and self._running_editor and self._running_editor.winfo_exists():
                    self._running_editor.set_error_line(diag.line)
                self.console_write(f"✖ Diagnóstico: {diag.title}\n", "error")
                self._insert_console_diagnostic_controls(diag)
            self._maybe_bell()

    def _maybe_bell(self) -> None:
        if self.audio_feedback_var.get():
            try:
                self.root.bell()
            except Exception:
                pass

    # --- Diálogos modales accesibles ---
    def _create_modal(self, title: str, width: int = 540, height: int = 380) -> tk.Toplevel:
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.configure(bg=self.theme["app_bg"])
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.minsize(420, 260)

        x = self.root.winfo_rootx() + max(20, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(20, (self.root.winfo_height() - height) // 2)
        dlg.geometry(f"{width}x{height}+{x}+{y}")
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        return dlg

    def _confirm_safety_warnings(self, warnings: list[SafetyWarning]) -> bool:
        dlg = self._create_modal("Modo seguro — Confirmación requerida", 600, 400)
        result = {"proceed": False}

        frame = ttk.Frame(dlg, style="App.TFrame", padding=14)
        frame.pack(expand=True, fill="both")

        tk.Label(
            frame,
            text="⚠ El análisis AST detectó operaciones potencialmente sensibles en tu código:",
            bg=self.theme["app_bg"],
            fg=self.theme["warning_fg"],
            font=("Segoe UI", 10, "bold"),
            anchor="w",
            justify="left",
            wraplength=550,
        ).pack(fill="x", pady=(0, 8))

        box = tk.Text(
            frame,
            height=11,
            bg=self.theme["panel_bg"],
            fg=self.theme["editor_fg"],
            font=("Consolas", 10),
            wrap="word",
            borderwidth=1,
            relief="solid",
            highlightthickness=2,
            highlightcolor=self.theme["focus_ring"],
        )
        box.pack(expand=True, fill="both", pady=(0, 10))
        for w in warnings:
            badge = "[ALTO]    " if w.severity == "alto" else "[MODERADO]"
            box.insert(tk.END, f"{badge} Línea {w.line} · {w.symbol}\n    {w.reason}\n\n")
        box.config(state="disabled")

        tk.Label(
            frame,
            text="Si confías en este código (o lo escribiste tú mismo), puedes continuar.",
            bg=self.theme["app_bg"],
            fg=self.theme["muted_fg"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        btns = ttk.Frame(frame, style="App.TFrame")
        btns.pack(fill="x")

        def on_cancel():
            result["proceed"] = False
            dlg.destroy()

        def on_proceed():
            result["proceed"] = True
            dlg.destroy()

        btn_cancel = ttk.Button(btns, text="Cancelar (Seguro)", style="Run.TButton", command=on_cancel)
        btn_cancel.pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Ejecutar de todos modos", style="Stop.TButton", command=on_proceed).pack(side="right")

        btn_cancel.focus_set()
        self.root.wait_window(dlg)
        return result["proceed"]

    def _show_diagnostic_dialog(self, diag: Diagnostic) -> None:
        dlg = self._create_modal("Explicación del error", 540, 300)
        frame = ttk.Frame(dlg, style="App.TFrame", padding=16)
        frame.pack(expand=True, fill="both")

        tk.Label(
            frame,
            text=f"✖ {diag.title}",
            bg=self.theme["app_bg"],
            fg=self.theme["error_fg"],
            font=("Segoe UI", 11, "bold"),
            wraplength=500,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        tk.Label(
            frame,
            text=f"Qué significa:\n{diag.explanation}",
            bg=self.theme["app_bg"],
            fg=self.theme["editor_fg"],
            font=("Segoe UI", 10),
            wraplength=500,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        tk.Label(
            frame,
            text=f"Cómo solucionarlo:\n{diag.tip}",
            bg=self.theme["app_bg"],
            fg=self.theme["success_fg"],
            font=("Segoe UI", 10),
            wraplength=500,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(0, 14))

        btns = ttk.Frame(frame, style="App.TFrame")
        btns.pack(side="bottom", fill="x")
        if diag.line is not None:
            ttk.Button(
                btns,
                text=f"↪ Ir a la línea {diag.line}",
                style="Run.TButton",
                command=lambda: (dlg.destroy(), self._jump_to_error_line(diag.line)),
            ).pack(side="left")

        btn_ok = ttk.Button(btns, text="Cerrar", style="Neutral.TButton", command=dlg.destroy)
        btn_ok.pack(side="right")
        btn_ok.focus_set()

    def show_cheatsheet_dialog(self) -> None:
        dlg = self._create_modal("Chuleta rápida de Python", 720, 480)
        frame = ttk.Frame(dlg, style="App.TFrame", padding=12)
        frame.pack(expand=True, fill="both")

        top = ttk.Frame(frame, style="App.TFrame")
        top.pack(fill="x", pady=(0, 8))
        tk.Label(top, text="Filtrar tema:", bg=self.theme["app_bg"], fg=self.theme["header_fg"]).pack(side="left", padx=(0, 6))
        query_var = tk.StringVar()
        entry_q = ttk.Entry(top, textvariable=query_var, style="App.TEntry")
        entry_q.pack(side="left", fill="x", expand=True)

        body = ttk.Frame(frame, style="App.TFrame")
        body.pack(expand=True, fill="both")
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        lb = tk.Listbox(
            body,
            width=26,
            exportselection=False,
            bg=self.theme["panel_bg"],
            fg=self.theme["editor_fg"],
            selectbackground=self.theme["selection_bg"],
            selectforeground=self.theme["selection_fg"],
            font=("Segoe UI", 10),
            highlightthickness=2,
            highlightcolor=self.theme["focus_ring"],
        )
        lb.grid(row=0, column=0, sticky="ns", padx=(0, 8))

        right = ttk.Frame(body, style="App.TFrame")
        right.grid(row=0, column=1, sticky="nsew")

        lbl_summary = tk.Label(
            right,
            text="",
            bg=self.theme["app_bg"],
            fg=self.theme["header_fg"],
            font=("Segoe UI", 10, "bold"),
            wraplength=420,
            justify="left",
            anchor="w",
        )
        lbl_summary.pack(fill="x", pady=(0, 6))

        txt_snip = tk.Text(
            right,
            bg=self.theme["editor_bg"],
            fg=self.theme["editor_fg"],
            font=("Consolas", 11),
            wrap="none",
            padx=10,
            pady=10,
            highlightthickness=2,
            highlightcolor=self.theme["focus_ring"],
        )
        txt_snip.pack(expand=True, fill="both")

        filtered_items: list[dict[str, str]] = list(CHEATSHEET_SECTIONS)
        selected_snippet = {"code": ""}

        def show_item(index: int):
            if 0 <= index < len(filtered_items):
                item = filtered_items[index]
                lbl_summary.config(text=f"{item['category']} — {item['summary']}")
                selected_snippet["code"] = item["snippet"]
                txt_snip.config(state="normal")
                txt_snip.delete("1.0", tk.END)
                txt_snip.insert("1.0", item["snippet"])
                txt_snip.config(state="disabled")

        def refresh_list(*_args):
            q = query_var.get().strip().lower()
            filtered_items.clear()
            lb.delete(0, tk.END)
            for item in CHEATSHEET_SECTIONS:
                blob = f"{item['category']} {item['summary']} {item['snippet']}".lower()
                if not q or q in blob:
                    filtered_items.append(item)
                    lb.insert(tk.END, item["category"])
            if filtered_items:
                lb.selection_set(0)
                show_item(0)
            else:
                lbl_summary.config(text="Sin coincidencias.")
                selected_snippet["code"] = ""
                txt_snip.config(state="normal")
                txt_snip.delete("1.0", tk.END)
                txt_snip.config(state="disabled")

        lb.bind("<<ListboxSelect>>", lambda _e: show_item(lb.curselection()[0]) if lb.curselection() else None)
        query_var.trace_add("write", refresh_list)
        refresh_list()

        btns = ttk.Frame(frame, style="App.TFrame")
        btns.pack(fill="x", pady=(10, 0))

        def insert_into_editor():
            ed = self.get_active_editor()
            if ed and selected_snippet["code"]:
                ed.text.insert("insert", selected_snippet["code"] + "\n")
                ed.text.focus_set()
                dlg.destroy()

        def copy_snippet():
            if selected_snippet["code"]:
                self.root.clipboard_clear()
                self.root.clipboard_append(selected_snippet["code"])
                self.status_state_var.set("✔ Ejemplo copiado al portapapeles")

        ttk.Button(btns, text="Insertar en editor", style="Run.TButton", command=insert_into_editor).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Copiar ejemplo", style="Neutral.TButton", command=copy_snippet).pack(side="left")
        ttk.Button(btns, text="Cerrar", style="Neutral.TButton", command=dlg.destroy).pack(side="right")
        entry_q.focus_set()

    def open_interpreter_dialog(self) -> None:
        dlg = self._create_modal("Intérprete Python y argumentos (sys.argv)", 640, 410)
        frame = ttk.Frame(dlg, style="App.TFrame", padding=14)
        frame.pack(expand=True, fill="both")

        tk.Label(
            frame,
            text="Intérpretes detectados en el equipo (o selecciona un venv manualmente):",
            bg=self.theme["app_bg"],
            fg=self.theme["header_fg"],
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 6))

        discovered = discover_interpreters()
        listbox = tk.Listbox(
            frame,
            height=5,
            bg=self.theme["panel_bg"],
            fg=self.theme["editor_fg"],
            selectbackground=self.theme["selection_bg"],
            selectforeground=self.theme["selection_fg"],
            font=("Consolas", 9),
            highlightthickness=2,
            highlightcolor=self.theme["focus_ring"],
        )
        listbox.pack(fill="x", pady=(0, 8))
        for path, ver in discovered:
            listbox.insert(tk.END, f"{ver} — {path}")

        path_var = tk.StringVar(value=self.interpreter_path)
        args_var = tk.StringVar(value=self.script_args)
        status_var = tk.StringVar(value="")

        def on_select_list(_e=None):
            sel = listbox.curselection()
            if sel:
                path_var.set(discovered[sel[0]][0])

        listbox.bind("<<ListboxSelect>>", on_select_list)

        row_path = ttk.Frame(frame, style="App.TFrame")
        row_path.pack(fill="x", pady=4)
        tk.Label(row_path, text="Ruta python.exe:", bg=self.theme["app_bg"], fg=self.theme["header_fg"], width=16, anchor="w").pack(
            side="left"
        )
        entry_path = ttk.Entry(row_path, textvariable=path_var, style="App.TEntry")
        entry_path.pack(side="left", fill="x", expand=True, padx=4)

        def browse_exe():
            ftypes = (
                [("Ejecutable Python", "python*.exe"), ("Todos los archivos", "*.*")]
                if sys.platform == "win32"
                else [("Todos los archivos", "*")]
            )
            chosen = filedialog.askopenfilename(title="Seleccionar intérprete Python o venv", filetypes=ftypes, parent=dlg)
            if chosen:
                path_var.set(chosen)

        ttk.Button(row_path, text="Examinar...", style="Neutral.TButton", command=browse_exe).pack(side="left")

        row_args = ttk.Frame(frame, style="App.TFrame")
        row_args.pack(fill="x", pady=8)
        tk.Label(row_args, text="Argumentos (argv):", bg=self.theme["app_bg"], fg=self.theme["header_fg"], width=16, anchor="w").pack(
            side="left"
        )
        ttk.Entry(row_args, textvariable=args_var, style="App.TEntry").pack(side="left", fill="x", expand=True, padx=4)

        tk.Label(
            frame,
            text='Ejemplo de argumentos: --modo prueba "hola mundo" 42  (deja ruta vacía para detección automática)',
            bg=self.theme["app_bg"],
            fg=self.theme["muted_fg"],
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            frame, textvariable=status_var, bg=self.theme["app_bg"], fg=self.theme["error_fg"], font=("Segoe UI", 9), anchor="w"
        ).pack(fill="x", pady=(6, 6))

        btns = ttk.Frame(frame, style="App.TFrame")
        btns.pack(side="bottom", fill="x", pady=(6, 0))

        def save_and_close():
            chosen_path = path_var.get().strip()
            if chosen_path:
                ok, info = validate_interpreter(chosen_path)
                if not ok:
                    status_var.set(f"✖ Intérprete inválido: {info}")
                    return
            self.interpreter_path = chosen_path
            self.script_args = args_var.get().strip()
            self.settings.set("interpreter_path", self.interpreter_path, persist=False)
            self.settings.set("script_args", self.script_args, persist=True)
            self._refresh_interpreter_info()
            self._update_args_badge()
            dlg.destroy()

        ttk.Button(btns, text="Guardar", style="Run.TButton", command=save_and_close).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Cancelar", style="Neutral.TButton", command=dlg.destroy).pack(side="right")
        entry_path.focus_set()

    def open_font_dialog(self) -> None:
        dlg = self._create_modal("Tipografía y tamaño de texto", 460, 340)
        frame = ttk.Frame(dlg, style="App.TFrame", padding=14)
        frame.pack(expand=True, fill="both")

        installed = set(tkfont.families(self.root))
        preferred = [
            "Cascadia Code",
            "Cascadia Mono",
            "Consolas",
            "JetBrains Mono",
            "Fira Code",
            "DejaVu Sans Mono",
            "Atkinson Hyperlegible",
            "OpenDyslexic",
            "Courier New",
            "Lucida Console",
            "Segoe UI",
            "Verdana",
        ]
        available = [f for f in preferred if f in installed]
        if self._font_family not in available:
            available.insert(0, self._font_family)

        fam_var = tk.StringVar(value=self._font_family)
        size_var = tk.IntVar(value=self._font_size)

        tk.Label(
            frame,
            text="Familia tipográfica (monoespaciadas y alta legibilidad):",
            bg=self.theme["app_bg"],
            fg=self.theme["header_fg"],
            anchor="w",
        ).pack(fill="x", pady=(0, 6))
        ttk.Combobox(frame, textvariable=fam_var, values=available, state="readonly", style="App.TCombobox").pack(
            fill="x", pady=(0, 12)
        )

        tk.Label(
            frame, text=f"Tamaño ({MIN_FONT_SIZE}–{MAX_FONT_SIZE} pt):", bg=self.theme["app_bg"], fg=self.theme["header_fg"], anchor="w"
        ).pack(fill="x", pady=(0, 4))
        tk.Spinbox(
            frame,
            from_=MIN_FONT_SIZE,
            to=MAX_FONT_SIZE,
            textvariable=size_var,
            bg=self.theme["panel_bg"],
            fg=self.theme["editor_fg"],
            buttonbackground=self.theme["btn_neutral_bg"],
            font=("Segoe UI", 10),
        ).pack(fill="x", pady=(0, 12))

        preview = tk.Label(
            frame,
            text='def saludar(nombre):\n    print(f"¡Hola, {nombre}!")',
            bg=self.theme["editor_bg"],
            fg=self.theme["editor_fg"],
            font=(self._font_family, self._font_size),
            justify="left",
            anchor="w",
            padx=10,
            pady=10,
            relief="solid",
            bd=1,
        )
        preview.pack(fill="x", pady=(0, 12))

        def update_preview(*_args):
            try:
                sz = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(size_var.get())))
                preview.config(font=(fam_var.get(), sz))
            except Exception:
                pass

        fam_var.trace_add("write", update_preview)
        size_var.trace_add("write", update_preview)

        btns = ttk.Frame(frame, style="App.TFrame")
        btns.pack(side="bottom", fill="x")

        def apply_font():
            try:
                sz = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(size_var.get())))
            except Exception:
                sz = self._font_size
            self._font_family = fam_var.get()
            self._font_size = sz
            self._apply_font_change()
            dlg.destroy()

        ttk.Button(btns, text="Aplicar", style="Run.TButton", command=apply_font).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Cancelar", style="Neutral.TButton", command=dlg.destroy).pack(side="right")

    def show_shortcuts_dialog(self) -> None:
        dlg = self._create_modal("Atajos de teclado — PyPractice", 560, 470)
        frame = ttk.Frame(dlg, style="App.TFrame", padding=14)
        frame.pack(expand=True, fill="both")

        shortcuts = (
            "F5 / Ctrl+Enter         Ejecutar código actual\n"
            "F10                     Depurar paso a paso / Siguiente línea\n"
            "F11                     Entrar en función (Step Into)\n"
            "F8                      Continuar hasta siguiente punto de ruptura\n"
            "F9 (o clic en línea)    Poner / quitar punto de ruptura (●)\n"
            "Shift+F5                Detener ejecución o depuración\n"
            "F6                      Alternar foco entre Editor y Consola\n"
            "Ctrl+L                  Limpiar consola\n\n"
            "Ctrl+N / Ctrl+O / Ctrl+S  Nuevo / Abrir / Guardar\n"
            "Ctrl+Shift+S            Guardar como...\n"
            "Ctrl+E                  Exportar práctica (.md / .txt)\n"
            "Ctrl+W                  Cerrar pestaña actual\n\n"
            "Ctrl+F / Ctrl+H         Buscar / Buscar y reemplazar\n"
            "Ctrl+/ (Ctrl+Shift+7)   Comentar / descomentar líneas\n"
            "Tab / Shift+Tab         Indentar / desindentar 4 espacios\n"
            "Ctrl+G                  Ir a número de línea\n\n"
            "Ctrl+Shift+E            Mostrar/ocultar Panel de ejercicios\n"
            "Ctrl+Shift+V            Mostrar/ocultar Inspector de variables\n"
            "Ctrl++ / Ctrl+- / Ctrl+0  Zoom de tipografía\n"
            "F2 / F1                 Chuleta rápida / Atajos de teclado"
        )
        txt = tk.Text(
            frame,
            bg=self.theme["panel_bg"],
            fg=self.theme["editor_fg"],
            font=("Consolas", 10),
            wrap="word",
            borderwidth=0,
            padx=10,
            pady=10,
        )
        txt.insert("1.0", shortcuts)
        txt.config(state="disabled")
        txt.pack(expand=True, fill="both", pady=(0, 10))

        btn_close = ttk.Button(frame, text="Cerrar", style="Neutral.TButton", command=dlg.destroy)
        btn_close.pack(side="right")
        btn_close.focus_set()

    def show_about_dialog(self) -> None:
        messagebox.showinfo(
            "Acerca de PyPractice",
            f"PyPractice v{APP_VERSION}\n\n"
            "Entorno de escritorio educativo para aprender Python.\n"
            "Construido 100 % con Tkinter y la biblioteca estándar de Python.\n\n"
            "Incluye ejecución aislada, modo seguro AST, resaltado léxico exacto,\n"
            "depurador visual paso a paso, ejercicios con pruebas automáticas,\n"
            "plantillas, chuleta rápida, exportación y temas WCAG AA/AAA.",
        )

    # --- Cierre limpio ---
    def cancel_timers(self) -> None:
        for job_attr in ("_queue_job", "_autosave_job"):
            job = getattr(self, job_attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, job_attr, None)

    def on_close(self) -> None:
        dirty_tabs = [tid for tid, ed in self.editors.items() if ed.is_dirty]
        for tab_id in list(dirty_tabs):
            ed = self.editors.get(tab_id)
            if not ed:
                continue
            self.notebook.select(tab_id)
            name = os.path.basename(ed.filepath) if ed.filepath else self.notebook.tab(tab_id, "text").rstrip(" ●")
            resp = messagebox.askyesnocancel(
                "Guardar antes de salir",
                f"¿Deseas guardar los cambios en '{name}' antes de cerrar PyPractice?",
            )
            if resp is None:
                return
            if resp is True and not self.save_file(ed):
                return

        self.cancel_timers()
        self.settings.clear_recovery_session()
        self.settings.save()
        self.runner.cleanup()
        self.debugger.cleanup()
        self.root.destroy()


def main() -> None:
    enable_windows_dpi_awareness()
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"pypractice.educative.app.{APP_VERSION}")
        except Exception:
            pass

    root = tk.Tk()
    PyPracticeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
