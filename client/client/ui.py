"""Frag — CustomTkinter desktop UI.

Layout: sidebar nav on the left with the brand mark, section buttons, and a
connection chip + settings entry at the bottom. Main content area on the right
swaps between views (Mods, Worlds, Sync, Settings).
"""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import __version__
from .api import FragClient, unzip_into, zip_directory
from .config import Config
from .pd_catalog import PDCatalogClient
from .resources import ICON_ICO, ICON_PNG, asset
from .scanner import ModInfo, scan_mods
from .sync_settings import RemoteSettings, pull_settings, push_settings
from .world_info import WorldInfo, read_world

# ---- Frag palette -----------------------------------------------------------

BG = "#0B0B12"
SURFACE = "#15151F"
SURFACE_ALT = "#1D1D2B"
SURFACE_HI = "#2A2A3E"
SIDEBAR = "#0E0E16"
DIVIDER = "#1F1F30"
PRIMARY = "#8B5CF6"
PRIMARY_HOV = "#7C3AED"
PRIMARY_SOFT = "#2A2046"
ACCENT = "#EC4899"
ACCENT_SOFT = "#3A1B30"
TEXT = "#F4F4F8"
TEXT_DIM = "#9CA3B0"
TEXT_FAINT = "#6B7280"
SUCCESS = "#10B981"
SUCCESS_SOFT = "#0B2A22"
WARNING = "#F59E0B"
WARNING_SOFT = "#2A1F0B"
ERROR = "#EF4444"
ERROR_SOFT = "#2A1418"

FONT_DISPLAY = ("Segoe UI", 22, "bold")
FONT_H1 = ("Segoe UI", 20, "bold")
FONT_H2 = ("Segoe UI", 15, "bold")
FONT_NAV = ("Segoe UI", 13)
FONT_BODY = ("Segoe UI", 12)
FONT_DIM = ("Segoe UI", 11)
FONT_TINY = ("Segoe UI", 10)
FONT_MONO = ("Consolas", 11)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ---- shared widgets ---------------------------------------------------------


def primary_button(parent, **kw) -> ctk.CTkButton:
    defaults = dict(
        fg_color=PRIMARY,
        hover_color=PRIMARY_HOV,
        text_color=TEXT,
        corner_radius=8,
        height=36,
        font=FONT_BODY,
    )
    defaults.update(kw)
    return ctk.CTkButton(parent, **defaults)


def ghost_button(parent, **kw) -> ctk.CTkButton:
    defaults = dict(
        fg_color="transparent",
        border_width=1,
        border_color=SURFACE_HI,
        hover_color=SURFACE_ALT,
        text_color=TEXT,
        corner_radius=8,
        height=36,
        font=FONT_BODY,
    )
    defaults.update(kw)
    return ctk.CTkButton(parent, **defaults)


def danger_button(parent, **kw) -> ctk.CTkButton:
    defaults = dict(
        fg_color="transparent",
        border_width=1,
        border_color=ERROR,
        hover_color=ERROR_SOFT,
        text_color=ERROR,
        corner_radius=8,
        height=32,
        font=FONT_BODY,
    )
    defaults.update(kw)
    return ctk.CTkButton(parent, **defaults)


def card(parent, **kw) -> ctk.CTkFrame:
    return ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=14, **kw)


def _pill(parent, text: str, color: str, soft: str) -> ctk.CTkLabel:
    """Status pill: rounded label with soft background and colored text."""
    lbl = ctk.CTkLabel(
        parent,
        text=f"  {text}  ",
        font=FONT_TINY,
        text_color=color,
        fg_color=soft,
        corner_radius=11,
        height=22,
    )
    return lbl


# ---- hover row helper -------------------------------------------------------


class HoverRow(ctk.CTkFrame):
    """A frame that lightens on hover. Subclasses set up content via _build."""

    def __init__(self, parent, base=SURFACE, hover=SURFACE_ALT, on_click=None, **kw):
        super().__init__(parent, fg_color=base, corner_radius=10, **kw)
        self._base = base
        self._hover = hover
        self._on_click = on_click
        for ev in ("<Enter>",):
            self.bind(ev, self._on_enter)
        for ev in ("<Leave>",):
            self.bind(ev, self._on_leave)
        if on_click:
            self.bind("<Button-1>", lambda _e: on_click())

    def _on_enter(self, _e=None):
        self.configure(fg_color=self._hover)
        self._propagate_bg(self, self._hover)

    def _on_leave(self, _e=None):
        self.configure(fg_color=self._base)
        self._propagate_bg(self, self._base)

    def _propagate_bg(self, widget, color: str) -> None:
        for child in widget.winfo_children():
            try:
                if isinstance(child, ctk.CTkFrame) and child.cget("fg_color") in (
                    self._base,
                    self._hover,
                    "transparent",
                ):
                    if child.cget("fg_color") != "transparent":
                        child.configure(fg_color=color)
                    self._propagate_bg(child, color)
                elif isinstance(child, ctk.CTkLabel):
                    try:
                        if child.cget("fg_color") in (self._base, self._hover):
                            child.configure(fg_color=color)
                    except Exception:
                        pass
                    self._propagate_bg(child, color)
            except Exception:
                pass


# ---- app shell --------------------------------------------------------------


SECTIONS = [
    ("mods", "Mods", "📦"),
    ("worlds", "Worlds", "🌍"),
    ("sync", "Sync", "☁"),
    ("storage", "Storage", "💾"),
]


class FragApp(ctk.CTk):
    def __init__(self):
        _debug = os.environ.get("FRAG_DEBUG_FREEZES")
        if _debug:
            import time as _t, logging as _l
            _phase_t0 = _t.perf_counter()
            def _phase(name):
                nonlocal _phase_t0
                now = _t.perf_counter()
                _l.warning("FragApp.__init__ %s: %.0fms", name, (now - _phase_t0) * 1000)
                _phase_t0 = now
        else:
            def _phase(_name): pass

        super().__init__(fg_color=BG)
        _phase("super().__init__")
        self.title("Frag — Mod Manager")
        self.geometry("1240x760")
        self.minsize(1040, 660)
        self._apply_icon()
        _phase("apply_icon")

        self.cfg = Config.load()
        self.client = FragClient(self.cfg)
        self._status_var = ctk.StringVar(value="Ready.")
        self._conn_text = ctk.StringVar(value="offline")
        self._views: dict[str, ctk.CTkFrame] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._current_section = "mods"
        _phase("config + client")

        self._build_sidebar()
        _phase("build_sidebar")
        self._build_content()
        _phase("build_content")
        self._build_status_bar()
        _phase("build_status_bar")

        self._show_section("mods")
        _phase("show_section('mods')")
        # Stagger the initial refreshes so they don't both flood the main
        # thread with widget-destroy + render work on the same tick.  The
        # user lands on Mods first, so prioritize that scan; Worlds can
        # wait until Mods has a chance to start animating its spinner.
        self.after(150, self.mods_view.refresh_async)
        self.after(450, self.worlds_view.refresh)
        self.after(200, self.refresh_connection)
        self.after(60_000, self._schedule_connection_check)
        # Global keyboard shortcuts
        self.bind("<Control-Key-1>", lambda _e: self._show_section("mods"))
        self.bind("<Control-Key-2>", lambda _e: self._show_section("worlds"))
        self.bind("<Control-Key-3>", lambda _e: self._show_section("sync"))
        self.bind("<Control-Key-4>", lambda _e: self._show_section("settings"))
        self.bind("<Control-r>", lambda _e: self._refresh_current())

    # ---- layout -------------------------------------------------------------

    def _apply_icon(self) -> None:
        try:
            ico = asset(ICON_ICO)
            if ico.is_file():
                self.iconbitmap(default=str(ico))
            png = asset(ICON_PNG)
            if png.is_file():
                self._icon_photo = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon_photo)
        except tk.TclError:
            pass

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, fg_color=SIDEBAR, width=220, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Brand header — icon image + wordmark, with violet bar fallback
        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=20, pady=(24, 8))

        if not self._mount_brand_icon(brand):
            ctk.CTkFrame(
                brand, fg_color=PRIMARY, width=4, height=28, corner_radius=2
            ).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(brand, text="FRAG", font=FONT_DISPLAY, text_color=TEXT).pack(
            side="left"
        )

        ctk.CTkLabel(
            sidebar, text="mod manager", font=FONT_TINY, text_color=TEXT_FAINT
        ).pack(anchor="w", padx=24, pady=(0, 24))

        # Divider
        ctk.CTkFrame(sidebar, fg_color=DIVIDER, height=1).pack(fill="x", padx=16)

        # Nav buttons
        nav = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav.pack(fill="x", pady=(16, 0))
        for key, label, glyph in SECTIONS:
            btn = ctk.CTkButton(
                nav,
                text=f"  {glyph}    {label}",
                anchor="w",
                font=FONT_NAV,
                fg_color="transparent",
                hover_color=SURFACE_ALT,
                text_color=TEXT_DIM,
                corner_radius=8,
                height=40,
                command=lambda k=key: self._show_section(k),
            )
            btn.pack(fill="x", padx=12, pady=2)
            self._nav_buttons[key] = btn  # Capture nav_buttons before loop completes

        # Bottom: connection chip + settings entry (click to re-check)
        chip_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        chip_row.pack(side="bottom", fill="x", padx=20, pady=(8, 16))
        chip_row.bind("<Button-1>", lambda _e: self.refresh_connection())
        self._conn_chip_dot = ctk.CTkLabel(
            chip_row, text="●", text_color=TEXT_FAINT, font=("Segoe UI", 12)
        )
        self._conn_chip_dot.pack(side="left")
        self._conn_chip_dot.bind("<Button-1>", lambda _e: self.refresh_connection())
        _conn_text_lbl = ctk.CTkLabel(
            chip_row, textvariable=self._conn_text, font=FONT_DIM, text_color=TEXT_DIM
        )
        _conn_text_lbl.pack(side="left", padx=(6, 0))
        _conn_text_lbl.bind("<Button-1>", lambda _e: self.refresh_connection())

        ctk.CTkFrame(sidebar, fg_color=DIVIDER, height=1).pack(
            side="bottom", fill="x", padx=16, pady=(0, 8)
        )

        settings_btn = ctk.CTkButton(
            sidebar,
            text="  ⚙    Settings",
            anchor="w",
            font=FONT_NAV,
            fg_color="transparent",
            hover_color=SURFACE_ALT,
            text_color=TEXT_DIM,
            corner_radius=8,
            height=40,
            command=lambda: self._show_section("settings"),
        )
        settings_btn.pack(side="bottom", fill="x", padx=12, pady=2)
        self._nav_buttons["settings"] = settings_btn

    def _mount_brand_icon(self, parent) -> bool:
        try:
            from PIL import Image  # type: ignore
        except ImportError:
            return False
        try:
            img = Image.open(asset(ICON_PNG))
            self._brand_ctk = ctk.CTkImage(
                light_image=img, dark_image=img, size=(28, 28)
            )
            ctk.CTkLabel(parent, image=self._brand_ctk, text="").pack(
                side="left", padx=(0, 10)
            )
            return True
        except (OSError, FileNotFoundError):
            return False

    def _build_content(self) -> None:
        _debug = os.environ.get("FRAG_DEBUG_FREEZES")
        if _debug:
            import time as _t, logging as _l
            def _v(name, fn):
                t0 = _t.perf_counter()
                v = fn()
                _l.warning("  build view %s: %.0fms", name, (_t.perf_counter() - t0) * 1000)
                return v
        else:
            def _v(_name, fn): return fn()

        content = ctk.CTkFrame(self, fg_color=BG)
        content.pack(side="left", fill="both", expand=True)

        # Build each view as a hidden frame
        self.mods_view = _v("ModsView", lambda: ModsView(content, self))
        self.worlds_view = _v("WorldsView", lambda: WorldsView(content, self))
        self.sync_view = _v("SyncView", lambda: SyncView(content, self))
        self.storage_view = _v("StorageView", lambda: StorageView(content, self))
        self.settings_view = _v("SettingsView", lambda: SettingsView(content, self))

        self._views = {
            "mods": self.mods_view,
            "worlds": self.worlds_view,
            "sync": self.sync_view,
            "storage": self.storage_view,
            "settings": self.settings_view,
        }

    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=SURFACE, height=28, corner_radius=0)
        bar.pack(side="bottom", fill="x")
        ctk.CTkLabel(
            bar,
            textvariable=self._status_var,
            font=FONT_TINY,
            text_color=TEXT_DIM,
            anchor="w",
        ).pack(side="left", padx=16, pady=5)

    def _show_section(self, key: str) -> None:
        prev = self._current_section
        for view in self._views.values():
            view.pack_forget()
        view = self._views[key]
        view.pack(fill="both", expand=True, padx=24, pady=24)
        # Nav highlight
        for k, btn in self._nav_buttons.items():
            if k == key:
                btn.configure(fg_color=PRIMARY_SOFT, text_color=TEXT)
            else:
                btn.configure(fg_color="transparent", text_color=TEXT_DIM)
        self._current_section = key
        # Auto-actions when switching tabs
        if key == "sync" and prev != "sync":
            self.after(50, self.sync_view.refresh_files)
        if key == "storage" and prev != "storage":
            self.after(50, self.storage_view.refresh_async)

    # ---- helpers ------------------------------------------------------------

    def set_status(self, msg: str) -> None:
        self._status_var.set(msg)

    # Any main-thread callback that runs longer than this is logged as a
    # freeze candidate.  Helps catch work that should have moved to a
    # worker thread but didn't.  Set FRAG_DEBUG_FREEZES=1 to enable.
    _MAIN_THREAD_FREEZE_THRESHOLD_MS = 200

    def _maybe_log_freeze(self, label: str, started_at: float) -> None:
        if not os.environ.get("FRAG_DEBUG_FREEZES"):
            return
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        if elapsed_ms >= self._MAIN_THREAD_FREEZE_THRESHOLD_MS:
            import logging
            logging.getLogger("frag.ui").warning(
                "main-thread freeze: %s took %.0f ms", label, elapsed_ms,
            )

    def run_in_thread(
        self,
        fn,
        *args,
        on_done=None,
        on_error=None,
        on_progress=None,
        status: str = "",
    ) -> None:
        """Run a function in a background thread with UI callbacks."""
        if status:
            self.set_status(status)

        # Capture the calling site for the freeze logger.  fn.__qualname__
        # is the closure name (often "work"); fall back to function name.
        label = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", "fn")

        def worker():
            try:
                if on_progress:
                    result = fn(
                        *args, progress=lambda d, t: self.after(0, on_progress, d, t)
                    )
                else:
                    result = fn(*args)
            except Exception as e:
                self.after(0, self._handle_error, e, traceback.format_exc(), on_error)
                return

            def on_complete():
                started = time.perf_counter()
                if on_done:
                    on_done(result)
                self.set_status("Ready.")
                self._maybe_log_freeze(f"on_done({label})", started)

            self.after(0, on_complete)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_error(self, e: Exception, _tb: str, on_error) -> None:
        self.set_status(f"Error: {e}")
        if on_error:
            on_error(e)
        else:
            messagebox.showerror("Frag", f"{type(e).__name__}: {e}")

    def refresh_connection(self) -> None:
        def work():
            return self.client.ping()

        def done(ok: bool):
            if ok:
                self._conn_text.set("online")
                self._conn_chip_dot.configure(text_color=SUCCESS)
            else:
                self._conn_text.set("offline")
                self._conn_chip_dot.configure(text_color=TEXT_FAINT)

        self.run_in_thread(work, on_done=done)

    def _schedule_connection_check(self) -> None:
        """Ping the server every 60 s to keep the connection indicator fresh."""
        self.refresh_connection()
        self.after(60_000, self._schedule_connection_check)

    def _refresh_current(self) -> None:
        """Ctrl+R: refresh whichever view is currently shown."""
        if self._current_section == "mods":
            self.mods_view.refresh_async()
        elif self._current_section == "worlds":
            self.worlds_view.refresh()
        elif self._current_section == "sync":
            self.sync_view.refresh_files()


