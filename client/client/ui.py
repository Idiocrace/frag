"""Frag — CustomTkinter desktop UI.

Layout: sidebar nav on the left with the brand mark, section buttons, and a
connection chip + settings entry at the bottom. Main content area on the right
swaps between views (Mods, Worlds, Sync, Settings).
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .api import FragClient, unzip_into, zip_directory
from .config import Config
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
    return ctk.CTkButton(
        parent,
        fg_color=PRIMARY,
        hover_color=PRIMARY_HOV,
        text_color=TEXT,
        corner_radius=8,
        height=36,
        font=FONT_BODY,
        **kw,
    )


def ghost_button(parent, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(
        parent,
        fg_color="transparent",
        border_width=1,
        border_color=SURFACE_HI,
        hover_color=SURFACE_ALT,
        text_color=TEXT,
        corner_radius=8,
        height=36,
        font=FONT_BODY,
        **kw,
    )


def danger_button(parent, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(
        parent,
        fg_color="transparent",
        border_width=1,
        border_color=ERROR,
        hover_color=ERROR_SOFT,
        text_color=ERROR,
        corner_radius=8,
        height=32,
        font=FONT_BODY,
        **kw,
    )


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
]


class FragApp(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=BG)
        self.title("Frag — Mod Manager")
        self.geometry("1240x760")
        self.minsize(1040, 660)
        self._apply_icon()

        self.cfg = Config.load()
        self.client = FragClient(self.cfg)
        self._status_var = ctk.StringVar(value="Ready.")
        self._conn_text = ctk.StringVar(value="offline")
        self._views: dict[str, ctk.CTkFrame] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._current_section = "mods"

        self._build_sidebar()
        self._build_content()
        self._build_status_bar()

        self._show_section("mods")
        self.after(150, self.mods_view.refresh_async)
        self.after(150, self.worlds_view.refresh)
        self.after(200, self.refresh_connection)

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
            self._nav_buttons[key] = btn

        # Bottom: connection chip + settings entry
        chip_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        chip_row.pack(side="bottom", fill="x", padx=20, pady=(8, 16))
        self._conn_chip_dot = ctk.CTkLabel(
            chip_row, text="●", text_color=TEXT_FAINT, font=("Segoe UI", 12)
        )
        self._conn_chip_dot.pack(side="left")
        ctk.CTkLabel(
            chip_row, textvariable=self._conn_text, font=FONT_DIM, text_color=TEXT_DIM
        ).pack(side="left", padx=(6, 0))

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
        content = ctk.CTkFrame(self, fg_color=BG)
        content.pack(side="left", fill="both", expand=True)

        # Build each view as a hidden frame
        self.mods_view = ModsView(content, self)
        self.worlds_view = WorldsView(content, self)
        self.sync_view = SyncView(content, self)
        self.settings_view = SettingsView(content, self)

        self._views = {
            "mods": self.mods_view,
            "worlds": self.worlds_view,
            "sync": self.sync_view,
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

    # ---- helpers ------------------------------------------------------------

    def set_status(self, msg: str) -> None:
        self._status_var.set(msg)

    def run_in_thread(
        self,
        fn,
        *args,
        on_done=None,
        on_error=None,
        on_progress=None,
        status: str = "",
    ) -> None:
        if status:
            self.set_status(status)

        def worker():
            try:
                if on_progress:
                    result = fn(*args, progress=lambda d, t: self.after(0, on_progress, d, t))
                else:
                    result = fn(*args)
            except Exception as e:  # noqa: BLE001
                tb = traceback.format_exc()
                self.after(0, self._handle_error, e, tb, on_error)
                return
            self.after(0, lambda: (on_done(result) if on_done else None, self.set_status("Ready.")))

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
        self._build()

    def _build(self) -> None:
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", pady=(8, 12))

        self.search_var = ctk.StringVar()
        search = ctk.CTkEntry(
            toolbar,
            textvariable=self.search_var,
            placeholder_text="Search mods…",
            fg_color=SURFACE,
            border_color=SURFACE_HI,
            text_color=TEXT,
            height=36,
            width=260,
            corner_radius=8,
        )
        search.pack(side="left")
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
        ghost_button(
            toolbar, text="Open folder", command=self._open_folder
        ).pack(side="right", padx=(0, 8))
        ghost_button(
            toolbar, text="Select none", width=110, command=self._select_none
        ).pack(side="right", padx=(0, 8))
        ghost_button(
            toolbar, text="Select all", width=100, command=self._select_all
        ).pack(side="right", padx=(0, 8))

        # Path + warnings
        self.path_label = ctk.CTkLabel(
            self, text="", font=FONT_DIM, text_color=TEXT_FAINT, anchor="w"
        )
        self.path_label.pack(fill="x")
        self.warning_label = ctk.CTkLabel(
            self, text="", font=FONT_DIM, text_color=WARNING, anchor="w"
        )
        self.warning_label.pack(fill="x", pady=(4, 0))

        # Scrollable list inside a card
        list_card = card(self)
        list_card.pack(fill="both", expand=True, pady=(14, 0))
        self.list_frame = ctk.CTkScrollableFrame(
            list_card,
            fg_color=SURFACE,
            scrollbar_button_color=SURFACE_HI,
            scrollbar_button_hover_color=PRIMARY,
        )
        self.list_frame.pack(fill="both", expand=True, padx=10, pady=10)

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
            return scan_mods(self.app.cfg.mods_path)

        def done(result):
            mods, warnings = result
            self.mods = mods
            self.warning_label.configure(
                text=("  " + "  ".join(warnings)) if warnings else ""
            )
            self.count_chip.configure(text=f"{len(mods)} found")
            self._apply_filter()
            self.app.sync_view.refresh_selection_chip()

        self.app.run_in_thread(work, on_done=done, status="Scanning mods…")

    def _set_loading(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.list_frame,
            text="Scanning…",
            font=FONT_BODY,
            text_color=TEXT_DIM,
        ).pack(pady=32)

    def _apply_filter(self) -> None:
        q = self.search_var.get().strip().lower()
        for child in self.list_frame.winfo_children():
            child.destroy()
        filtered = [
            m
            for m in self.mods
            if not q
            or q in m.best_name.lower()
            or q in m.filename.lower()
            or q in (m.mod_id or "").lower()
        ]
        if not filtered:
            _empty_state(
                self.list_frame,
                title="No mods" if not q else "No matches",
                hint=("Drop .jar files into your mods folder, then Refresh." if not q
                      else f"Nothing matches “{q}”."),
            )
            return
        for mod in filtered:
            self._render_row(mod)

    def _render_row(self, mod: ModInfo) -> None:
        row = HoverRow(self.list_frame, base=SURFACE, hover=SURFACE_ALT)
        row.pack(fill="x", padx=2, pady=4)

        # Sync checkbox
        var = ctk.BooleanVar(value=self.app.cfg.is_mod_synced(mod.sha1))
        cb = ctk.CTkCheckBox(
            row,
            text="",
            variable=var,
            width=20,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOV,
            border_color=SURFACE_HI,
            command=lambda sha1=mod.sha1, v=var: self._on_toggle(sha1, v.get()),
        )
        cb.pack(side="left", padx=(14, 6), pady=10)

        # Avatar — colored initial
        avatar = ctk.CTkLabel(
            row,
            text=(mod.best_name[:1] or "?").upper(),
            font=("Segoe UI", 16, "bold"),
            text_color=TEXT,
            fg_color=PRIMARY_SOFT,
            corner_radius=22,
            width=44,
            height=44,
        )
        avatar.pack(side="left", padx=(8, 14), pady=10)

        # Name + meta
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, pady=10)
        ctk.CTkLabel(
            left, text=mod.best_name, font=FONT_H2, text_color=TEXT, anchor="w"
        ).pack(fill="x")
        meta = []
        if mod.best_version != "?":
            meta.append(f"v{mod.best_version}")
        if mod.loader:
            meta.append(mod.loader)
        meta.append(f"{mod.size / 1024 / 1024:.1f} MB")
        ctk.CTkLabel(
            left,
            text="  ·  ".join(meta),
            font=FONT_DIM,
            text_color=TEXT_DIM,
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

        # Right: status pill
        right = ctk.CTkFrame(row, fg_color="transparent")
        right.pack(side="right", padx=14, pady=10)
        color, soft, text = _mod_status(mod)
        _pill(right, text, color, soft).pack(side="right")

    def _on_toggle(self, sha1: str, synced: bool) -> None:
        self.app.cfg.set_mod_synced(sha1, synced)
        self.app.cfg.save()
        self.app.sync_view.refresh_selection_chip()

    def _select_all(self) -> None:
        for mod in self.mods:
            self.app.cfg.set_mod_synced(mod.sha1, True)
        self.app.cfg.save()
        self.app.sync_view.refresh_selection_chip()
        self._apply_filter()

    def _select_none(self) -> None:
        for mod in self.mods:
            self.app.cfg.set_mod_synced(mod.sha1, False)
        self.app.cfg.save()
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

        self.sync_chip = ctk.CTkLabel(
            toolbar, text="", font=FONT_TINY, text_color=TEXT_DIM,
            fg_color=SURFACE, corner_radius=8, padx=12, height=36,
        )
        self.sync_chip.pack(side="left", padx=(10, 0))

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

        list_card = card(self)
        list_card.pack(fill="both", expand=True, pady=(14, 0))
        self.list_frame = ctk.CTkScrollableFrame(
            list_card,
            fg_color=SURFACE,
            scrollbar_button_color=SURFACE_HI,
            scrollbar_button_hover_color=PRIMARY,
        )
        self.list_frame.pack(fill="both", expand=True, padx=10, pady=10)

    def refresh(self) -> None:
        self.path_label.configure(text=str(self.app.cfg.saves_path))
        if self.app.cfg.sync_worlds:
            self.sync_chip.configure(text="world sync on", text_color=SUCCESS)
        else:
            self.sync_chip.configure(text="world sync off", text_color=TEXT_DIM)

        for child in self.list_frame.winfo_children():
            child.destroy()
        self._rows.clear()

        saves = self.app.cfg.saves_path
        worlds: list[Path] = []
        if saves.is_dir():
            worlds = sorted(p for p in saves.iterdir() if p.is_dir())
        self.worlds = worlds
        self.count_chip.configure(text=f"{len(worlds)} found")

        if not worlds:
            _empty_state(
                self.list_frame,
                title="No worlds",
                hint="Worlds appear here once you've played at least once.",
            )
            return

        for world in worlds:
            self._render_row(world)

        self.app.sync_view.refresh_selection_chip()
        self.app.run_in_thread(
            self._inspect_all, worlds, on_done=lambda _r: None,
            status="Inspecting worlds…",
        )

    def _render_row(self, world: Path) -> None:
        row = HoverRow(self.list_frame, base=SURFACE, hover=SURFACE_ALT)
        row.pack(fill="x", padx=2, pady=4)

        # Sync checkbox
        var = ctk.BooleanVar(value=self.app.cfg.is_world_synced(world.name))
        cb = ctk.CTkCheckBox(
            row,
            text="",
            variable=var,
            width=20,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOV,
            border_color=SURFACE_HI,
            command=lambda n=world.name, v=var: self._on_toggle(n, v.get()),
        )
        cb.pack(side="left", padx=(14, 6), pady=10)

        # Globe avatar
        avatar = ctk.CTkLabel(
            row,
            text="🌍",
            font=("Segoe UI", 18),
            fg_color=ACCENT_SOFT,
            corner_radius=22,
            width=44,
            height=44,
        )
        avatar.pack(side="left", padx=(8, 14), pady=10)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, pady=10)
        ctk.CTkLabel(
            left, text=world.name, font=FONT_H2, text_color=TEXT, anchor="w"
        ).pack(fill="x")
        meta = ctk.CTkLabel(
            left, text="inspecting…", font=FONT_DIM, text_color=TEXT_DIM, anchor="w"
        )
        meta.pack(fill="x", pady=(2, 0))

        right = ctk.CTkFrame(row, fg_color="transparent")
        right.pack(side="right", padx=14, pady=10)
        btn = ghost_button(right, text="Show mods", width=110, state="disabled")
        btn.pack(side="right")

        self._rows[world.name] = {
            "meta": meta, "btn": btn, "right": right,
            "info": None, "cb": cb, "var": var,
        }

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
        for world in worlds:
            info = read_world(world)
            try:
                size_mb = sum(
                    p.stat().st_size for p in world.rglob("*") if p.is_file()
                ) / 1024 / 1024
            except OSError:
                size_mb = 0.0
            self.app.after(0, self._apply_info, world.name, info, size_mb)

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
                (SUCCESS, SUCCESS_SOFT) if info.source == "frag-mod"
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
        ctk.CTkLabel(header, text=info.name, font=FONT_H1, text_color=TEXT, anchor="w").pack(
            anchor="w"
        )

        chips = ctk.CTkFrame(header, fg_color="transparent")
        chips.pack(anchor="w", pady=(8, 0))
        if info.mc_version:
            _pill(chips, f"MC {info.mc_version}", TEXT, SURFACE_ALT).pack(side="left", padx=(0, 6))
        if info.loader:
            loader_text = info.loader
            if info.loader_version:
                loader_text += f" {info.loader_version}"
            _pill(chips, loader_text, PRIMARY, PRIMARY_SOFT).pack(side="left", padx=(0, 6))
        _pill(chips, f"{info.mod_count} mods", ACCENT, ACCENT_SOFT).pack(side="left", padx=(0, 6))
        if info.source != "none":
            color, soft = (
                (SUCCESS, SUCCESS_SOFT) if info.source == "frag-mod"
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
                header, text="  ·  ".join(meta_parts),
                font=FONT_DIM, text_color=TEXT_FAINT, anchor="w",
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
                left, text=mod.best_name, font=FONT_BODY,
                text_color=TEXT, anchor="w",
            ).pack(anchor="w")
            if mod.display_name and mod.mod_id and mod.display_name != mod.mod_id:
                ctk.CTkLabel(
                    left, text=mod.mod_id, font=FONT_TINY,
                    text_color=TEXT_FAINT, anchor="w",
                ).pack(anchor="w")
            if mod.description:
                desc = mod.description
                if len(desc) > 140:
                    desc = desc[:137].rstrip() + "…"
                ctk.CTkLabel(
                    left, text=desc, font=FONT_TINY,
                    text_color=TEXT_DIM, anchor="w", wraplength=420, justify="left",
                ).pack(anchor="w", pady=(2, 0))

            ctk.CTkLabel(
                row_frame, text=mod.version or "?", font=FONT_MONO,
                text_color=TEXT_DIM, anchor="e",
            ).pack(side="right", padx=14, pady=8)


# ---- sync view --------------------------------------------------------------


class SyncView(View):
    def __init__(self, parent, app: "FragApp"):
        super().__init__(parent, app, "Sync", "Push your local mods to the cloud, or pull them on another device")
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
            font=FONT_BODY, text_color=TEXT_DIM, wraplength=380, justify="left",
        ).pack(anchor="w", padx=20)

        self.selection_chip = ctk.CTkLabel(
            push, text="", font=FONT_TINY, text_color=TEXT_DIM,
            fg_color=SURFACE_ALT, corner_radius=8, padx=12, height=26,
        )
        self.selection_chip.pack(anchor="w", padx=20, pady=(10, 0))

        self.upload_progress = ctk.CTkProgressBar(
            push, progress_color=PRIMARY, fg_color=SURFACE_ALT, height=6,
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
            font=FONT_BODY, text_color=TEXT_DIM, wraplength=380, justify="left",
        ).pack(anchor="w", padx=20)
        self.download_progress = ctk.CTkProgressBar(
            pull, progress_color=ACCENT, fg_color=SURFACE_ALT, height=6,
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
        ctk.CTkLabel(header, text="Cloud files", font=FONT_H2, text_color=TEXT).pack(side="left")
        ghost_button(header, text="Refresh", command=self.refresh_files).pack(side="right")

        self.files_frame = ctk.CTkScrollableFrame(
            list_card,
            fg_color=SURFACE, height=200,
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
            row, text="Delete", width=80,
            command=lambda n=name: self._delete_clicked(n),
        ).pack(side="right", padx=8, pady=8)
        ghost_button(
            row, text="Download", width=110,
            command=lambda n=name: self._download_named(n),
        ).pack(side="right", padx=4, pady=8)

    def refresh_selection_chip(self) -> None:
        """Update the chip showing how many mods/worlds are selected for upload."""
        cfg = self.app.cfg
        mods = self.app.mods_view.mods
        worlds = self.app.worlds_view.worlds
        selected_mods = sum(1 for m in mods if cfg.is_mod_synced(m.sha1))
        selected_worlds = sum(1 for w in worlds if cfg.is_world_synced(w.name))
        parts = [f"{selected_mods}/{len(mods)} mods"]
        if cfg.sync_worlds:
            parts.append(f"{selected_worlds}/{len(worlds)} worlds")
        self.selection_chip.configure(text="  ·  ".join(parts))

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
        should_upload_worlds = cfg.sync_worlds and cfg.saves_path.is_dir() and selected_worlds

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
            uploads_planned = (1 if selected_mods else 0) + (1 if should_upload_worlds else 0)
            mods_share = 1.0 / uploads_planned if uploads_planned else 1.0
            cursor = 0.0

            if selected_mods:
                self._set_upload(cursor, "Zipping mods…")
                zip_directory(
                    cfg.mods_path, mods_zip, items=selected_mods,
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
                    cfg.saves_path, worlds_zip, items=selected_worlds,
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

    def _set_upload(self, frac: float, text: str) -> None:
        self.app.after(0, lambda: (
            self.upload_progress.set(max(0.0, min(1.0, frac))),
            self.upload_status.configure(text=text),
        ))

    def _set_download(self, frac: float, text: str) -> None:
        self.app.after(0, lambda: (
            self.download_progress.set(max(0.0, min(1.0, frac))),
            self.download_status.configure(text=text),
        ))

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
                filename, tmp_zip,
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
                tmp_zip, target_dir,
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

        self.app.run_in_thread(work, on_done=done, on_error=fail, status=f"Downloading {label}…")

    def _delete_clicked(self, filename: str) -> None:
        if not messagebox.askyesno("Frag", f"Delete '{filename}' from your Frag bucket?"):
            return

        def work():
            self.app.client.delete_file(filename)

        def done(_):
            self.refresh_files()

        self.app.run_in_thread(work, on_done=done, on_error=self._show_error, status=f"Deleting {filename}…")

    def _show_error(self, e: Exception) -> None:
        messagebox.showerror("Frag", str(e))


# ---- settings view ----------------------------------------------------------


class SettingsView(View):
    def __init__(self, parent, app: "FragApp"):
        super().__init__(parent, app, "Settings", "Account, paths, and sync options")
        self._build()

    def _build(self) -> None:
        wrap = ctk.CTkScrollableFrame(
            self, fg_color=BG,
            scrollbar_button_color=SURFACE_HI,
            scrollbar_button_hover_color=PRIMARY,
        )
        wrap.pack(fill="both", expand=True, pady=(8, 0))

        # Account
        acct = card(wrap)
        acct.pack(fill="x", pady=(0, 16))
        _section_header(acct, "Account", "Connect to your Frag server.")
        self.url_var = ctk.StringVar(value=self.app.cfg.server_url)
        self.token_var = ctk.StringVar(value=self.app.cfg.auth_token)
        _labeled(acct, "Server URL", self.url_var)
        _labeled(acct, "Auth token", self.token_var, show="●")
        btns = ctk.CTkFrame(acct, fg_color="transparent")
        btns.pack(fill="x", padx=22, pady=(10, 20))
        primary_button(btns, text="Save & test", command=self._save_and_test).pack(side="left")
        ghost_button(btns, text="Re-authenticate", command=self._reauth).pack(
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

        # Sync
        sync = card(wrap)
        sync.pack(fill="x", pady=(0, 16))
        _section_header(sync, "Sync options", "")
        self.worlds_var = ctk.BooleanVar(value=self.app.cfg.sync_worlds)
        row = ctk.CTkFrame(sync, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(4, 20))
        ctk.CTkSwitch(
            row, text="Also sync worlds (saves folder)",
            variable=self.worlds_var,
            font=FONT_BODY, text_color=TEXT,
            progress_color=PRIMARY,
            button_color=TEXT, button_hover_color=ACCENT,
        ).pack(side="left")

        # Cloud Sync — replicate settings (sync toggle + per-mod/world selection)
        # across devices via the Frag bucket.
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
        primary_button(save_row, text="Save settings", command=self._save_all).pack(side="left")
        self.save_status = ctk.CTkLabel(
            save_row, text="", font=FONT_DIM, text_color=TEXT_DIM
        )
        self.save_status.pack(side="left", padx=(16, 0))

    def _collect(self) -> None:
        cfg = self.app.cfg
        cfg.server_url = self.url_var.get().strip()
        cfg.auth_token = self.token_var.get().strip()
        cfg.minecraft_dir = self.mc_var.get().strip()
        cfg.mods_dir = self.mods_var.get().strip()
        cfg.saves_dir = self.saves_var.get().strip()
        cfg.sync_worlds = bool(self.worlds_var.get())

    def _save_all(self) -> None:
        self._collect()
        self.app.cfg.save()
        self.save_status.configure(text="✓  Saved", text_color=SUCCESS)
        self.app.mods_view.refresh_async()
        self.app.worlds_view.refresh()
        self.app.refresh_connection()
        self.after(2000, lambda: self.save_status.configure(text=""))

    def _save_and_test(self) -> None:
        self._collect()
        self.app.cfg.jwt = ""
        self.app.cfg.jwt_expires_at = 0.0
        self.app.cfg.save()
        self.account_status.configure(text="Testing…", text_color=TEXT_DIM)

        def work():
            self.app.client.authenticate(force=True)
            return True

        def done(_):
            self.account_status.configure(text="✓  Connected", text_color=SUCCESS)
            self.app.refresh_connection()

        def fail(e):
            self.account_status.configure(text=f"✗  {e}", text_color=ERROR)

        self.app.run_in_thread(work, on_done=done, on_error=fail, status="Authenticating…")

    def _reauth(self) -> None:
        self.app.cfg.jwt = ""
        self.app.cfg.jwt_expires_at = 0.0
        self.app.cfg.save()
        self._save_and_test()

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

        self.app.run_in_thread(work, on_done=done, on_error=fail, status="Pushing settings…")

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
            self.worlds_var.set(self.app.cfg.sync_worlds)
            self.app.mods_view.refresh_async()
            self.app.worlds_view.refresh()
            self.app.after(2500, lambda: self.cloud_status.configure(text=""))

        def fail(e):
            self.cloud_status.configure(text=f"✗  {e}", text_color=ERROR)

        self.app.run_in_thread(work, on_done=done, on_error=fail, status="Pulling settings…")


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
        parent, textvariable=var, font=FONT_MONO,
        fg_color=SURFACE_ALT, border_color=SURFACE_HI, text_color=TEXT,
        show=show, height=36, corner_radius=8,
    ).pack(fill="x", padx=22)


def _labeled_path(parent, label: str, var: ctk.StringVar) -> None:
    ctk.CTkLabel(
        parent, text=label, font=FONT_DIM, text_color=TEXT_DIM, anchor="w"
    ).pack(fill="x", padx=22, pady=(10, 4))
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=22)
    ctk.CTkEntry(
        row, textvariable=var, font=FONT_MONO,
        fg_color=SURFACE_ALT, border_color=SURFACE_HI, text_color=TEXT,
        height=36, corner_radius=8,
    ).pack(side="left", fill="x", expand=True)

    def browse():
        chosen = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if chosen:
            var.set(chosen)

    ghost_button(row, text="Browse…", width=96, command=browse).pack(side="left", padx=(8, 0))


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
        ctk.CTkLabel(wrap, text="—", font=("Segoe UI", 28), text_color=TEXT_FAINT).pack()
    ctk.CTkLabel(wrap, text=title, font=FONT_H2, text_color=TEXT).pack(pady=(12, 4))
    ctk.CTkLabel(wrap, text=hint, font=FONT_DIM, text_color=TEXT_DIM).pack()


# ---- guards & entry ---------------------------------------------------------


def _ensure_configured(app: "FragApp") -> bool:
    if not app.cfg.server_url or not app.cfg.auth_token:
        messagebox.showwarning(
            "Frag",
            "Set your server URL and auth token in the Settings section first.",
        )
        app._show_section("settings")
        return False
    return True


def run() -> None:
    app = FragApp()
    app.mainloop()


if __name__ == "__main__":
    run()
