"""Componente de editor de código Python basado en tokenize con asistencia inteligente y accesibilidad."""

import builtins
import io
import keyword
import re
import tkinter as tk
from tkinter import ttk
import tokenize
from typing import Callable, Optional

from diagnostics import Diagnostic, check_syntax_detailed

_BUILTIN_NAMES = frozenset(
    name for name in dir(builtins) if not name.startswith("_") and name not in ("True", "False", "None")
)
_KEYWORDS = frozenset(keyword.kwlist)
_SOFT_KEYWORDS = frozenset(getattr(keyword, "softkwlist", ()))

_STRING_TOKEN_TYPES = {tokenize.STRING}
for _extra in (
    "FSTRING_START",
    "FSTRING_MIDDLE",
    "FSTRING_END",
    "TSTRING_START",
    "TSTRING_MIDDLE",
    "TSTRING_END",
):
    _tok = getattr(tokenize, _extra, None)
    if _tok is not None:
        _STRING_TOKEN_TYPES.add(_tok)

_AUTO_PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'"}
_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
_CLOSE_TO_OPEN = {")": "(", "]": "[", "}": "{"}


class CodeEditor(tk.Frame):
    """Editor de código con resaltado exacto (tokenize), scrollbars, búsqueda y asistencia Python."""

    def __init__(
        self,
        parent,
        *,
        theme: dict,
        code_font,
        on_cursor_change: Optional[Callable[[int, int], None]] = None,
        on_modified_change: Optional[Callable[[bool], None]] = None,
        on_live_diagnostic: Optional[Callable[[Optional[Diagnostic]], None]] = None,
        **kwargs,
    ):
        super().__init__(parent, bg=theme["panel_bg"], **kwargs)
        self.theme = theme
        self.code_font = code_font
        self.on_cursor_change = on_cursor_change
        self.on_modified_change = on_modified_change
        self.on_live_diagnostic = on_live_diagnostic

        self.filepath: Optional[str] = None
        self.is_dirty: bool = False
        self._suppress_modified = False
        self._highlight_job: Optional[str] = None
        self._syntax_job: Optional[str] = None
        self._error_line: Optional[int] = None
        self._debug_line: Optional[int] = None
        self.breakpoints: set[int] = set()
        self._last_line_count = 0

        # Estado de Buscar / Reemplazar
        self._search_visible = False
        self._search_matches: list[tuple[str, str]] = []
        self._search_index = -1

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_search_bar()
        self._build_editor_widgets()
        self._build_context_menu()
        self._bind_events()
        self.apply_theme(theme)

        self.update_line_numbers()
        self._update_active_line()

    def _build_search_bar(self) -> None:
        self.search_frame = tk.Frame(self, padx=6, pady=4)
        self.search_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.case_var = tk.BooleanVar(value=False)
        self.search_count_var = tk.StringVar(value="")

        self.lbl_find = tk.Label(self.search_frame, text="Buscar:", font=("Segoe UI", 9))
        self.lbl_find.grid(row=0, column=0, padx=(0, 4), sticky="w")

        self.entry_find = ttk.Entry(self.search_frame, textvariable=self.search_var, width=22, style="App.TEntry")
        self.entry_find.grid(row=0, column=1, padx=2, sticky="ew")

        self.btn_prev = ttk.Button(self.search_frame, text="▲", width=3, style="Neutral.TButton", command=self.find_prev)
        self.btn_prev.grid(row=0, column=2, padx=2)

        self.btn_next = ttk.Button(self.search_frame, text="▼", width=3, style="Neutral.TButton", command=self.find_next)
        self.btn_next.grid(row=0, column=3, padx=2)

        self.chk_case = ttk.Checkbutton(
            self.search_frame,
            text="Aa",
            variable=self.case_var,
            command=self._refresh_search,
            style="Toolbar.TCheckbutton",
        )
        self.chk_case.grid(row=0, column=4, padx=4)

        self.lbl_count = tk.Label(self.search_frame, textvariable=self.search_count_var, font=("Segoe UI", 9), width=10, anchor="w")
        self.lbl_count.grid(row=0, column=5, padx=4)

        self.btn_close_search = ttk.Button(
            self.search_frame, text="✕", width=3, style="Neutral.TButton", command=self.hide_search_bar
        )
        self.btn_close_search.grid(row=0, column=6, padx=(4, 0))

        # Segunda fila (Reemplazar)
        self.lbl_replace = tk.Label(self.search_frame, text="Reemplazar:", font=("Segoe UI", 9))
        self.entry_replace = ttk.Entry(self.search_frame, textvariable=self.replace_var, width=22, style="App.TEntry")
        self.btn_replace_one = ttk.Button(
            self.search_frame, text="Reemplazar", style="Neutral.TButton", command=self.replace_current
        )
        self.btn_replace_all = ttk.Button(
            self.search_frame, text="Todo", style="Neutral.TButton", command=self.replace_all
        )

        self.search_frame.grid_columnconfigure(1, weight=1)

        self.entry_find.bind("<KeyRelease>", lambda _e: self._refresh_search())
        self.entry_find.bind("<Return>", lambda _e: (self.find_next(), "break")[1])
        self.entry_find.bind("<Shift-Return>", lambda _e: (self.find_prev(), "break")[1])
        self.entry_find.bind("<Escape>", lambda _e: (self.hide_search_bar(), "break")[1])
        self.entry_replace.bind("<Return>", lambda _e: (self.replace_current(), "break")[1])
        self.entry_replace.bind("<Escape>", lambda _e: (self.hide_search_bar(), "break")[1])

    def _build_editor_widgets(self) -> None:
        # Margen de números de línea (excluido del foco por teclado)
        self.line_numbers = tk.Text(
            self,
            width=4,
            font=self.code_font,
            borderwidth=0,
            highlightthickness=0,
            state="disabled",
            takefocus=0,
            wrap="none",
            cursor="arrow",
            padx=6,
            pady=4,
        )
        self.line_numbers.grid(row=1, column=0, sticky="ns")
        self.line_numbers.tag_configure("right", justify="right")

        # Área principal de código con foco visible
        self.text = tk.Text(
            self,
            font=self.code_font,
            undo=True,
            autoseparators=True,
            maxundo=-1,
            borderwidth=0,
            highlightthickness=2,
            wrap="none",
            padx=6,
            pady=4,
        )
        self.text.grid(row=1, column=1, sticky="nsew")

        self.v_scroll = ttk.Scrollbar(
            self, orient="vertical", command=self._on_vscroll, style="App.Vertical.TScrollbar"
        )
        self.v_scroll.grid(row=1, column=2, sticky="ns")

        self.h_scroll = ttk.Scrollbar(
            self, orient="horizontal", command=self.text.xview, style="App.Horizontal.TScrollbar"
        )
        self.h_scroll.grid(row=2, column=1, sticky="ew")

        self.text.config(yscrollcommand=self._sync_yscroll, xscrollcommand=self.h_scroll.set)

    def _build_context_menu(self) -> None:
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Deshacer", accelerator="Ctrl+Z", command=lambda: self.text.event_generate("<<Undo>>"))
        self.context_menu.add_command(label="Rehacer", accelerator="Ctrl+Y", command=lambda: self.text.event_generate("<<Redo>>"))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Cortar", accelerator="Ctrl+X", command=lambda: self.text.event_generate("<<Cut>>"))
        self.context_menu.add_command(label="Copiar", accelerator="Ctrl+C", command=lambda: self.text.event_generate("<<Copy>>"))
        self.context_menu.add_command(label="Pegar", accelerator="Ctrl+V", command=lambda: self.text.event_generate("<<Paste>>"))
        self.context_menu.add_command(label="Seleccionar todo", accelerator="Ctrl+A", command=self.select_all)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Comentar / Descomentar", accelerator="Ctrl+/", command=self.toggle_comment)
        self.context_menu.add_command(label="Buscar...", accelerator="Ctrl+F", command=lambda: self.show_search_bar(replace=False))

    def _bind_events(self) -> None:
        self.text.bind("<<Modified>>", self._on_text_modified)
        self.text.bind("<KeyRelease>", self._on_cursor_activity)
        self.text.bind("<ButtonRelease-1>", self._on_cursor_activity)
        self.text.bind("<FocusIn>", self._on_cursor_activity)

        # Sincronización bidireccional de rueda del ratón en números de línea
        self.line_numbers.bind("<MouseWheel>", self._on_gutter_wheel)
        self.line_numbers.bind("<Button-4>", lambda _e: self._on_gutter_wheel_linux(-1))
        self.line_numbers.bind("<Button-5>", lambda _e: self._on_gutter_wheel_linux(1))
        self.line_numbers.bind("<Button-1>", self._on_gutter_click)

        # Asistencia inteligente de escritura Python
        self.text.bind("<Tab>", self._on_tab)
        self.text.bind("<Shift-Tab>", self._on_shift_tab)
        try:
            self.text.bind("<ISO_Left_Tab>", self._on_shift_tab)
        except tk.TclError:
            # Tk < 8.6.13 en Windows (p. ej. Python 3.10) no define el keysym X11 ISO_Left_Tab
            pass
        self.text.bind("<Return>", self._on_return)
        self.text.bind("<BackSpace>", self._on_backspace)
        self.text.bind("<Key>", self._on_key_press)

        # Atajos de editor
        self.text.bind("<Control-a>", lambda _e: (self.select_all(), "break")[1])
        self.text.bind("<Control-A>", lambda _e: (self.select_all(), "break")[1])
        self.text.bind("<Control-slash>", lambda _e: (self.toggle_comment(), "break")[1])
        self.text.bind("<Control-KP_Divide>", lambda _e: (self.toggle_comment(), "break")[1])
        self.text.bind("<Control-Shift-7>", lambda _e: (self.toggle_comment(), "break")[1])
        self.text.bind("<Control-f>", lambda _e: (self.show_search_bar(replace=False), "break")[1])
        self.text.bind("<Control-F>", lambda _e: (self.show_search_bar(replace=False), "break")[1])
        self.text.bind("<Control-h>", lambda _e: (self.show_search_bar(replace=True), "break")[1])
        self.text.bind("<Control-H>", lambda _e: (self.show_search_bar(replace=True), "break")[1])
        self.text.bind("<Escape>", lambda _e: self.hide_search_bar() if self._search_visible else None)

        # Menú contextual
        self.text.bind("<Button-3>", self._show_context_menu)

    def apply_theme(self, theme: dict) -> None:
        self.theme = theme
        self.configure(bg=theme["panel_bg"])
        self.search_frame.configure(bg=theme["toolbar_bg"])
        for lbl in (self.lbl_find, self.lbl_replace, self.lbl_count):
            lbl.configure(bg=theme["toolbar_bg"], fg=theme["header_fg"])

        self.line_numbers.configure(
            bg=theme["gutter_bg"],
            fg=theme["gutter_fg"],
            selectbackground=theme["gutter_bg"],
            selectforeground=theme["gutter_fg"],
        )
        self.line_numbers.tag_configure(
            "gutter_error",
            background=theme["gutter_error_bg"],
            foreground=theme["gutter_error_fg"],
        )
        self.line_numbers.tag_configure(
            "gutter_debug",
            background=theme["gutter_debug_bg"],
            foreground=theme["gutter_debug_fg"],
        )
        self.line_numbers.tag_configure(
            "gutter_bp",
            foreground=theme["gutter_bp_fg"],
        )

        self.text.configure(
            bg=theme["editor_bg"],
            fg=theme["editor_fg"],
            insertbackground=theme["cursor"],
            selectbackground=theme["selection_bg"],
            selectforeground=theme["selection_fg"],
            highlightthickness=theme.get("focus_thickness", 2),
            highlightbackground=theme["border"],
            highlightcolor=theme["focus_ring"],
        )

        syntax = theme["syntax"]
        for tag_name, color in syntax.items():
            self.text.tag_configure(tag_name, foreground=color)

        self.text.tag_configure("current_line", background=theme["current_line"])
        self.text.tag_configure(
            "bracket_match",
            background=theme["bracket_match_bg"],
            foreground=theme["bracket_match_fg"],
        )
        self.text.tag_configure(
            "bracket_mismatch",
            background=theme["bracket_mismatch_bg"],
            foreground=theme["bracket_mismatch_fg"],
        )
        self.text.tag_configure(
            "error_line",
            background=theme["error_line_bg"],
            underline=True,
        )
        self.text.tag_configure(
            "debug_line",
            background=theme["debug_line_bg"],
        )
        self.text.tag_configure(
            "search_match",
            background=theme["search_match_bg"],
            foreground=theme["search_match_fg"],
        )
        self.text.tag_configure(
            "search_current",
            background=theme["search_current_bg"],
            foreground=theme["search_current_fg"],
        )

        # Prioridades de tags: selección > búsqueda > paréntesis > sintaxis > depuración > error > línea activa
        self.text.tag_lower("current_line")
        self.text.tag_raise("error_line", "current_line")
        self.text.tag_raise("debug_line", "error_line")
        for tag_name in syntax:
            self.text.tag_raise(tag_name, "debug_line")
        self.text.tag_raise("bracket_match")
        self.text.tag_raise("bracket_mismatch")
        self.text.tag_raise("search_match")
        self.text.tag_raise("search_current")
        self.text.tag_raise("sel")

        self.context_menu.configure(
            bg=theme["toolbar_bg"],
            fg=theme["header_fg"],
            activebackground=theme["selection_bg"],
            activeforeground=theme["selection_fg"],
        )

    # --- API pública de contenido y estado ---
    def get_code(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set_code(self, code: str, *, mark_dirty: bool = False) -> None:
        self._suppress_modified = True
        try:
            self.text.delete("1.0", tk.END)
            if code:
                self.text.insert("1.0", code)
            self.text.edit_reset()
            self.text.edit_modified(False)
        finally:
            self._suppress_modified = False

        self.set_dirty(mark_dirty)
        self.set_error_line(None)
        self.set_debug_line(None)
        self.breakpoints.clear()
        self.update_line_numbers(force=True)
        self.highlight_syntax()
        self._update_active_line()
        self._update_brackets()
        self._notify_cursor()

    def set_dirty(self, dirty: bool) -> None:
        if self.is_dirty != dirty:
            self.is_dirty = dirty
            if self.on_modified_change:
                self.on_modified_change(dirty)

    def get_cursor_position(self) -> tuple[int, int]:
        idx = self.text.index("insert")
        line_s, col_s = idx.split(".")
        return int(line_s), int(col_s) + 1

    def goto_line(self, line: int) -> None:
        total = int(self.text.index("end-1c").split(".")[0])
        target = max(1, min(line, total))
        self.text.mark_set("insert", f"{target}.0")
        self.text.see(f"{target}.0")
        self.text.focus_set()
        self._update_active_line()
        self._update_brackets()
        self._notify_cursor()

    def set_error_line(self, line: Optional[int]) -> None:
        self._error_line = line
        self.text.tag_remove("error_line", "1.0", tk.END)
        if line is not None:
            total = int(self.text.index("end-1c").split(".")[0])
            if 1 <= line <= total:
                self.text.tag_add("error_line", f"{line}.0", f"{line}.end+1c")
        self.update_line_numbers(force=True)

    def set_debug_line(self, line: Optional[int]) -> None:
        self._debug_line = line
        self.text.tag_remove("debug_line", "1.0", tk.END)
        if line is not None:
            total = int(self.text.index("end-1c").split(".")[0])
            if 1 <= line <= total:
                self.text.tag_add("debug_line", f"{line}.0", f"{line}.end+1c")
                self.text.see(f"{line}.0")
        self.update_line_numbers(force=True)

    def toggle_breakpoint(self, line: Optional[int] = None) -> bool:
        if line is None:
            line, _ = self.get_cursor_position()
        total = int(self.text.index("end-1c").split(".")[0])
        if not (1 <= line <= total):
            return False
        if line in self.breakpoints:
            self.breakpoints.remove(line)
            added = False
        else:
            self.breakpoints.add(line)
            added = True
        self.update_line_numbers(force=True)
        return added

    def check_syntax(self) -> Optional[Diagnostic]:
        diag = check_syntax_detailed(self.get_code())
        self.set_error_line(diag.line if diag else None)
        return diag

    def select_all(self) -> None:
        self.text.tag_add("sel", "1.0", "end-1c")
        self.text.mark_set("insert", "end-1c")
        self.text.see("insert")

    # --- Scroll y números de línea ---
    def _on_vscroll(self, *args) -> None:
        self.text.yview(*args)
        self.line_numbers.yview(*args)

    def _sync_yscroll(self, first: str, last: str) -> None:
        self.v_scroll.set(first, last)
        self.line_numbers.yview_moveto(first)

    def _on_gutter_wheel(self, event) -> str:
        delta = int(-1 * (event.delta / 120)) if event.delta else 0
        if delta:
            self.text.yview_scroll(delta * 3, "units")
        return "break"

    def _on_gutter_wheel_linux(self, direction: int) -> str:
        self.text.yview_scroll(direction * 3, "units")
        return "break"

    def _on_gutter_click(self, event) -> str:
        idx = self.line_numbers.index(f"@{event.x},{event.y}")
        line_no = int(idx.split(".")[0])
        self.toggle_breakpoint(line_no)
        return "break"

    def update_line_numbers(self, force: bool = False) -> None:
        total_lines = int(self.text.index("end-1c").split(".")[0])
        if (
            not force
            and total_lines == self._last_line_count
            and self._error_line is None
            and self._debug_line is None
            and not self.breakpoints
        ):
            return
        self._last_line_count = total_lines

        width = max(5, len(str(total_lines)) + 3)
        if int(self.line_numbers.cget("width")) != width:
            self.line_numbers.config(width=width)

        rendered = []
        for i in range(1, total_lines + 1):
            prefix = ""
            if self._debug_line == i:
                prefix = "➤"
            elif self._error_line == i:
                prefix = "✖"
            elif i in self.breakpoints:
                prefix = "●"
            rendered.append(f"{prefix}{i}")
        lines_str = "\n".join(rendered)

        y_top = self.text.yview()[0]
        self.line_numbers.config(state="normal")
        self.line_numbers.delete("1.0", tk.END)
        self.line_numbers.insert("1.0", lines_str, "right")
        for bp in self.breakpoints:
            if 1 <= bp <= total_lines:
                self.line_numbers.tag_add("gutter_bp", f"{bp}.0", f"{bp}.end")
        if self._error_line is not None and 1 <= self._error_line <= total_lines:
            self.line_numbers.tag_add("gutter_error", f"{self._error_line}.0", f"{self._error_line}.end")
        if self._debug_line is not None and 1 <= self._debug_line <= total_lines:
            self.line_numbers.tag_add("gutter_debug", f"{self._debug_line}.0", f"{self._debug_line}.end")
        self.line_numbers.config(state="disabled")
        self.line_numbers.yview_moveto(y_top)

    # --- Eventos y debounce ---
    def _on_text_modified(self, _event=None) -> None:
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self._suppress_modified:
            return

        self.set_dirty(True)
        self.update_line_numbers()
        self._schedule_highlight()
        self._schedule_live_syntax()
        self._update_active_line()
        self._update_brackets()
        self._notify_cursor()
        if self._search_visible:
            self._refresh_search()

    def _on_cursor_activity(self, _event=None) -> None:
        self._update_active_line()
        self._update_brackets()
        self._notify_cursor()

    def _notify_cursor(self) -> None:
        if self.on_cursor_change:
            line, col = self.get_cursor_position()
            self.on_cursor_change(line, col)

    def _schedule_highlight(self) -> None:
        if self._highlight_job is not None:
            self.after_cancel(self._highlight_job)
        self._highlight_job = self.after(100, self._run_scheduled_highlight)

    def _run_scheduled_highlight(self) -> None:
        self._highlight_job = None
        self.highlight_syntax()

    def _schedule_live_syntax(self) -> None:
        if self._syntax_job is not None:
            self.after_cancel(self._syntax_job)
        self._syntax_job = self.after(550, self._run_live_syntax)

    def _run_live_syntax(self) -> None:
        self._syntax_job = None
        diag = check_syntax_detailed(self.get_code())
        self.set_error_line(diag.line if diag else None)
        if self.on_live_diagnostic:
            self.on_live_diagnostic(diag)

    # --- Resaltado léxico exacto con tokenize ---
    def highlight_syntax(self) -> None:
        code = self.get_code()
        syntax_tags = tuple(self.theme["syntax"].keys())
        for tag in syntax_tags:
            self.text.tag_remove(tag, "1.0", tk.END)

        if not code:
            return

        prev_meaningful = None
        in_decorator = False
        last_valid_pos = (1, 0)

        try:
            tokens = tokenize.generate_tokens(io.StringIO(code).readline)
            for tok in tokens:
                tok_type = tok.type
                tok_str = tok.string
                start_line, start_col = tok.start
                end_line, end_col = tok.end
                last_valid_pos = tok.end

                if tok_type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
                    if tok_type in (tokenize.NEWLINE, tokenize.NL):
                        in_decorator = False
                    continue

                start_idx = f"{start_line}.{start_col}"
                end_idx = f"{end_line}.{end_col}"
                tag = None

                if tok_type == tokenize.COMMENT:
                    tag = "comment"
                elif tok_type in _STRING_TOKEN_TYPES:
                    tag = "string"
                elif tok_type == tokenize.NUMBER:
                    tag = "number"
                elif tok_type == tokenize.OP:
                    if tok_str == "@" and prev_meaningful in (None, "\n"):
                        in_decorator = True
                        tag = "decorator"
                    else:
                        tag = "operator"
                elif tok_type == tokenize.NAME:
                    if in_decorator:
                        tag = "decorator"
                    elif prev_meaningful in ("def", "class"):
                        tag = "function"
                    elif tok_str in _KEYWORDS:
                        tag = "keyword"
                    elif tok_str in _SOFT_KEYWORDS:
                        tag = "softkeyword"
                    elif tok_str in ("self", "cls"):
                        tag = "self_param"
                    elif tok_str in _BUILTIN_NAMES and prev_meaningful != ".":
                        tag = "builtin"

                if tag:
                    self.text.tag_add(tag, start_idx, end_idx)

                if tok_type != tokenize.COMMENT:
                    prev_meaningful = tok_str
                    if in_decorator and tok_type == tokenize.OP and tok_str not in ("@", "."):
                        in_decorator = False
        except (tokenize.TokenError, IndentationError):
            # Si hay una cadena o bloque sin cerrar mientras el usuario escribe, pintar el resto como cadena
            tail_start = f"{last_valid_pos[0]}.{last_valid_pos[1]}"
            tail_text = self.text.get(tail_start, "end-1c")
            if tail_text.lstrip().startswith(('"', "'", 'f"', "f'", 'r"', "r'", 'b"', "b'")):
                self.text.tag_add("string", tail_start, "end-1c")

    # --- Línea activa y emparejamiento de paréntesis ---
    def _update_active_line(self) -> None:
        self.text.tag_remove("current_line", "1.0", tk.END)
        self.text.tag_add("current_line", "insert linestart", "insert lineend+1c")

    def _update_brackets(self) -> None:
        self.text.tag_remove("bracket_match", "1.0", tk.END)
        self.text.tag_remove("bracket_mismatch", "1.0", tk.END)

        code = self.get_code()
        if not code:
            return

        # Convertir índice Tk a offset de carácter
        line_no, col_no = self.get_cursor_position()
        lines = code.split("\n")
        if line_no - 1 >= len(lines):
            return
        offset = sum(len(l) + 1 for l in lines[: line_no - 1]) + (col_no - 1)

        probe_pos = None
        if offset > 0 and code[offset - 1] in "()[]{}":
            probe_pos = offset - 1
        elif offset < len(code) and code[offset] in "()[]{}":
            probe_pos = offset

        if probe_pos is None:
            return

        ch = code[probe_pos]
        match_pos = self._find_matching_bracket(code, probe_pos, ch)
        idx_a = self._offset_to_index(lines, probe_pos)
        if match_pos is not None:
            idx_b = self._offset_to_index(lines, match_pos)
            self.text.tag_add("bracket_match", idx_a, f"{idx_a}+1c")
            self.text.tag_add("bracket_match", idx_b, f"{idx_b}+1c")
        else:
            self.text.tag_add("bracket_mismatch", idx_a, f"{idx_a}+1c")

    @staticmethod
    def _offset_to_index(lines: list[str], offset: int) -> str:
        acc = 0
        for i, line in enumerate(lines, start=1):
            next_acc = acc + len(line) + 1
            if offset < next_acc:
                return f"{i}.{offset - acc}"
            acc = next_acc
        return f"{len(lines)}.{len(lines[-1])}"

    @staticmethod
    def _find_matching_bracket(code: str, pos: int, ch: str) -> Optional[int]:
        if ch in _OPEN_TO_CLOSE:
            target = _OPEN_TO_CLOSE[ch]
            depth = 0
            for i in range(pos, len(code)):
                if code[i] == ch:
                    depth += 1
                elif code[i] == target:
                    depth -= 1
                    if depth == 0:
                        return i
        elif ch in _CLOSE_TO_OPEN:
            target = _CLOSE_TO_OPEN[ch]
            depth = 0
            for i in range(pos, -1, -1):
                if code[i] == ch:
                    depth += 1
                elif code[i] == target:
                    depth -= 1
                    if depth == 0:
                        return i
        return None

    # --- Asistencia de edición Python ---
    def _on_tab(self, _event) -> str:
        try:
            sel_start = self.text.index("sel.first")
            sel_end = self.text.index("sel.last")
        except tk.TclError:
            self.text.insert("insert", "    ")
            return "break"

        start_line = int(sel_start.split(".")[0])
        end_line = int(sel_end.split(".")[0])
        if sel_end.endswith(".0") and end_line > start_line:
            end_line -= 1

        self.text.edit_separator()
        for ln in range(start_line, end_line + 1):
            self.text.insert(f"{ln}.0", "    ")
        self.text.edit_separator()
        self.text.tag_add("sel", f"{start_line}.0", f"{end_line}.end")
        return "break"

    def _on_shift_tab(self, _event) -> str:
        try:
            sel_start = self.text.index("sel.first")
            sel_end = self.text.index("sel.last")
            start_line = int(sel_start.split(".")[0])
            end_line = int(sel_end.split(".")[0])
            if sel_end.endswith(".0") and end_line > start_line:
                end_line -= 1
            had_sel = True
        except tk.TclError:
            start_line = end_line = int(self.text.index("insert").split(".")[0])
            had_sel = False

        self.text.edit_separator()
        for ln in range(start_line, end_line + 1):
            line_text = self.text.get(f"{ln}.0", f"{ln}.end")
            if line_text.startswith("    "):
                self.text.delete(f"{ln}.0", f"{ln}.4")
            elif line_text.startswith("\t"):
                self.text.delete(f"{ln}.0", f"{ln}.1")
            else:
                leading_spaces = len(line_text) - len(line_text.lstrip(" "))
                if leading_spaces > 0:
                    self.text.delete(f"{ln}.0", f"{ln}.{min(4, leading_spaces)}")
        self.text.edit_separator()
        if had_sel:
            self.text.tag_add("sel", f"{start_line}.0", f"{end_line}.end")
        return "break"

    def _on_return(self, _event) -> str:
        line_start = self.text.index("insert linestart")
        before_cursor = self.text.get(line_start, "insert")
        indent_match = re.match(r"^[ \t]*", before_cursor)
        indent = indent_match.group(0).replace("\t", "    ") if indent_match else ""

        # Quitar comentarios simples al comprobar si la línea termina en ':'
        code_part = before_cursor.split("#", 1)[0].rstrip()
        if code_part.endswith(":"):
            indent += "    "

        self.text.edit_separator()
        self.text.insert("insert", "\n" + indent)
        self.text.see("insert")
        return "break"

    def _on_backspace(self, _event) -> Optional[str]:
        try:
            self.text.index("sel.first")
            return None
        except tk.TclError:
            pass

        prev_char = self.text.get("insert-1c", "insert")
        next_char = self.text.get("insert", "insert+1c")
        if prev_char in _AUTO_PAIRS and _AUTO_PAIRS[prev_char] == next_char:
            self.text.delete("insert-1c", "insert+1c")
            return "break"
        return None

    def _on_key_press(self, event) -> Optional[str]:
        ch = event.char
        # Ignorar teclas de control puras, pero permitir AltGr (teclados ES: [, ], {, })
        if not ch or ord(ch) < 32:
            return None
        if (event.state & 0x4) and not (event.state & 0x20000) and ch not in _AUTO_PAIRS and ch not in (")", "]", "}"):
            return None

        # Sobrescribir cierre automático si ya está justo delante
        if ch in (")", "]", "}", '"', "'"):
            next_ch = self.text.get("insert", "insert+1c")
            has_sel = bool(self.text.tag_ranges("sel"))
            if not has_sel and next_ch == ch:
                self.text.mark_set("insert", "insert+1c")
                return "break"

        if ch in _AUTO_PAIRS:
            closing = _AUTO_PAIRS[ch]
            try:
                sel_first = self.text.index("sel.first")
                sel_last = self.text.index("sel.last")
                selected = self.text.get(sel_first, sel_last)
                self.text.edit_separator()
                self.text.delete(sel_first, sel_last)
                self.text.insert(sel_first, f"{ch}{selected}{closing}")
                self.text.tag_add("sel", f"{sel_first}+1c", f"{sel_first}+{1 + len(selected)}c")
                self.text.mark_set("insert", f"{sel_first}+{1 + len(selected)}c")
                return "break"
            except tk.TclError:
                pass

            next_ch = self.text.get("insert", "insert+1c")
            prev_ch = self.text.get("insert-1c", "insert")
            if ch in ('"', "'") and (prev_ch.isalnum() or next_ch.isalnum()):
                return None
            if next_ch and (next_ch.isalnum() or next_ch == "_"):
                return None

            self.text.insert("insert", ch + closing)
            self.text.mark_set("insert", "insert-1c")
            return "break"

        return None

    def toggle_comment(self) -> None:
        try:
            sel_start = self.text.index("sel.first")
            sel_end = self.text.index("sel.last")
            start_line = int(sel_start.split(".")[0])
            end_line = int(sel_end.split(".")[0])
            if sel_end.endswith(".0") and end_line > start_line:
                end_line -= 1
            had_sel = True
        except tk.TclError:
            start_line = end_line = int(self.text.index("insert").split(".")[0])
            had_sel = False

        lines = [self.text.get(f"{ln}.0", f"{ln}.end") for ln in range(start_line, end_line + 1)]
        non_empty = [ln for ln in lines if ln.strip()]
        all_commented = bool(non_empty) and all(ln.lstrip().startswith("#") for ln in non_empty)

        self.text.edit_separator()
        for ln in range(start_line, end_line + 1):
            text_line = self.text.get(f"{ln}.0", f"{ln}.end")
            if not text_line.strip():
                continue
            lead = len(text_line) - len(text_line.lstrip(" "))
            if all_commented:
                if text_line[lead:].startswith("# "):
                    self.text.delete(f"{ln}.{lead}", f"{ln}.{lead + 2}")
                elif text_line[lead:].startswith("#"):
                    self.text.delete(f"{ln}.{lead}", f"{ln}.{lead + 1}")
            else:
                self.text.insert(f"{ln}.{lead}", "# ")
        self.text.edit_separator()

        if had_sel:
            self.text.tag_add("sel", f"{start_line}.0", f"{end_line}.end")

    def _show_context_menu(self, event) -> None:
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    # --- Buscar y Reemplazar ---
    def show_search_bar(self, replace: bool = False) -> None:
        self._search_visible = True
        self.search_frame.grid(row=0, column=0, columnspan=3, sticky="ew")

        if replace:
            self.lbl_replace.grid(row=1, column=0, padx=(0, 4), pady=(4, 0), sticky="w")
            self.entry_replace.grid(row=1, column=1, padx=2, pady=(4, 0), sticky="ew")
            self.btn_replace_one.grid(row=1, column=2, columnspan=2, padx=2, pady=(4, 0), sticky="ew")
            self.btn_replace_all.grid(row=1, column=4, columnspan=2, padx=2, pady=(4, 0), sticky="ew")
        else:
            for w in (self.lbl_replace, self.entry_replace, self.btn_replace_one, self.btn_replace_all):
                w.grid_forget()

        # Precargar texto seleccionado si existe
        try:
            selected = self.text.get("sel.first", "sel.last")
            if selected and "\n" not in selected:
                self.search_var.set(selected)
        except tk.TclError:
            pass

        self.entry_find.focus_set()
        self.entry_find.select_range(0, tk.END)
        self._refresh_search()

    def hide_search_bar(self) -> None:
        self._search_visible = False
        self.search_frame.grid_forget()
        self.text.tag_remove("search_match", "1.0", tk.END)
        self.text.tag_remove("search_current", "1.0", tk.END)
        self._search_matches.clear()
        self._search_index = -1
        self.search_count_var.set("")
        self.text.focus_set()

    def _refresh_search(self) -> None:
        self.text.tag_remove("search_match", "1.0", tk.END)
        self.text.tag_remove("search_current", "1.0", tk.END)
        self._search_matches.clear()

        query = self.search_var.get()
        if not query:
            self._search_index = -1
            self.search_count_var.set("")
            return

        nocase = not self.case_var.get()
        start = "1.0"
        while True:
            pos = self.text.search(query, start, stopindex=tk.END, nocase=nocase)
            if not pos:
                break
            end = f"{pos}+{len(query)}c"
            self.text.tag_add("search_match", pos, end)
            self._search_matches.append((pos, end))
            start = end

        if not self._search_matches:
            self._search_index = -1
            self.search_count_var.set("0 resultados")
            return

        if self._search_index < 0 or self._search_index >= len(self._search_matches):
            self._search_index = 0
        self._focus_search_match()

    def _focus_search_match(self) -> None:
        self.text.tag_remove("search_current", "1.0", tk.END)
        if not self._search_matches or self._search_index < 0:
            return
        pos, end = self._search_matches[self._search_index]
        self.text.tag_add("search_current", pos, end)
        self.text.mark_set("insert", pos)
        self.text.see(pos)
        self.search_count_var.set(f"{self._search_index + 1} de {len(self._search_matches)}")

    def find_next(self) -> None:
        if not self._search_matches:
            self._refresh_search()
            return
        self._search_index = (self._search_index + 1) % len(self._search_matches)
        self._focus_search_match()

    def find_prev(self) -> None:
        if not self._search_matches:
            self._refresh_search()
            return
        self._search_index = (self._search_index - 1) % len(self._search_matches)
        self._focus_search_match()

    def replace_current(self) -> None:
        if not self._search_matches or self._search_index < 0:
            self._refresh_search()
            return
        pos, end = self._search_matches[self._search_index]
        replacement = self.replace_var.get()
        self.text.edit_separator()
        self.text.delete(pos, end)
        self.text.insert(pos, replacement)
        self.text.edit_separator()
        self._refresh_search()

    def replace_all(self) -> None:
        query = self.search_var.get()
        if not query or not self._search_matches:
            return
        replacement = self.replace_var.get()
        self.text.edit_separator()
        for pos, end in reversed(self._search_matches):
            self.text.delete(pos, end)
            self.text.insert(pos, replacement)
        self.text.edit_separator()
        self._refresh_search()