# ---- view base --------------------------------------------------------------


class View(ctk.CTkFrame):
    """Base for content views. Provides a title + subtitle header."""

    def __init__(self, parent, app: "FragApp", title: str, subtitle: str = ""):
        super().__init__(parent, fg_color=BG)
        self.app = app
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(header, text=title, font=FONT_H1, text_color=TEXT).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(
                header, text=subtitle, font=FONT_DIM, text_color=TEXT_DIM
            ).pack(anchor="w", pady=(2, 0))


# ---- mods view --------------------------------------------------------------


class ModsView(View):
    def __init__(self, parent, app: "FragApp"):
        super().__init__(parent, app, "Mods", "Installed locally")
        self.mods: list[ModInfo] = []
        self._filter = ""
        self._filter_mode = "all"  # "all" | "synced" | "unsynced"
        self._search_entry: ctk.CTkEntry | None = None
        self._filter_btns: dict[str, ctk.CTkButton] = {}
        self._sort_var: ctk.StringVar | None = None
        self._build()

    def _build(self) -> None:
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", pady=(8, 4))

        self.search_var = ctk.StringVar()
        self._search_entry = ctk.CTkEntry(
            toolbar,
            textvariable=self.search_var,
            placeholder_text="Search mods…  (Ctrl+F)",
            fg_color=SURFACE,
            border_color=SURFACE_HI,
            text_color=TEXT,
            height=36,
            width=280,
            corner_radius=8,
        )
        self._search_entry.pack(side="left")
        self.search_var.trace_add("write", lambda *_: self._apply_filter())

        self.count_chip = ctk.CTkLabel(
            toolbar,
            text="—",
            font=FONT_DIM,
            text_color=TEXT_DIM,
            fg_color=SURFACE,
            corner_radius=8,
            padx=12,
            height=36,
        )
        self.count_chip.pack(side="left", padx=(10, 0))

        primary_button(toolbar, text="Refresh", command=self.refresh_async).pack(
            side="right"
        )
        ghost_button(toolbar, text="Open folder", command=self._open_folder).pack(
            side="right", padx=(0, 8)
        )
        ghost_button(
            toolbar, text="Select none", width=110, command=self._select_none
        ).pack(side="right", padx=(0, 8))
        ghost_button(
            toolbar, text="Select all", width=100, command=self._select_all
        ).pack(side="right", padx=(0, 8))

        # Filter chips + sort row
        toolbar2 = ctk.CTkFrame(self, fg_color="transparent")
        toolbar2.pack(fill="x", pady=(0, 6))

        for mode, label in (
            ("all", "All"),
            ("synced", "Synced"),
            ("unsynced", "Not synced"),
        ):
            btn = ctk.CTkButton(
                toolbar2,
                text=label,
                font=FONT_TINY,
                fg_color=PRIMARY_SOFT if mode == "all" else "transparent",
                text_color=TEXT if mode == "all" else TEXT_DIM,
                hover_color=SURFACE_ALT,
                border_width=1,
                border_color=PRIMARY_SOFT if mode == "all" else SURFACE_HI,
                corner_radius=8,
                height=28,
                width=96,
                command=lambda m=mode: self._set_filter_mode(m),
            )
            btn.pack(side="left", padx=(0, 4))
            self._filter_btns[mode] = btn

        self._sort_var = ctk.StringVar(value="Name A–Z")
        ctk.CTkOptionMenu(
            toolbar2,
            variable=self._sort_var,
            values=["Name A–Z", "Name Z–A", "Size ↓", "Size ↑", "Synced first"],
            fg_color=SURFACE,
            button_color=SURFACE_HI,
            button_hover_color=SURFACE_ALT,
            text_color=TEXT_DIM,
            font=FONT_TINY,
            height=28,
            width=130,
            command=lambda _: self._apply_filter(),
        ).pack(side="left", padx=(8, 0))

        self.app.bind("<Control-f>", lambda _e: self._focus_search())

        # Path + warnings
        self.path_label = ctk.CTkLabel(
            self, text="", font=FONT_DIM, text_color=TEXT_FAINT, anchor="w"
        )
        self.path_label.pack(fill="x")
        self.warning_label = ctk.CTkLabel(
            self, text="", font=FONT_DIM, text_color=WARNING, anchor="w"
        )
        self.warning_label.pack(fill="x", pady=(4, 0))

        # Scrollable list inside a card.  Hand-rolled tk.Canvas + inner frame
        # because CTkScrollableFrame has buggy scrollregion updates on bulk
        # repacks and its CTk children are 10x slower than plain tk to lay out.
        list_card = card(self)
        list_card.pack(fill="both", expand=True, pady=(14, 0))

        list_outer = tk.Frame(list_card, bg=SURFACE)
        list_outer.pack(fill="both", expand=True, padx=10, pady=10)

        self._list_canvas = tk.Canvas(
            list_outer, bg=SURFACE, highlightthickness=0, bd=0
        )
        self._list_scroll = ctk.CTkScrollbar(
            list_outer, orientation="vertical",
            command=self._list_canvas.yview,
            button_color=SURFACE_HI,
            button_hover_color=PRIMARY,
        )
        self._list_canvas.configure(yscrollcommand=self._list_scroll.set)
        self._list_scroll.pack(side="right", fill="y")
        self._list_canvas.pack(side="left", fill="both", expand=True)

        self.list_frame = tk.Frame(self._list_canvas, bg=SURFACE)
        self._list_window = self._list_canvas.create_window(
            (0, 0), window=self.list_frame, anchor="nw"
        )

        # Keep inner frame width pinned to the canvas width, and update the
        # scrollregion whenever the inner frame's height changes.
        def _on_canvas_configure(event):
            self._list_canvas.itemconfigure(self._list_window, width=event.width)
        def _on_inner_configure(_event):
            self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))
        self._list_canvas.bind("<Configure>", _on_canvas_configure)
        self.list_frame.bind("<Configure>", _on_inner_configure)

        # Mouse-wheel scroll over the list area.
        def _on_wheel(event):
            self._list_canvas.yview_scroll(-int(event.delta / 120), "units")
        self._list_canvas.bind("<Enter>", lambda _e: self._list_canvas.bind_all("<MouseWheel>", _on_wheel))
        self._list_canvas.bind("<Leave>", lambda _e: self._list_canvas.unbind_all("<MouseWheel>"))

        # Cache of PhotoImage objects keyed by mod.sha1 so they survive GC
        # and we don't decode the same PNG twice.
        self._icon_cache: dict[str, tk.PhotoImage] = {}

    def _open_folder(self) -> None:
        import os

        path = self.app.cfg.mods_path
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo("Frag", str(path))

    def refresh_async(self) -> None:
        self.path_label.configure(text=str(self.app.cfg.mods_path))
        self.warning_label.configure(text="")
        self._set_loading()

        def work():
            # Scan + decode all PIL icon images off the main thread.  The
            # only thing left for the UI thread is the cheap ImageTk wrap
            # (which has to happen on the main thread because Tk image
            # objects are root-bound) and packing the row widgets.
            mods, warnings = scan_mods(self.app.cfg.mods_path)
            decoded = self._decode_all_mod_icons(mods)
            return mods, warnings, decoded

        def done(result):
            mods, warnings, decoded = result
            self.mods = mods
            self._pending_icon_images = decoded
            self.warning_label.configure(
                text=("  " + "  ".join(warnings)) if warnings else ""
            )
            self._update_count_chip()
            self._apply_filter()
            self.app.sync_view.refresh_selection_chip()

        self.app.run_in_thread(work, on_done=done, status="Scanning mods…")

    def _set_loading(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        # Stop any previous spinner before starting a new one.
        if getattr(self, "_spinner_after_id", None):
            try:
                self.after_cancel(self._spinner_after_id)
            except tk.TclError:
                pass
            self._spinner_after_id = None

        wrap = tk.Frame(self.list_frame, bg=SURFACE)
        wrap.pack(pady=48)

        spinner = tk.Canvas(
            wrap, width=42, height=42, bg=SURFACE, highlightthickness=0, bd=0,
        )
        spinner.pack()
        # 12-arm circular spinner: 12 short lines whose alpha-equivalent
        # (greyscale) rotates each tick to fake the classic Material spinner.
        arms = []
        import math
        for i in range(12):
            angle = math.radians(i * 30 - 90)
            x0 = 21 + math.cos(angle) * 11
            y0 = 21 + math.sin(angle) * 11
            x1 = 21 + math.cos(angle) * 18
            y1 = 21 + math.sin(angle) * 18
            arms.append(spinner.create_line(x0, y0, x1, y1, width=3, capstyle="round"))

        label = tk.Label(
            wrap, text="Scanning mods…", bg=SURFACE, fg=TEXT_DIM,
            font=FONT_BODY,
        )
        label.pack(pady=(12, 0))

        # Greyscale ramp from bright to dim, 12 stops.
        ramp = ("#F4F4F8", "#D6D6E0", "#B8B8C8", "#9A9AB0", "#7C7C98",
                "#5E5E80", "#404068", "#383858", "#303048", "#28283A",
                "#20202E", "#18181E")

        step = {"i": 0}

        def tick():
            for k, arm in enumerate(arms):
                spinner.itemconfigure(arm, fill=ramp[(k + step["i"]) % 12])
            step["i"] = (step["i"] + 1) % 12
            self._spinner_after_id = self.after(80, tick)

        tick()
        self._spinner_widgets = (wrap, spinner)

    def _update_count_chip(self) -> None:
        total = len(self.mods)
        synced = sum(1 for m in self.mods if self.app.cfg.is_mod_synced(m.sha1))
        self.count_chip.configure(text=f"{total} found  ·  {synced} selected")

    def _focus_search(self) -> None:
        """Ctrl+F: navigate to Mods and focus the search box."""
        self.app._show_section("mods")
        if self._search_entry:
            self._search_entry.focus_set()
            self._search_entry.select_range(0, "end")

    def _set_filter_mode(self, mode: str) -> None:
        self._filter_mode = mode
        for m, btn in self._filter_btns.items():
            if m == mode:
                btn.configure(
                    fg_color=PRIMARY_SOFT, text_color=TEXT, border_color=PRIMARY_SOFT
                )
            else:
                btn.configure(
                    fg_color="transparent", text_color=TEXT_DIM, border_color=SURFACE_HI
                )
        self._apply_filter()

    def _apply_filter(self) -> None:
        # Bump the generation counter first.  Any in-flight chunked render
        # from a prior _apply_filter() will see its `gen` no longer matches
        # and abort on its next tick, so we don't have to wait for it to
        # finish before we start tearing down its widgets.
        self._render_gen = getattr(self, "_render_gen", 0) + 1
        q = self.search_var.get().strip().lower()
        # Synchronous destroy.  At ~1ms per row this is fine for typical
        # mod folders; chunking it instead would risk new rows landing on
        # top of half-destroyed old ones if the user mashes Refresh.
        for child in self.list_frame.winfo_children():
            child.destroy()
        filtered = [
            m
            for m in self.mods
            if (
                not q
                or q in m.best_name.lower()
                or q in m.filename.lower()
                or q in (m.mod_id or "").lower()
            )
            and (
                self._filter_mode == "all"
                or (
                    self._filter_mode == "synced" and self.app.cfg.is_mod_synced(m.sha1)
                )
                or (
                    self._filter_mode == "unsynced"
                    and not self.app.cfg.is_mod_synced(m.sha1)
                )
            )
        ]
        sort_key = self._sort_var.get() if self._sort_var else "Name A–Z"
        if sort_key == "Name A–Z":
            filtered.sort(key=lambda m: m.best_name.lower())
        elif sort_key == "Name Z–A":
            filtered.sort(key=lambda m: m.best_name.lower(), reverse=True)
        elif sort_key == "Size ↓":
            filtered.sort(key=lambda m: m.size, reverse=True)
        elif sort_key == "Size ↑":
            filtered.sort(key=lambda m: m.size)
        elif sort_key == "Synced first":
            filtered.sort(
                key=lambda m: (
                    not self.app.cfg.is_mod_synced(m.sha1),
                    m.best_name.lower(),
                )
            )
        if not filtered:
            no_mods = not self.mods
            _empty_state(
                self.list_frame,
                title="No mods" if no_mods else "No matches",
                hint=(
                    "Drop .jar files into your mods folder, then Refresh."
                    if no_mods
                    else (
                        f'Nothing matches "{q}".'
                        if q
                        else f"No {self._filter_mode} mods."
                    )
                ),
            )
            return
        # Cancel any running spinner now that real content is coming in.
        if getattr(self, "_spinner_after_id", None):
            try:
                self.after_cancel(self._spinner_after_id)
            except tk.TclError:
                pass
            self._spinner_after_id = None

        self._render_rows_chunked(filtered, self._render_gen)

    def _render_rows_chunked(
        self, filtered: list[ModInfo], gen: int, start: int = 0,
        chunk: int = 6,
    ) -> None:
        """Pack rows in small batches, yielding to the event loop between.

        Without chunking, rendering ~80 rows synchronously blocks the UI
        for a few hundred ms — long enough to see the window stall.
        Yielding every *chunk* rows lets the spinner / scrollbar / mouse
        stay responsive while the list fills in.
        """
        if gen != getattr(self, "_render_gen", gen):
            return  # superseded by a newer _apply_filter
        end = min(start + chunk, len(filtered))
        for mod in filtered[start:end]:
            self._render_row(mod)
        if end < len(filtered):
            self.after(1, self._render_rows_chunked, filtered, gen, end, chunk)
            return
        # Final batch done — refresh scrollregion.
        self.list_frame.update_idletasks()
        self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))

    def _render_row(self, mod: ModInfo) -> None:
        # Plain tk widgets are dramatically faster than CTk equivalents.
        # We lose rounded corners on the row itself but gain ~10x render speed.
        row = tk.Frame(self.list_frame, bg=SURFACE, height=64)
        row.pack(fill="x", padx=2, pady=3)
        row.pack_propagate(False)

        # Sync checkbox (CTk because it's the only widget the user clicks
        # frequently on this row and styling matters).
        var = ctk.BooleanVar(value=self.app.cfg.is_mod_synced(mod.sha1))
        cb = ctk.CTkCheckBox(
            row, text="", variable=var, width=20,
            checkbox_width=18, checkbox_height=18,
            corner_radius=4, fg_color=PRIMARY, hover_color=PRIMARY_HOV,
            border_color=SURFACE_HI,
            command=lambda sha1=mod.sha1, v=var: self._on_toggle(sha1, v.get()),
        )
        cb.pack(side="left", padx=(14, 6))

        # Mod icon: real image when the jar shipped one, colored letter otherwise.
        icon_img = self._icon_for(mod)
        icon_lbl = tk.Label(row, image=icon_img, bg=SURFACE, bd=0)
        icon_lbl.image = icon_img  # type: ignore[attr-defined]  # keep ref alive
        icon_lbl.pack(side="left", padx=(8, 14))

        # Name + meta (plain tk)
        left = tk.Frame(row, bg=SURFACE)
        left.pack(side="left", fill="both", expand=True)
        tk.Label(
            left, text=mod.best_name, bg=SURFACE, fg=TEXT,
            font=FONT_H2, anchor="w",
        ).pack(fill="x", pady=(11, 0))
        meta = []
        if mod.best_version != "?":
            meta.append(f"v{mod.best_version}")
        if mod.loader:
            meta.append(mod.loader)
        meta.append(f"{mod.size / 1024 / 1024:.1f} MB")
        tk.Label(
            left, text="  ·  ".join(meta), bg=SURFACE, fg=TEXT_DIM,
            font=FONT_DIM, anchor="w",
        ).pack(fill="x", pady=(2, 0))

        # Right: status pill (CTk for rounded look) + optional Modrinth link.
        right = tk.Frame(row, bg=SURFACE)
        right.pack(side="right", padx=14)
        color, soft, text = _mod_status(mod)
        _pill(right, text, color, soft).pack(side="right", pady=18)
        if mod.modrinth_page_url:
            ghost_button(
                right, text="↗", width=32, height=28,
                command=lambda url=mod.modrinth_page_url: webbrowser.open(url),
            ).pack(side="right", padx=(0, 6), pady=18)

    # Per-mod icon pipeline.
    #
    # The heavy work (PIL open / resize / rounded mask) runs on a worker
    # thread via _decode_all_mod_icons() and is stored in _image_cache as
    # raw PIL Images.  Only the cheap ImageTk.PhotoImage wrap happens on
    # the main thread inside _icon_for(), because Tk image objects are
    # bound to the Tk root and can only be created from the UI thread.

    _ICON_SIZE = 44
    _ICON_RADIUS = 10

    def _decode_all_mod_icons(self, mods: list[ModInfo]) -> dict[str, "object"]:
        """Pre-decode + round all mod icons in a thread pool.

        Returns ``{sha1: PIL.Image}``.  Skips entries already in
        ``_image_cache`` so repeat refreshes are nearly free.  Safe to
        call from any thread.
        """
        from concurrent.futures import ThreadPoolExecutor
        cache = getattr(self, "_image_cache", None)
        if cache is None:
            cache = self._image_cache = {}
        todo = [m for m in mods if m.sha1 not in cache]
        if not todo:
            return cache

        # PIL releases the GIL during decode/resize, so threads help.
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(self._decode_icon_image_for_mod, todo))
        for mod, img in zip(todo, results):
            cache[mod.sha1] = img
        return cache

    def _decode_icon_image_for_mod(self, mod: ModInfo):
        """Pure-PIL: produce a 44x44 rounded RGBA Image for *mod*.

        Must NOT touch Tk — runs on worker threads.
        """
        from PIL import Image, ImageDraw
        import io

        size = self._ICON_SIZE
        if mod.icon_bytes:
            try:
                src = Image.open(io.BytesIO(mod.icon_bytes)).convert("RGBA")
                src.thumbnail((size * 2, size * 2), Image.LANCZOS)
                w, h = src.size
                side = min(w, h)
                left = (w - side) // 2
                top = (h - side) // 2
                src = src.crop((left, top, left + side, top + side))
                base = src.resize((size, size), Image.LANCZOS)
            except Exception:
                base = self._fallback_avatar(mod, size)
        else:
            base = self._fallback_avatar(mod, size)

        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, size, size), radius=self._ICON_RADIUS, fill=255,
        )
        rounded = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        rounded.paste(base, (0, 0), mask)
        return rounded

    def _icon_for(self, mod: ModInfo) -> tk.PhotoImage:
        """Return a Tk PhotoImage for *mod*, using the pre-decoded Image cache.

        Must run on the main thread (creates a Tk image).  Cheap: just a
        ImageTk.PhotoImage wrap of a PIL Image that the worker already
        decoded.  Falls back to synchronous decode if the cache miss is
        from a code path that didn't go through _decode_all_mod_icons.
        """
        cached = self._icon_cache.get(mod.sha1)
        if cached is not None:
            return cached

        from PIL import ImageTk
        image_cache = getattr(self, "_image_cache", None) or {}
        pil_image = image_cache.get(mod.sha1)
        if pil_image is None:
            # Worker hasn't decoded this one yet (e.g. row added via search
            # before a re-scan finished).  Do it inline; rare path.
            pil_image = self._decode_icon_image_for_mod(mod)
            image_cache[mod.sha1] = pil_image

        if os.environ.get("FRAG_DEBUG_FREEZES"):
            import time as _t, logging as _l
            t0 = _t.perf_counter()
            photo = ImageTk.PhotoImage(pil_image)
            elapsed = (_t.perf_counter() - t0) * 1000
            if elapsed > 5:
                _l.warning("    ImageTk.PhotoImage(%s): %.1fms", mod.best_name[:30], elapsed)
        else:
            photo = ImageTk.PhotoImage(pil_image)
        self._icon_cache[mod.sha1] = photo
        return photo

    def _fallback_avatar(self, mod: ModInfo, size: int) -> "Image.Image":  # type: ignore[name-defined]
        """Colored square with the mod's initial — used when no icon was shipped."""
        from PIL import Image, ImageDraw, ImageFont

        # Stable per-mod color so the same mod gets the same swatch each scan.
        palette = (
            "#8B5CF6", "#EC4899", "#F59E0B", "#10B981",
            "#06B6D4", "#6366F1", "#EF4444", "#14B8A6",
        )
        seed = int(mod.sha1[:8], 16) if mod.sha1 else 0
        color = palette[seed % len(palette)]

        bg = Image.new("RGBA", (size, size), color)
        draw = ImageDraw.Draw(bg)
        letter = (mod.best_name[:1] or "?").upper()
        try:
            font = ImageFont.truetype("seguibl.ttf", 22)
        except OSError:
            try:
                font = ImageFont.truetype("arial.ttf", 22)
            except OSError:
                font = ImageFont.load_default()
        # Center the letter.
        bbox = draw.textbbox((0, 0), letter, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
            letter, fill="#FFFFFF", font=font,
        )
        return bg

    def _on_toggle(self, sha1: str, synced: bool) -> None:
        self.app.cfg.set_mod_synced(sha1, synced)
        self.app.cfg.save()
        self._update_count_chip()
        self.app.sync_view.refresh_selection_chip()

    def _select_all(self) -> None:
        for mod in self.mods:
            self.app.cfg.set_mod_synced(mod.sha1, True)
        self.app.cfg.save()
        self._update_count_chip()
        self.app.sync_view.refresh_selection_chip()
        self._apply_filter()

    def _select_none(self) -> None:
        for mod in self.mods:
            self.app.cfg.set_mod_synced(mod.sha1, False)
        self.app.cfg.save()
        self._update_count_chip()
        self.app.sync_view.refresh_selection_chip()
        self._apply_filter()


def _mod_status(mod: ModInfo) -> tuple[str, str, str]:
    if mod.metadata_source == "modrinth":
        return SUCCESS, SUCCESS_SOFT, "verified"
    if mod.metadata_source == "jar":
        return PRIMARY, PRIMARY_SOFT, "local"
    return TEXT_DIM, SURFACE_HI, "unknown"


# ---- worlds view ------------------------------------------------------------


class WorldsView(View):
    def __init__(self, parent, app: "FragApp"):
        super().__init__(parent, app, "Worlds", "Local saves")
        self.worlds: list[Path] = []
        self._rows: dict[str, dict] = {}
        self._build()

    def _build(self) -> None:
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", pady=(8, 12))

        self.count_chip = ctk.CTkLabel(
            toolbar,
            text="—",
            font=FONT_DIM,
            text_color=TEXT_DIM,
            fg_color=SURFACE,
            corner_radius=8,
            padx=12,
            height=36,
        )
        self.count_chip.pack(side="left")

        primary_button(toolbar, text="Refresh", command=self.refresh).pack(side="right")
        ghost_button(
            toolbar, text="Select none", width=110, command=self._select_none
        ).pack(side="right", padx=(0, 8))
        ghost_button(
            toolbar, text="Select all", width=100, command=self._select_all
        ).pack(side="right", padx=(0, 8))

        self.path_label = ctk.CTkLabel(
            self, text="", font=FONT_DIM, text_color=TEXT_FAINT, anchor="w"
        )
        self.path_label.pack(fill="x")

        # Same hand-rolled canvas list as ModsView — see that view for rationale.
        list_card = card(self)
        list_card.pack(fill="both", expand=True, pady=(14, 0))

        list_outer = tk.Frame(list_card, bg=SURFACE)
        list_outer.pack(fill="both", expand=True, padx=10, pady=10)

        self._list_canvas = tk.Canvas(
            list_outer, bg=SURFACE, highlightthickness=0, bd=0
        )
        self._list_scroll = ctk.CTkScrollbar(
            list_outer, orientation="vertical",
            command=self._list_canvas.yview,
            button_color=SURFACE_HI,
            button_hover_color=PRIMARY,
        )
        self._list_canvas.configure(yscrollcommand=self._list_scroll.set)
        self._list_scroll.pack(side="right", fill="y")
        self._list_canvas.pack(side="left", fill="both", expand=True)

        self.list_frame = tk.Frame(self._list_canvas, bg=SURFACE)
        self._list_window = self._list_canvas.create_window(
            (0, 0), window=self.list_frame, anchor="nw"
        )

        def _on_canvas_configure(event):
            self._list_canvas.itemconfigure(self._list_window, width=event.width)
        def _on_inner_configure(_event):
            self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))
        self._list_canvas.bind("<Configure>", _on_canvas_configure)
        self.list_frame.bind("<Configure>", _on_inner_configure)

        def _on_wheel(event):
            self._list_canvas.yview_scroll(-int(event.delta / 120), "units")
        self._list_canvas.bind("<Enter>", lambda _e: self._list_canvas.bind_all("<MouseWheel>", _on_wheel))
        self._list_canvas.bind("<Leave>", lambda _e: self._list_canvas.unbind_all("<MouseWheel>"))

        # World-icon PhotoImage cache (key = world.name).  Held to prevent GC.
        self._icon_cache: dict[str, tk.PhotoImage] = {}

    def refresh(self) -> None:
        self.path_label.configure(text=str(self.app.cfg.saves_path))

        for child in self.list_frame.winfo_children():
            child.destroy()
        self._rows.clear()
        if getattr(self, "_spinner_after_id", None):
            try:
                self.after_cancel(self._spinner_after_id)
            except tk.TclError:
                pass
            self._spinner_after_id = None
        self._set_loading()

        def work():
            saves = self.app.cfg.saves_path
            worlds: list[Path] = []
            if saves.is_dir():
                worlds = sorted(p for p in saves.iterdir() if p.is_dir())
            # Decode every world icon off the main thread.  Per-world PIL
            # work dominates render time, so this is what made the UI hitch.
            self._decode_all_world_icons(worlds)
            return worlds

        def done(worlds: list[Path]) -> None:
            self.worlds = worlds
            self.count_chip.configure(text=f"{len(worlds)} found")
            # Clear the spinner before we start packing real rows.
            for child in self.list_frame.winfo_children():
                child.destroy()
            if getattr(self, "_spinner_after_id", None):
                try:
                    self.after_cancel(self._spinner_after_id)
                except tk.TclError:
                    pass
                self._spinner_after_id = None

            if not worlds:
                _empty_state(
                    self.list_frame,
                    title="No worlds",
                    hint="Worlds appear here once you've played at least once.",
                )
                return

            self._render_gen = getattr(self, "_render_gen", 0) + 1
            self._render_worlds_chunked(worlds, self._render_gen)

            self.app.sync_view.refresh_selection_chip()
            self.app.run_in_thread(
                self._inspect_all,
                worlds,
                on_done=lambda _r: None,
                status="Inspecting worlds…",
            )

        self.app.run_in_thread(work, on_done=done, status="Scanning worlds…")

    def _render_worlds_chunked(
        self, worlds: list[Path], gen: int, start: int = 0, chunk: int = 6,
    ) -> None:
        if gen != getattr(self, "_render_gen", gen):
            return
        end = min(start + chunk, len(worlds))
        for world in worlds[start:end]:
            self._render_row(world)
        if end < len(worlds):
            self.after(1, self._render_worlds_chunked, worlds, gen, end, chunk)
            return
        self.list_frame.update_idletasks()
        self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))

    def _set_loading(self) -> None:
        # Same spinner as ModsView — see that view for the full shape.
        for child in self.list_frame.winfo_children():
            child.destroy()
        wrap = tk.Frame(self.list_frame, bg=SURFACE)
        wrap.pack(pady=48)
        spinner = tk.Canvas(
            wrap, width=42, height=42, bg=SURFACE, highlightthickness=0, bd=0,
        )
        spinner.pack()
        arms = []
        import math
        for i in range(12):
            angle = math.radians(i * 30 - 90)
            x0 = 21 + math.cos(angle) * 11
            y0 = 21 + math.sin(angle) * 11
            x1 = 21 + math.cos(angle) * 18
            y1 = 21 + math.sin(angle) * 18
            arms.append(spinner.create_line(x0, y0, x1, y1, width=3, capstyle="round"))
        tk.Label(
            wrap, text="Scanning worlds…", bg=SURFACE, fg=TEXT_DIM, font=FONT_BODY,
        ).pack(pady=(12, 0))
        ramp = ("#F4F4F8", "#D6D6E0", "#B8B8C8", "#9A9AB0", "#7C7C98",
                "#5E5E80", "#404068", "#383858", "#303048", "#28283A",
                "#20202E", "#18181E")
        step = {"i": 0}
        def tick():
            for k, arm in enumerate(arms):
                spinner.itemconfigure(arm, fill=ramp[(k + step["i"]) % 12])
            step["i"] = (step["i"] + 1) % 12
            self._spinner_after_id = self.after(80, tick)
        tick()

    def _render_row(self, world: Path) -> None:
        row = tk.Frame(self.list_frame, bg=SURFACE, height=64)
        row.pack(fill="x", padx=2, pady=3)
        row.pack_propagate(False)

        var = ctk.BooleanVar(value=self.app.cfg.is_world_synced(world.name))
        cb = ctk.CTkCheckBox(
            row, text="", variable=var, width=20,
            checkbox_width=18, checkbox_height=18,
            corner_radius=4, fg_color=PRIMARY, hover_color=PRIMARY_HOV,
            border_color=SURFACE_HI,
            command=lambda n=world.name, v=var: self._on_toggle(n, v.get()),
        )
        cb.pack(side="left", padx=(14, 6))

        # World icon (Minecraft writes <world>/icon.png automatically).
        icon_img = self._icon_for(world)
        icon_lbl = tk.Label(row, image=icon_img, bg=SURFACE, bd=0)
        icon_lbl.image = icon_img  # type: ignore[attr-defined]
        icon_lbl.pack(side="left", padx=(8, 14))

        left = tk.Frame(row, bg=SURFACE)
        left.pack(side="left", fill="both", expand=True)
        tk.Label(
            left, text=world.name, bg=SURFACE, fg=TEXT,
            font=FONT_H2, anchor="w",
        ).pack(fill="x", pady=(11, 0))
        meta = tk.Label(
            left, text="inspecting…", bg=SURFACE, fg=TEXT_DIM,
            font=FONT_DIM, anchor="w",
        )
        meta.pack(fill="x", pady=(2, 0))

        right = tk.Frame(row, bg=SURFACE)
        right.pack(side="right", padx=14)
        btn = ghost_button(right, text="Show mods", width=110, state="disabled")
        btn.pack(side="right", pady=18)

        self._rows[world.name] = {
            "meta": meta,
            "btn": btn,
            "right": right,
            "info": None,
            "cb": cb,
            "var": var,
        }

    # World-icon pipeline: same split as ModsView._icon_for.  PIL work
    # happens off-thread via _decode_all_world_icons(); _icon_for() only
    # does the cheap ImageTk wrap on the main thread.

    _ICON_SIZE = 44
    _ICON_RADIUS = 10

    def _decode_all_world_icons(self, worlds: list[Path]) -> dict[str, object]:
        from concurrent.futures import ThreadPoolExecutor
        cache = getattr(self, "_image_cache", None)
        if cache is None:
            cache = self._image_cache = {}
        todo = [w for w in worlds if w.name not in cache]
        if not todo:
            return cache
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(self._decode_icon_image_for_world, todo))
        for world, img in zip(todo, results):
            cache[world.name] = img
        return cache

    def _decode_icon_image_for_world(self, world: Path):
        """Pure-PIL: produce a 44x44 rounded RGBA Image for *world*."""
        from PIL import Image, ImageDraw, ImageFont
        size = self._ICON_SIZE
        png_path = world / "icon.png"
        img = None
        if png_path.is_file():
            try:
                src = Image.open(png_path).convert("RGBA")
                src.thumbnail((size * 3, size * 3), Image.LANCZOS)
                w, h = src.size
                side = min(w, h)
                left = (w - side) // 2
                top = (h - side) // 2
                src = src.crop((left, top, left + side, top + side))
                img = src.resize((size, size), Image.LANCZOS)
            except Exception:
                img = None
        if img is None:
            img = Image.new("RGBA", (size, size), "#EC4899")
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("seguiemj.ttf", 26)
            except OSError:
                try:
                    font = ImageFont.truetype("arial.ttf", 22)
                except OSError:
                    font = ImageFont.load_default()
            letter = (world.name[:1] or "?").upper()
            bbox = draw.textbbox((0, 0), letter, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(
                ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
                letter, fill="#FFFFFF", font=font,
            )
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, size, size), radius=self._ICON_RADIUS, fill=255,
        )
        rounded = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        rounded.paste(img, (0, 0), mask)
        return rounded

    def _icon_for(self, world: Path) -> tk.PhotoImage:
        """Tk-wrap the pre-decoded PIL Image.  Main thread only."""
        cached = self._icon_cache.get(world.name)
        if cached is not None:
            return cached

        from PIL import ImageTk
        image_cache = getattr(self, "_image_cache", None) or {}
        pil_image = image_cache.get(world.name)
        if pil_image is None:
            pil_image = self._decode_icon_image_for_world(world)
            image_cache[world.name] = pil_image

        photo = ImageTk.PhotoImage(pil_image)
        self._icon_cache[world.name] = photo
        return photo

    def _on_toggle(self, name: str, synced: bool) -> None:
        self.app.cfg.set_world_synced(name, synced)
        self.app.cfg.save()
        self.app.sync_view.refresh_selection_chip()

    def _select_all(self) -> None:
        for world in self.worlds:
            self.app.cfg.set_world_synced(world.name, True)
            row = self._rows.get(world.name)
            if row:
                row["var"].set(True)
        self.app.cfg.save()
        self.app.sync_view.refresh_selection_chip()

    def _select_none(self) -> None:
        for world in self.worlds:
            self.app.cfg.set_world_synced(world.name, False)
            row = self._rows.get(world.name)
            if row:
                row["var"].set(False)
        self.app.cfg.save()
        self.app.sync_view.refresh_selection_chip()

    def _inspect_all(self, worlds: list[Path]) -> None:
        from concurrent.futures import ThreadPoolExecutor

        def inspect_one(world: Path) -> tuple[str, "WorldInfo", float]:
            info = read_world(world)
            try:
                size_mb = (
                    sum(p.stat().st_size for p in world.rglob("*") if p.is_file())
                    / 1024
                    / 1024
                )
            except OSError:
                size_mb = 0.0
            return world.name, info, size_mb

        # 8 workers: world inspection walks the whole save dir, which is
        # millions of small files for big worlds — more parallelism here
        # would just thrash the disk.
        with ThreadPoolExecutor(max_workers=8) as ex:
            for name, info, size_mb in ex.map(inspect_one, worlds):
                self.app.after(0, self._apply_info, name, info, size_mb)

    def _apply_info(self, world_name: str, info: WorldInfo, size_mb: float) -> None:
        row = self._rows.get(world_name)
        if not row:
            return
        row["info"] = info
        parts = [f"{size_mb:.1f} MB"]
        if info.mc_version:
            parts.append(f"MC {info.mc_version}")
        elif info.mc_data_version:
            parts.append(f"data v{info.mc_data_version}")
        if info.loader:
            loader_part = info.loader
            if info.loader_version:
                loader_part += f" {info.loader_version}"
            parts.append(loader_part)
        if info.mods:
            parts.append(f"{info.mod_count} mods")
        if info.last_played:
            parts.append(info.last_played)
        row["meta"].configure(text="  ·  ".join(parts))

        # source pill on the right side
        if "source_pill" in row:
            row["source_pill"].destroy()
        if info.source != "none":
            color, soft = (
                (SUCCESS, SUCCESS_SOFT)
                if info.source == "frag-mod"
                else (TEXT_DIM, SURFACE_HI)
            )
            sp = _pill(row["right"], info.source_label, color, soft)
            sp.pack(side="right", padx=(0, 8))
            row["source_pill"] = sp

        btn = row["btn"]
        if info.mods:
            btn.configure(
                state="normal",
                command=lambda n=world_name: self._show_mods_popup(n),
            )
        elif info.source == "level.dat":
            btn.configure(text="Install Frag mod", state="disabled")
        else:
            btn.configure(text="No mod data", state="disabled")

    def _show_mods_popup(self, world_name: str) -> None:
        row = self._rows.get(world_name)
        if not row or not row["info"]:
            return
        info: WorldInfo = row["info"]

        win = ctk.CTkToplevel(self.app, fg_color=BG)
        win.title(f"Frag — {info.name}")
        win.geometry("640x640")
        win.transient(self.app)
        try:
            ico = asset(ICON_ICO)
            if ico.is_file():
                win.iconbitmap(str(ico))
        except tk.TclError:
            pass

        header = ctk.CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(22, 4))
        ctk.CTkLabel(
            header, text=info.name, font=FONT_H1, text_color=TEXT, anchor="w"
        ).pack(anchor="w")

        chips = ctk.CTkFrame(header, fg_color="transparent")
        chips.pack(anchor="w", pady=(8, 0))
        if info.mc_version:
            _pill(chips, f"MC {info.mc_version}", TEXT, SURFACE_ALT).pack(
                side="left", padx=(0, 6)
            )
        if info.loader:
            loader_text = info.loader
            if info.loader_version:
                loader_text += f" {info.loader_version}"
            _pill(chips, loader_text, PRIMARY, PRIMARY_SOFT).pack(
                side="left", padx=(0, 6)
            )
        _pill(chips, f"{info.mod_count} mods", ACCENT, ACCENT_SOFT).pack(
            side="left", padx=(0, 6)
        )
        if info.source != "none":
            color, soft = (
                (SUCCESS, SUCCESS_SOFT)
                if info.source == "frag-mod"
                else (TEXT_DIM, SURFACE_HI)
            )
            _pill(chips, info.source_label, color, soft).pack(side="left", padx=(0, 6))
        if info.hardcore:
            _pill(chips, "hardcore", ERROR, ERROR_SOFT).pack(side="left", padx=(0, 6))

        # Secondary meta line — seed, difficulty, gamerule/dimension counts
        meta_parts = []
        if info.seed is not None:
            meta_parts.append(f"seed {info.seed}")
        if info.difficulty:
            meta_parts.append(info.difficulty)
        if info.game_type:
            meta_parts.append(info.game_type)
        if info.gamerules:
            meta_parts.append(f"{len(info.gamerules)} gamerules")
        if info.dimensions:
            meta_parts.append(f"{len(info.dimensions)} dims")
        if info.last_played:
            meta_parts.append(f"played {info.last_played}")
        if meta_parts:
            ctk.CTkLabel(
                header,
                text="  ·  ".join(meta_parts),
                font=FONT_DIM,
                text_color=TEXT_FAINT,
                anchor="w",
            ).pack(anchor="w", pady=(8, 0))

        body_card = card(win)
        body_card.pack(fill="both", expand=True, padx=24, pady=(16, 24))
        body = ctk.CTkScrollableFrame(
            body_card,
            fg_color=SURFACE,
            scrollbar_button_color=SURFACE_HI,
            scrollbar_button_hover_color=PRIMARY,
        )
        body.pack(fill="both", expand=True, padx=8, pady=8)

        for mod in info.mods:
            row_frame = HoverRow(body, base=SURFACE, hover=SURFACE_ALT)
            row_frame.pack(fill="x", padx=2, pady=3)

            left = ctk.CTkFrame(row_frame, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=14, pady=8)
            ctk.CTkLabel(
                left,
                text=mod.best_name,
                font=FONT_BODY,
                text_color=TEXT,
                anchor="w",
            ).pack(anchor="w")
            if mod.display_name and mod.mod_id and mod.display_name != mod.mod_id:
                ctk.CTkLabel(
                    left,
                    text=mod.mod_id,
                    font=FONT_TINY,
                    text_color=TEXT_FAINT,
                    anchor="w",
                ).pack(anchor="w")
            if mod.description:
                desc = mod.description
                if len(desc) > 140:
                    desc = desc[:137].rstrip() + "…"
                ctk.CTkLabel(
                    left,
                    text=desc,
                    font=FONT_TINY,
                    text_color=TEXT_DIM,
                    anchor="w",
                    wraplength=420,
                    justify="left",
                ).pack(anchor="w", pady=(2, 0))

            ctk.CTkLabel(
                row_frame,
                text=mod.version or "?",
                font=FONT_MONO,
                text_color=TEXT_DIM,
                anchor="e",
            ).pack(side="right", padx=14, pady=8)


# ---- sync view --------------------------------------------------------------


class SyncView(View):
    def __init__(self, parent, app: "FragApp"):
        super().__init__(
            parent,
            app,
            "Sync",
            "Push your local mods to the cloud, or pull them on another device",
        )
        self._build()

    def _build(self) -> None:
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", pady=(8, 0))

        # Push card
        push = card(top)
        push.pack(side="left", fill="both", expand=True, padx=(0, 8))
        ctk.CTkLabel(push, text="↑  Push", font=FONT_H2, text_color=TEXT).pack(
            anchor="w", padx=20, pady=(18, 4)
        )
        ctk.CTkLabel(
            push,
            text="Bundle your selected mods (and worlds, if enabled) and upload to your Frag bucket.",
            font=FONT_BODY,
            text_color=TEXT_DIM,
            wraplength=380,
            justify="left",
        ).pack(anchor="w", padx=20)

        self.selection_chip = ctk.CTkLabel(
            push,
            text="",
            font=FONT_TINY,
            text_color=TEXT_DIM,
            fg_color=SURFACE_ALT,
            corner_radius=8,
            padx=12,
            height=26,
        )
        self.selection_chip.pack(anchor="w", padx=20, pady=(10, 0))

        self.upload_progress = ctk.CTkProgressBar(
            push,
            progress_color=PRIMARY,
            fg_color=SURFACE_ALT,
            height=6,
        )
        self.upload_progress.set(0)
        self.upload_progress.pack(fill="x", padx=20, pady=(14, 4))
        self.upload_status = ctk.CTkLabel(
            push, text="", font=FONT_DIM, text_color=TEXT_DIM
        )
        self.upload_status.pack(anchor="w", padx=20)
        primary_button(push, text="Upload now", command=self._upload_clicked).pack(
            anchor="w", padx=20, pady=(14, 20)
        )

        # Pull card
        pull = card(top)
        pull.pack(side="left", fill="both", expand=True, padx=(8, 0))
        ctk.CTkLabel(pull, text="↓  Pull", font=FONT_H2, text_color=TEXT).pack(
            anchor="w", padx=20, pady=(18, 4)
        )
        ctk.CTkLabel(
            pull,
            text="Replace your local mods with the bundle from your Frag bucket. A backup is kept.",
            font=FONT_BODY,
            text_color=TEXT_DIM,
            wraplength=380,
            justify="left",
        ).pack(anchor="w", padx=20)
        self.download_progress = ctk.CTkProgressBar(
            pull,
            progress_color=ACCENT,
            fg_color=SURFACE_ALT,
            height=6,
        )
        self.download_progress.set(0)
        self.download_progress.pack(fill="x", padx=20, pady=(16, 4))
        self.download_status = ctk.CTkLabel(
            pull, text="", font=FONT_DIM, text_color=TEXT_DIM
        )
        self.download_status.pack(anchor="w", padx=20)
        primary_button(
            pull, text="Download mods", command=lambda: self._download_clicked("mods")
        ).pack(anchor="w", padx=20, pady=(14, 20))

        # Cloud list
        list_card = card(self)
        list_card.pack(fill="both", expand=True, pady=(16, 0))
        header = ctk.CTkFrame(list_card, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 8))
        ctk.CTkLabel(header, text="Cloud files", font=FONT_H2, text_color=TEXT).pack(
            side="left"
        )
        ghost_button(header, text="Refresh", command=self.refresh_files).pack(
            side="right"
        )

        self.files_frame = ctk.CTkScrollableFrame(
            list_card,
            fg_color=SURFACE,
            height=200,
            scrollbar_button_color=SURFACE_HI,
            scrollbar_button_hover_color=PRIMARY,
        )
        self.files_frame.pack(fill="both", expand=True, padx=12, pady=(0, 16))

    def refresh_files(self) -> None:
        for child in self.files_frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.files_frame, text="Loading…", font=FONT_BODY, text_color=TEXT_DIM
        ).pack(pady=18)

        def work():
            return self.app.client.list_files()

        def done(files):
            for child in self.files_frame.winfo_children():
                child.destroy()
            if not files:
                _empty_state(
                    self.files_frame,
                    title="Nothing in your bucket yet",
                    hint="Push your mods to start.",
                )
                return
            for name in files:
                self._render_file_row(name)

        self.app.run_in_thread(
            work, on_done=done, on_error=self._show_error, status="Listing cloud files…"
        )

    def _render_file_row(self, name: str) -> None:
        row = HoverRow(self.files_frame, base=SURFACE, hover=SURFACE_ALT)
        row.pack(fill="x", padx=2, pady=3)
        ctk.CTkLabel(
            row, text="📦  " + name, font=FONT_BODY, text_color=TEXT, anchor="w"
        ).pack(side="left", padx=14, pady=10, fill="x", expand=True)
        danger_button(
            row,
            text="Delete",
            width=80,
            command=lambda n=name: self._delete_clicked(n),
        ).pack(side="right", padx=8, pady=8)
        ghost_button(
            row,
            text="Download",
            width=110,
            command=lambda n=name: self._download_named(n),
        ).pack(side="right", padx=4, pady=8)

    def refresh_selection_chip(self) -> None:
        """Update the chip showing how many mods/worlds are selected for upload."""
        cfg = self.app.cfg
        mods = self.app.mods_view.mods
        worlds = self.app.worlds_view.worlds
        selected_mods = sum(1 for m in mods if cfg.is_mod_synced(m.sha1))
        selected_worlds = sum(1 for w in worlds if cfg.is_world_synced(w.name))
        self.selection_chip.configure(
            text=f"{selected_mods}/{len(mods)} mods  ·  {selected_worlds}/{len(worlds)} worlds",
        )

    def _upload_clicked(self) -> None:
        if not _ensure_configured(self.app):
            return

        cfg = self.app.cfg
        selected_mods = [
            m.path for m in self.app.mods_view.mods if cfg.is_mod_synced(m.sha1)
        ]
        selected_worlds = [
            w for w in self.app.worlds_view.worlds if cfg.is_world_synced(w.name)
        ]
        should_upload_worlds = bool(cfg.saves_path.is_dir() and selected_worlds)

        if not selected_mods and not should_upload_worlds:
            messagebox.showinfo(
                "Frag",
                "Nothing selected to sync. Pick mods (and worlds, if enabled) "
                "on their tabs first.",
            )
            return

        if not messagebox.askyesno(
            "Frag",
            f"Bundle and upload {len(selected_mods)} mod(s)"
            + (f" and {len(selected_worlds)} world(s)" if should_upload_worlds else "")
            + " now?",
        ):
            return

        tmp_root = Path.home() / ".frag_tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        mods_zip = tmp_root / "mods.zip"
        worlds_zip = tmp_root / "worlds.zip"

        def work():
            uploads_planned = (1 if selected_mods else 0) + (
                1 if should_upload_worlds else 0
            )
            mods_share = 1.0 / uploads_planned if uploads_planned else 1.0
            cursor = 0.0

            if selected_mods:
                self._set_upload(cursor, "Zipping mods…")
                zip_directory(
                    cfg.mods_path,
                    mods_zip,
                    items=selected_mods,
                    progress=lambda i, t: self._set_upload(
                        cursor + (i / max(t, 1)) * mods_share * 0.4,
                        f"Zipping mods… ({i}/{t})",
                    ),
                )
                self._set_upload(cursor + mods_share * 0.4, "Uploading mods…")
                self.app.client.upload_zip(
                    mods_zip,
                    progress=lambda d, t: self._set_upload(
                        cursor + mods_share * (0.4 + (d / max(t, 1)) * 0.6),
                        f"Uploading mods… {d / 1024 / 1024:.1f} MB",
                    ),
                )
                cfg.last_mod_sync = time.time()
                cursor += mods_share

            if should_upload_worlds:
                self._set_upload(cursor, "Zipping worlds…")
                zip_directory(
                    cfg.saves_path,
                    worlds_zip,
                    items=selected_worlds,
                    progress=lambda i, t: self._set_upload(
                        cursor + (i / max(t, 1)) * mods_share * 0.4,
                        f"Zipping worlds… ({i}/{t})",
                    ),
                )
                self._set_upload(cursor + mods_share * 0.4, "Uploading worlds…")
                self.app.client.upload_zip(
                    worlds_zip,
                    progress=lambda d, t: self._set_upload(
                        cursor + mods_share * (0.4 + (d / max(t, 1)) * 0.6),
                        f"Uploading worlds… {d / 1024 / 1024:.1f} MB",
                    ),
                )
                cfg.last_world_sync = time.time()

            cfg.save()
            return True

        def done(_):
            self._set_upload(1.0, "Upload complete.")
            self.refresh_files()
            messagebox.showinfo("Frag", "Upload complete.")

        def cleanup():
            for z in (mods_zip, worlds_zip):
                z.unlink(missing_ok=True)

        def fail(e):
            self._set_upload(0, f"Failed: {e}")
            cleanup()

        self.app.run_in_thread(
            work,
            on_done=lambda r: (done(r), cleanup()),
            on_error=fail,
            status="Uploading…",
        )

    def _set_progress(self, progress_bar, status_label, frac: float, text: str) -> None:
        """Update progress bar and status label safely (thread-safe)."""

        def update():
            progress_bar.set(max(0.0, min(1.0, frac)))
            status_label.configure(text=text)

        self.app.after(0, update)

    def _set_upload(self, frac: float, text: str) -> None:
        self._set_progress(self.upload_progress, self.upload_status, frac, text)

    def _set_download(self, frac: float, text: str) -> None:
        self._set_progress(self.download_progress, self.download_status, frac, text)

    def _download_clicked(self, kind: str) -> None:
        if not _ensure_configured(self.app):
            return
        filename = "mods.zip" if kind == "mods" else "worlds.zip"
        self._download_named(filename)

    def _download_named(self, filename: str) -> None:
        if not _ensure_configured(self.app):
            return
        cfg = self.app.cfg
        if filename == "mods.zip":
            target_dir = cfg.mods_path
            label = "mods"
        elif filename == "worlds.zip":
            target_dir = cfg.saves_path
            label = "worlds"
        else:
            chosen = filedialog.askdirectory(title=f"Extract {filename} into…")
            if not chosen:
                return
            target_dir = Path(chosen)
            label = filename

        if not messagebox.askyesno(
            "Frag",
            f"Download '{filename}' and extract into:\n{target_dir}\n\n"
            "A backup of any existing files will be created.",
        ):
            return

        tmp_root = Path.home() / ".frag_tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        tmp_zip = tmp_root / filename

        def work():
            self._set_download(0, f"Downloading {label}…")
            self.app.client.download_file(
                filename,
                tmp_zip,
                progress=lambda d, t: self._set_download(
                    (d / max(t, 1)) * 0.7,
                    f"Downloading {label}… {d / 1024 / 1024:.1f} MB",
                ),
            )
            if target_dir.is_dir() and any(target_dir.iterdir()):
                backup = target_dir.parent / f"{target_dir.name}.bak.{int(time.time())}"
                target_dir.rename(backup)
            target_dir.mkdir(parents=True, exist_ok=True)
            self._set_download(0.75, f"Extracting {label}…")
            unzip_into(
                tmp_zip,
                target_dir,
                progress=lambda i, t: self._set_download(
                    0.75 + (i / max(t, 1)) * 0.25,
                    f"Extracting {label}… ({i}/{t})",
                ),
            )
            return True

        def done(_):
            self._set_download(1.0, f"{label.capitalize()} restored.")
            tmp_zip.unlink(missing_ok=True)
            self.app.mods_view.refresh_async()
            self.app.worlds_view.refresh()
            messagebox.showinfo("Frag", f"{label.capitalize()} restored from cloud.")

        def fail(e):
            self._set_download(0, f"Failed: {e}")
            tmp_zip.unlink(missing_ok=True)

        self.app.run_in_thread(
            work, on_done=done, on_error=fail, status=f"Downloading {label}…"
        )

    def _delete_clicked(self, filename: str) -> None:
        if not messagebox.askyesno(
            "Frag", f"Delete '{filename}' from your Frag bucket?"
        ):
            return

        def work():
            self.app.client.delete_file(filename)

        def done(_):
            self.refresh_files()

        self.app.run_in_thread(
            work,
            on_done=done,
            on_error=self._show_error,
            status=f"Deleting {filename}…",
        )

    def _show_error(self, e: Exception) -> None:
        messagebox.showerror("Frag", str(e))


# ---- storage view -----------------------------------------------------------


PATREON_URL = "https://www.patreon.com/c/mabelthemoron/membership"


def _fmt_bytes(n: int) -> str:
    """Human-readable size, max 1 decimal."""
    if n < 1024:
        return f"{n} B"
    units = ("KB", "MB", "GB", "TB")
    size = float(n)
    for unit in units:
        size /= 1024
        if size < 1024:
            # Drop the decimal when it's not adding info.
            return f"{size:.1f} {unit}".replace(".0 ", " ")
    return f"{size:.1f} PB"


class StorageView(View):
    """Per-user storage usage + Premium token redemption."""

    def __init__(self, parent, app: "FragApp"):
        super().__init__(parent, app, "Storage", "Your cloud quota")
        self._quota_bytes = 0
        self._used_bytes = 0
        self._base_quota_bytes = 0
        self._bonus_bytes = 0
        self._tokens_held = 0
        self._build()

    def _build(self) -> None:
        # ---- usage card -----------------------------------------------------
        usage_card = card(self)
        usage_card.pack(fill="x", pady=(8, 12))

        pad = tk.Frame(usage_card, bg=SURFACE)
        pad.pack(fill="x", padx=22, pady=18)

        self._usage_title = tk.Label(
            pad, text="Loading usage…", bg=SURFACE, fg=TEXT,
            font=FONT_H1, anchor="w",
        )
        self._usage_title.pack(fill="x")

        self._usage_subtitle = tk.Label(
            pad, text="", bg=SURFACE, fg=TEXT_DIM,
            font=FONT_DIM, anchor="w",
        )
        self._usage_subtitle.pack(fill="x", pady=(2, 14))

        # Progress bar — hand-drawn on a canvas so we can hit specific
        # colors (CTk progress bars don't theme as well).
        self._bar_canvas = tk.Canvas(
            pad, height=14, bg=SURFACE_HI, highlightthickness=0, bd=0,
        )
        self._bar_canvas.pack(fill="x")
        self._bar_fill = self._bar_canvas.create_rectangle(
            0, 0, 0, 14, fill=PRIMARY, width=0,
        )
        self._bar_canvas.bind("<Configure>", lambda _e: self._redraw_bar())

        chip_row = tk.Frame(pad, bg=SURFACE)
        chip_row.pack(fill="x", pady=(10, 0))
        self._used_chip = tk.Label(
            chip_row, text="", bg=SURFACE, fg=TEXT_DIM, font=FONT_DIM, anchor="w",
        )
        self._used_chip.pack(side="left")
        ghost_button(
            chip_row, text="Refresh", width=110, command=self.refresh_async,
        ).pack(side="right")

        # ---- current-plan chip ---------------------------------------------
        self._plan_chip_row = tk.Frame(self, bg=BG)
        self._plan_chip_row.pack(fill="x", pady=(4, 0))
        self._plan_chip = tk.Label(
            self._plan_chip_row, text="", bg=SURFACE, fg=TEXT_DIM,
            font=FONT_DIM, padx=12, pady=6,
        )
        self._plan_chip.pack(side="left")

        # ---- premium token redemption -------------------------------------
        redeem_card = card(self)
        redeem_card.pack(fill="x", pady=(16, 0))
        rpad = tk.Frame(redeem_card, bg=SURFACE)
        rpad.pack(fill="x", padx=22, pady=18)

        # Label row: "Premium Token"  ........................  Get a Token
        label_row = tk.Frame(rpad, bg=SURFACE)
        label_row.pack(fill="x")
        tk.Label(
            label_row, text="Premium Token", bg=SURFACE, fg=TEXT,
            font=FONT_H2, anchor="w",
        ).pack(side="left")
        get_link = tk.Label(
            label_row, text="Get a Token  ↗",
            bg=SURFACE, fg=PRIMARY, font=FONT_DIM, cursor="hand2",
        )
        get_link.pack(side="right")
        get_link.bind("<Button-1>", lambda _e: webbrowser.open(PATREON_URL))

        # Input + Redeem button row.
        entry_row = tk.Frame(rpad, bg=SURFACE)
        entry_row.pack(fill="x", pady=(10, 0))
        self._token_var = ctk.StringVar()
        self._token_entry = ctk.CTkEntry(
            entry_row, textvariable=self._token_var,
            placeholder_text="fragsup_…",
            fg_color=SURFACE_HI, border_color=SURFACE_HI, text_color=TEXT,
            height=36, corner_radius=8,
        )
        self._token_entry.pack(side="left", fill="x", expand=True)
        primary_button(
            entry_row, text="Redeem", width=120, height=36,
            command=self._on_redeem_clicked,
        ).pack(side="left", padx=(8, 0))

        self._redeem_status = tk.Label(
            rpad, text="", bg=SURFACE, fg=TEXT_DIM,
            font=FONT_DIM, anchor="w", justify="left", wraplength=900,
        )
        self._redeem_status.pack(fill="x", pady=(8, 0))

        # Release row: only relevant when a token is held; button stays
        # disabled until quota.supporter_tokens_held > 0 so the row's
        # visible at all times for layout stability.
        release_row = tk.Frame(rpad, bg=SURFACE)
        release_row.pack(fill="x", pady=(10, 0))
        tk.Label(
            release_row,
            text="Tokens are single-use: release yours to redeem it on another account.",
            bg=SURFACE, fg=TEXT_FAINT, font=FONT_TINY, anchor="w",
            justify="left", wraplength=720,
        ).pack(side="left", fill="x", expand=True)
        self._release_btn = ghost_button(
            release_row, text="Release my token", width=160, height=30,
            command=self._on_release_clicked, state="disabled",
        )
        self._release_btn.pack(side="right")

    # ---- data -------------------------------------------------------------

    def refresh_async(self) -> None:
        self._usage_title.configure(text="Loading usage…")
        self._usage_subtitle.configure(text="")
        self._used_chip.configure(text="")

        def work():
            return self.app.client.quota()

        def done(payload: dict):
            self._apply_quota_payload(payload)

        def fail(_exc: Exception):
            self._usage_title.configure(text="Couldn't reach server")
            self._usage_subtitle.configure(
                text="Check your connection, then refresh.",
            )

        self.app.run_in_thread(
            work, on_done=done, on_error=fail, status="Loading quota…",
        )

    def _apply_quota_payload(self, payload: dict) -> None:
        self._quota_bytes = int(payload.get("quota_bytes", 0))
        self._used_bytes = int(payload.get("used_bytes", 0))
        self._base_quota_bytes = int(payload.get("base_quota_bytes", self._quota_bytes))
        self._bonus_bytes = int(payload.get("supporter_bonus_bytes", 0))
        self._tokens_held = int(payload.get("supporter_tokens_held", 0))
        self._render_usage()
        self._render_plan_chip()

    def _render_plan_chip(self) -> None:
        if self._tokens_held > 0:
            self._plan_chip.configure(
                text=f"✦ Premium — +{_fmt_bytes(self._bonus_bytes)} bonus active",
                fg=PRIMARY, bg=PRIMARY_SOFT,
            )
            self._release_btn.configure(state="normal")
        else:
            self._plan_chip.configure(
                text="Free plan — redeem a Premium token below to upgrade",
                fg=TEXT_DIM, bg=SURFACE,
            )
            self._release_btn.configure(state="disabled")

    def _render_usage(self) -> None:
        quota = max(self._quota_bytes, 1)
        used = max(self._used_bytes, 0)
        pct = min(used / quota, 1.0)

        self._usage_title.configure(
            text=f"{_fmt_bytes(used)} of {_fmt_bytes(quota)} used",
        )
        if pct >= 0.9:
            warn_color = ERROR
            msg = (
                "You're almost out of room — redeem a Premium token for +50 GiB."
                if self._tokens_held == 0
                else "You're almost out of room."
            )
        elif pct >= 0.75:
            warn_color = WARNING
            msg = "Heads up: you're past 75% of your quota."
        else:
            warn_color = TEXT_DIM
            msg = f"{_fmt_bytes(max(quota - used, 0))} free."
        self._usage_subtitle.configure(text=msg, fg=warn_color)
        self._used_chip.configure(text=f"{pct * 100:.1f}% used")

        # Color the bar based on fill level.
        self._bar_fill_color = (
            ERROR if pct >= 0.9 else WARNING if pct >= 0.75 else PRIMARY
        )
        self._redraw_bar()

    # ---- token actions ---------------------------------------------------

    def _on_redeem_clicked(self) -> None:
        token = self._token_var.get().strip()
        if not token:
            self._redeem_status.configure(
                text="Paste your Premium token first.", fg=WARNING,
            )
            return

        self._redeem_status.configure(text="Redeeming…", fg=TEXT_DIM)

        def work():
            return self.app.client.claim_supporter_token(token)

        def done(payload: dict):
            self._token_var.set("")
            self._apply_quota_payload(payload)
            self._redeem_status.configure(
                text="Token redeemed — +50 GiB unlocked.", fg=SUCCESS,
            )

        def fail(exc: Exception):
            msg = str(exc)
            if msg.startswith("409:"):
                friendly = "That token is already in use on another account."
            elif msg.startswith("404:"):
                friendly = "We don't recognise that token. Double-check it for typos."
            elif msg.startswith("400:"):
                friendly = "That doesn't look like a valid token."
            else:
                friendly = f"Couldn't redeem token: {msg}"
            self._redeem_status.configure(text=friendly, fg=ERROR)

        self.app.run_in_thread(
            work, on_done=done, on_error=fail, status="Redeeming token…",
        )

    def _on_release_clicked(self) -> None:
        if self._tokens_held == 0:
            return
        if not messagebox.askyesno(
            "Frag",
            "Release your Premium token?\n\n"
            "Your quota will drop back to the Free tier until you redeem "
            "the token again here or on another account.",
        ):
            return

        self._redeem_status.configure(text="Releasing…", fg=TEXT_DIM)

        def work():
            return self.app.client.release_supporter_token()

        def done(_payload: dict):
            self._redeem_status.configure(
                text="Token released — it's now free to redeem elsewhere.",
                fg=TEXT_DIM,
            )
            self.refresh_async()

        def fail(exc: Exception):
            self._redeem_status.configure(
                text=f"Couldn't release token: {exc}", fg=ERROR,
            )

        self.app.run_in_thread(
            work, on_done=done, on_error=fail, status="Releasing token…",
        )

    def _redraw_bar(self) -> None:
        if not hasattr(self, "_bar_canvas"):
            return
        quota = max(self._quota_bytes, 1)
        used = max(self._used_bytes, 0)
        pct = min(used / quota, 1.0)
        width = self._bar_canvas.winfo_width()
        self._bar_canvas.coords(self._bar_fill, 0, 0, int(width * pct), 14)
        self._bar_canvas.itemconfigure(
            self._bar_fill, fill=getattr(self, "_bar_fill_color", PRIMARY),
        )


# ---- settings view ----------------------------------------------------------


class SettingsView(View):
    def __init__(self, parent, app: "FragApp"):
        super().__init__(parent, app, "Settings", "Account, paths, and sync options")
        self._build()

    def _build(self) -> None:
        wrap = ctk.CTkScrollableFrame(
            self,
            fg_color=BG,
            scrollbar_button_color=SURFACE_HI,
            scrollbar_button_hover_color=PRIMARY,
        )
        wrap.pack(fill="both", expand=True, pady=(8, 0))

        # Account
        acct = card(wrap)
        acct.pack(fill="x", pady=(0, 16))
        _section_header(acct, "Account", "Sign in with your Pixelated Dream account")
        self.url_var = ctk.StringVar(value=self.app.cfg.server_url)
        _labeled(acct, "Frag Server URL", self.url_var)

        # Sign-in status
        self.auth_status = ctk.CTkLabel(
            acct, text="", font=FONT_DIM, text_color=TEXT_DIM, anchor="w"
        )
        self.auth_status.pack(fill="x", padx=22, pady=(10, 4))
        self._update_auth_status()

        btns = ctk.CTkFrame(acct, fg_color="transparent")
        btns.pack(fill="x", padx=22, pady=(10, 20))
        primary_button(btns, text="Sign in with PD", command=self._auth_login).pack(
            side="left"
        )
        ghost_button(btns, text="Sign out", command=self._auth_logout).pack(
            side="left", padx=(8, 0)
        )
        self.account_status = ctk.CTkLabel(
            btns, text="", font=FONT_DIM, text_color=TEXT_DIM
        )
        self.account_status.pack(side="left", padx=(16, 0))

        # Paths
        paths = card(wrap)
        paths.pack(fill="x", pady=(0, 16))
        _section_header(paths, "Paths", "Where Frag looks for your Minecraft files.")
        self.mc_var = ctk.StringVar(value=self.app.cfg.minecraft_dir)
        self.mods_var = ctk.StringVar(value=self.app.cfg.mods_dir)
        self.saves_var = ctk.StringVar(value=self.app.cfg.saves_dir)
        _labeled_path(paths, ".minecraft", self.mc_var)
        _labeled_path(paths, "Mods folder", self.mods_var)
        _labeled_path(paths, "Saves folder", self.saves_var)
        ctk.CTkLabel(paths, text="", height=6).pack()  # spacer

        # Updates — talks to the PD platform catalog, not the Frag server.
        updates = card(wrap)
        updates.pack(fill="x", pady=(0, 16))
        _section_header(
            updates, "Updates",
            "Check Pixelated Dream for a newer version of Frag.",
        )
        self.update_status = ctk.CTkLabel(
            updates,
            text=f"Current version: {__version__}",
            font=FONT_DIM, text_color=TEXT_DIM, anchor="w",
        )
        self.update_status.pack(fill="x", padx=22, pady=(2, 10))
        upd_btns = ctk.CTkFrame(updates, fg_color="transparent")
        upd_btns.pack(fill="x", padx=22, pady=(0, 20))
        primary_button(
            upd_btns, text="Check for updates",
            command=self._check_for_updates,
        ).pack(side="left")
        self._update_download_btn = ghost_button(
            upd_btns, text="Download update",
            command=self._open_update_download, state="disabled",
        )
        self._update_download_btn.pack(side="left", padx=(8, 0))
        self._pending_download_url: str = ""

        # Cloud Sync — replicate per-mod/world selection across devices
        # via the Frag bucket.  (Worlds are now synced by default alongside
        # mods; the old "Also sync worlds" toggle was removed in 0.3.0.)
        cloud = card(wrap)
        cloud.pack(fill="x", pady=(0, 16))
        _section_header(
            cloud,
            "Cloud sync",
            "Share your sync preferences and mod/world selection across "
            "your other devices. Server URL, auth token, and local paths "
            "are never uploaded.",
        )

        self.cloud_state_label = ctk.CTkLabel(
            cloud, text="", font=FONT_DIM, text_color=TEXT_DIM, anchor="w"
        )
        self.cloud_state_label.pack(fill="x", padx=22, pady=(2, 10))
        self._refresh_cloud_state()

        cloud_btns = ctk.CTkFrame(cloud, fg_color="transparent")
        cloud_btns.pack(fill="x", padx=22, pady=(0, 20))
        primary_button(
            cloud_btns, text="Push settings", command=self._push_settings
        ).pack(side="left")
        ghost_button(
            cloud_btns, text="Pull settings", command=self._pull_settings
        ).pack(side="left", padx=(8, 0))
        self.cloud_status = ctk.CTkLabel(
            cloud_btns, text="", font=FONT_DIM, text_color=TEXT_DIM
        )
        self.cloud_status.pack(side="left", padx=(16, 0))

        # Save
        save_row = ctk.CTkFrame(wrap, fg_color="transparent")
        save_row.pack(fill="x", pady=(4, 16))
        primary_button(save_row, text="Save settings", command=self._save_all).pack(
            side="left"
        )
        self.save_status = ctk.CTkLabel(
            save_row, text="", font=FONT_DIM, text_color=TEXT_DIM
        )
        self.save_status.pack(side="left", padx=(16, 0))

    def _collect(self) -> None:
        cfg = self.app.cfg
        cfg.server_url = self.url_var.get().strip()
        cfg.minecraft_dir = self.mc_var.get().strip()
        cfg.mods_dir = self.mods_var.get().strip()
        cfg.saves_dir = self.saves_var.get().strip()

    def _save_all(self) -> None:
        self._collect()
        self.app.cfg.save()
        self.save_status.configure(text="✓  Saved", text_color=SUCCESS)
        self.app.mods_view.refresh_async()
        self.app.worlds_view.refresh()
        self.app.refresh_connection()
        self.after(2000, lambda: self.save_status.configure(text=""))

    def _check_for_updates(self) -> None:
        """Ask PD for the latest published Frag version."""
        self.update_status.configure(
            text="Checking…", text_color=TEXT_DIM,
        )
        self._update_download_btn.configure(state="disabled")
        self._pending_download_url = ""

        def work():
            return PDCatalogClient().check_update()

        def done(result):
            if result is None:
                self.update_status.configure(
                    text=(
                        "Couldn't reach Pixelated Dream.  Check your "
                        "connection and try again later."
                    ),
                    text_color=WARNING,
                )
                return
            if result.update_available:
                self.update_status.configure(
                    text=(
                        f"Update available: {result.latest_version} "
                        f"(you're on {result.current_version or __version__})"
                    ),
                    text_color=PRIMARY,
                )
                if result.download_url:
                    self._pending_download_url = result.download_url
                    self._update_download_btn.configure(state="normal")
            else:
                self.update_status.configure(
                    text=f"You're up to date.  ({result.current_version or __version__})",
                    text_color=SUCCESS,
                )

        def fail(_exc):
            self.update_status.configure(
                text="Update check failed.", text_color=WARNING,
            )

        self.app.run_in_thread(
            work, on_done=done, on_error=fail, status="Checking PD for updates…",
        )

    def _open_update_download(self) -> None:
        if not self._pending_download_url:
            return
        webbrowser.open(self._pending_download_url)

    def _update_auth_status(self) -> None:
        """Update the displayed authentication status."""
        if self.app.cfg.session_token:
            expires_at = self.app.cfg.session_expires_at
            remaining = expires_at - time.time() if expires_at else 0
            if remaining > 86400:
                exp = f"expires in {int(remaining // 86400)}d"
                color = SUCCESS
            elif remaining > 3600:
                exp = f"expires in {int(remaining // 3600)}h"
                color = SUCCESS
            elif remaining > 0:
                exp = f"expires in {int(remaining // 60)}m — sign in again soon"
                color = WARNING
            else:
                exp = "session may be expired — please sign in again"
                color = WARNING
            user = self.app.cfg.session_user_id or "PD account"
            self.auth_status.configure(
                text=f"✓ Signed in as {user}  ({exp})",
                text_color=color,
            )
        else:
            self.auth_status.configure(
                text="Not signed in. Sign in to sync mods and worlds.",
                text_color=TEXT_DIM,
            )

    def _auth_login(self) -> None:
        """Brokered login: ask frag server → open URL → poll until approved."""
        from .api import FragAPIError
        import webbrowser

        self._collect()
        self.app.cfg.save()
        self.account_status.configure(text="Asking server…", text_color=TEXT_DIM)

        def work():
            client = self.app.client
            start = client.start_auth()
            handle = start.get("handle")
            authorize_url = start.get("authorize_url")
            if not handle or not authorize_url:
                raise FragAPIError("Server returned an invalid login response.")

            webbrowser.open(authorize_url)

            # Poll for up to ~10 min, 2s interval. The frag server stores the
            # session token on the client side via FragClient.poll_auth().
            deadline = time.time() + 600
            while time.time() < deadline:
                result = client.poll_auth(handle)
                status = result.get("status")
                if status == "approved":
                    return result
                if status in ("denied", "expired"):
                    raise FragAPIError(f"Sign-in {status}.")
                time.sleep(2)
            raise FragAPIError("Sign-in timed out. Please try again.")

        def done(_):
            self._update_auth_status()
            self.account_status.configure(text="✓  Signed in", text_color=SUCCESS)
            self.app.refresh_connection()
            self.app.mods_view.refresh_async()
            self.after(2000, lambda: self.account_status.configure(text=""))

        def fail(e):
            self.account_status.configure(text=f"✗  {e}", text_color=ERROR)
            self._update_auth_status()

        self.app.run_in_thread(
            work, on_done=done, on_error=fail, status="Waiting for sign-in…"
        )

    def _auth_logout(self) -> None:
        """Tell the server to revoke our session and clear local state."""
        if not messagebox.askyesno("Frag", "Sign out and clear local session?"):
            return

        def work():
            try:
                self.app.client.logout()
            except Exception:
                # Even if the server is unreachable, drop the local session.
                self.app.cfg.session_token = ""
                self.app.cfg.session_user_id = ""
                self.app.cfg.session_expires_at = 0.0
                self.app.cfg.save()
            return True

        def done(_):
            self._update_auth_status()
            self.account_status.configure(text="✓  Signed out", text_color=TEXT_DIM)
            self.after(2000, lambda: self.account_status.configure(text=""))

        self.app.run_in_thread(work, on_done=done, status="Signing out…")

    # ---- cloud sync ---------------------------------------------------------

    def _refresh_cloud_state(self) -> None:
        ts = self.app.cfg.last_settings_sync
        if ts:
            from datetime import datetime, timezone

            human = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            self.cloud_state_label.configure(
                text=f"This device: {self.app.cfg.device_name} · last synced {human}"
            )
        else:
            self.cloud_state_label.configure(
                text=f"This device: {self.app.cfg.device_name} · never synced"
            )

    def _push_settings(self) -> None:
        if not _ensure_configured(self.app):
            return
        if not messagebox.askyesno(
            "Frag",
            "Upload your current sync settings (world-sync toggle and the "
            "per-mod/world selection) to your Frag bucket?",
        ):
            return
        self.cloud_status.configure(text="Pushing…", text_color=TEXT_DIM)

        def work():
            return push_settings(self.app.client, self.app.cfg)

        def done(remote: RemoteSettings):
            self.cloud_status.configure(text="✓  Pushed", text_color=SUCCESS)
            self._refresh_cloud_state()
            self.app.after(2500, lambda: self.cloud_status.configure(text=""))

        def fail(e):
            self.cloud_status.configure(text=f"✗  {e}", text_color=ERROR)

        self.app.run_in_thread(
            work, on_done=done, on_error=fail, status="Pushing settings…"
        )

    def _pull_settings(self) -> None:
        if not _ensure_configured(self.app):
            return
        if not messagebox.askyesno(
            "Frag",
            "Replace your current sync settings with the version stored in "
            "your Frag bucket?",
        ):
            return
        self.cloud_status.configure(text="Pulling…", text_color=TEXT_DIM)

        def work():
            return pull_settings(self.app.client, self.app.cfg)

        def done(remote):
            if remote is None:
                self.cloud_status.configure(
                    text="No settings in bucket yet.", text_color=WARNING
                )
                return
            self.cloud_status.configure(
                text=f"✓  Pulled from {remote.synced_from or 'cloud'}",
                text_color=SUCCESS,
            )
            self._refresh_cloud_state()
            # Reflect freshly-applied state across the UI
            self.app.mods_view.refresh_async()
            self.app.worlds_view.refresh()
            self.app.after(2500, lambda: self.cloud_status.configure(text=""))

        def fail(e):
            self.cloud_status.configure(text=f"✗  {e}", text_color=ERROR)

        self.app.run_in_thread(
            work, on_done=done, on_error=fail, status="Pulling settings…"
        )


# ---- form + empty-state helpers ---------------------------------------------


def _section_header(parent, title: str, subtitle: str) -> None:
    ctk.CTkLabel(parent, text=title, font=FONT_H2, text_color=TEXT, anchor="w").pack(
        anchor="w", padx=22, pady=(18, 2)
    )
    if subtitle:
        ctk.CTkLabel(
            parent, text=subtitle, font=FONT_DIM, text_color=TEXT_DIM, anchor="w"
        ).pack(anchor="w", padx=22, pady=(0, 6))


def _labeled(parent, label: str, var: ctk.StringVar, show: str = "") -> None:
    ctk.CTkLabel(
        parent, text=label, font=FONT_DIM, text_color=TEXT_DIM, anchor="w"
    ).pack(fill="x", padx=22, pady=(10, 4))
    ctk.CTkEntry(
        parent,
        textvariable=var,
        font=FONT_MONO,
        fg_color=SURFACE_ALT,
        border_color=SURFACE_HI,
        text_color=TEXT,
        show=show,
        height=36,
        corner_radius=8,
    ).pack(fill="x", padx=22)


def _labeled_path(parent, label: str, var: ctk.StringVar) -> None:
    ctk.CTkLabel(
        parent, text=label, font=FONT_DIM, text_color=TEXT_DIM, anchor="w"
    ).pack(fill="x", padx=22, pady=(10, 4))
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=22)
    ctk.CTkEntry(
        row,
        textvariable=var,
        font=FONT_MONO,
        fg_color=SURFACE_ALT,
        border_color=SURFACE_HI,
        text_color=TEXT,
        height=36,
        corner_radius=8,
    ).pack(side="left", fill="x", expand=True)

    def browse():
        chosen = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if chosen:
            var.set(chosen)

    ghost_button(row, text="Browse…", width=96, command=browse).pack(
        side="left", padx=(8, 0)
    )


def _empty_state(parent, title: str, hint: str) -> None:
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    wrap.pack(fill="both", expand=True, pady=48)
    try:
        from PIL import Image  # type: ignore

        img = Image.open(asset(ICON_PNG))
        photo = ctk.CTkImage(light_image=img, dark_image=img, size=(72, 72))
        lbl = ctk.CTkLabel(wrap, image=photo, text="")
        lbl.image = photo  # type: ignore
        lbl.pack()
    except Exception:
        ctk.CTkLabel(
            wrap, text="—", font=("Segoe UI", 28), text_color=TEXT_FAINT
        ).pack()
    ctk.CTkLabel(wrap, text=title, font=FONT_H2, text_color=TEXT).pack(pady=(12, 4))
    ctk.CTkLabel(wrap, text=hint, font=FONT_DIM, text_color=TEXT_DIM).pack()


# ---- guards & entry ---------------------------------------------------------


def _ensure_configured(app: "FragApp") -> bool:
    if not app.cfg.server_url or not app.cfg.session_token:
        messagebox.showwarning(
            "Frag",
            "Sign in via the Settings page before uploading or downloading.",
        )
        app._show_section("settings")
        return False
    return True


def run() -> None:
    if os.environ.get("FRAG_FROM_LAUNCHER") != "1":
        _root = tk.Tk()
        _root.withdraw()
        messagebox.showerror(
            "Frag",
            "Please launch Frag using the launcher (launcher.py or Frag.exe).\n"
            "Running the app directly is not supported.",
        )
        _root.destroy()
        return
    app = FragApp()
    app.mainloop()


if __name__ == "__main__":
    run()
