"""Frag entry point.

Shows a branded splash on the stdlib Tk that stays visible for the entire
startup sequence (heavy imports + FragApp construction), then hands off
to the main CustomTkinter app in the same process.

Startup gap problem
-------------------
The old launcher destroyed the splash after a fixed 1500ms and *then*
ran ``from client.ui import run`` + ``FragApp()`` synchronously.  Those
two steps together take 1-2s on first launch (customtkinter, Pillow,
nbtlib, and the full UI tree).  During that window the splash was gone
but the main window hadn't appeared yet — looked like a hang or crash.

This launcher fixes that by:
  - keeping the splash alive until the app is ready,
  - doing the heavy import on a worker thread so the splash keeps
    animating,
  - building FragApp on the main thread (Tk requires this), then
    forcing its first paint via update_idletasks() before tearing
    the splash down,
  - showing live status text so users see real progress.
"""

from __future__ import annotations

import importlib
import os
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

# Minimum time the splash stays visible even on fast machines.  Prevents
# a single-frame flash that looks worse than a brief proper splash.
MIN_SPLASH_MS = 600

# Heavy imports done up-front, one per main-loop tick, so the splash
# animation keeps running between them.  Threading doesn't help here
# because CPython holds the import lock + GIL through each top-level
# import statement, starving the Tk event loop for tens to hundreds of
# ms at a time.  Splitting the imports into discrete steps and letting
# the event loop run between each one is the only thing that keeps the
# spinner moving on a cold cache.
IMPORT_STEPS: tuple[tuple[str, str], ...] = (
    ("loading…  (1/6) tk image bridge", "PIL.ImageTk"),
    ("loading…  (2/6) image library",   "PIL.Image"),
    ("loading…  (3/6) image drawing",   "PIL.ImageDraw"),
    ("loading…  (4/6) custom widgets",  "customtkinter"),
    ("loading…  (5/6) world data",      "nbtlib"),
    ("loading…  (6/6) Frag UI",         "client.ui"),
)

BG = "#0E0E14"
PRIMARY = "#8B5CF6"
ACCENT = "#EC4899"
TEXT = "#F4F4F8"
TEXT_DIM = "#9CA3B0"


def app_dir() -> Path:
    """Directory containing the launcher (compiled or script)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def asset_dir() -> Path:
    """Where bundled assets (icon.png, icon.ico) live at runtime."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir())) / "assets"
    return app_dir() / "assets"


def _ensure_client_importable() -> None:
    """Make sure the `client` package next to this file is importable."""
    base = str(app_dir())
    if base not in sys.path:
        sys.path.insert(0, base)


class Splash:
    """Branded splash window with a live status line and animated bar."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Frag")
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self._apply_icon()

        w, h = 440, 240
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        # Subtle border via an outer frame in a lighter color
        border = tk.Frame(self.root, bg="#2A2A3E")
        border.pack(fill="both", expand=True)
        body = tk.Frame(border, bg=BG)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        pad = tk.Frame(body, bg=BG)
        pad.pack(expand=True, fill="both", padx=32, pady=28)

        # Wordmark: violet bar + FRAG
        brand = tk.Frame(pad, bg=BG)
        brand.pack(anchor="w")
        tk.Frame(brand, bg=PRIMARY, width=5, height=48).pack(side="left", padx=(0, 14))
        tk.Label(brand, text="FRAG", fg=TEXT, bg=BG, font=("Segoe UI", 38, "bold")).pack(
            side="left"
        )

        tk.Label(
            pad, text="mod manager", fg=TEXT_DIM, bg=BG, font=("Segoe UI", 12),
        ).pack(anchor="w", pady=(6, 0))

        # Indeterminate loading bar: a short pill that slides back and
        # forth across the track. Conveys "working" without lying about
        # progress we can't actually measure.
        self._track = tk.Frame(pad, bg="#1F1F30", height=3)
        self._track.pack(fill="x", pady=(28, 0))
        self._bar = tk.Frame(self._track, bg=ACCENT, height=3, width=120)
        self._bar.place(x=0, y=0, height=3)

        self._status_var = tk.StringVar(value="loading…")
        tk.Label(
            pad, textvariable=self._status_var, fg=TEXT_DIM, bg=BG,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(10, 0))

        self._anim_pos = 0
        self._anim_dir = 1
        self._cancelled = False
        self.root.after(50, self._tick)

    # ---- icon ---------------------------------------------------------

    def _apply_icon(self) -> None:
        try:
            ico = asset_dir() / "icon.ico"
            png = asset_dir() / "icon.png"
            if ico.is_file():
                self.root.iconbitmap(default=str(ico))
            if png.is_file():
                self.root._icon_photo = tk.PhotoImage(file=str(png))  # type: ignore[attr-defined]
                self.root.iconphoto(True, self.root._icon_photo)  # type: ignore[attr-defined]
        except tk.TclError:
            pass

    # ---- animation ----------------------------------------------------

    def _tick(self) -> None:
        if self._cancelled:
            return
        self._track.update_idletasks()
        track_w = max(self._track.winfo_width(), 1)
        bar_w = 120
        # Sine-ease so the slider feels alive rather than constant-velocity.
        # Step is small so animation stays smooth (~30 fps).
        self._anim_pos += self._anim_dir * 6
        max_x = max(track_w - bar_w, 1)
        if self._anim_pos >= max_x:
            self._anim_pos = max_x
            self._anim_dir = -1
        elif self._anim_pos <= 0:
            self._anim_pos = 0
            self._anim_dir = 1
        self._bar.place_configure(x=self._anim_pos, width=bar_w)
        self.root.after(33, self._tick)

    # ---- API ----------------------------------------------------------

    def set_status(self, text: str) -> None:
        self._status_var.set(text)

    def close(self) -> None:
        """Stop the animation loop and tear down the window."""
        self._cancelled = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main() -> None:
    _ensure_client_importable()
    # Signal to client.ui.run() that we came through the official entry point.
    os.environ["FRAG_FROM_LAUNCHER"] = "1"

    splash = Splash()
    splash_started_at = time.monotonic()

    state: dict = {
        "step": 0,
        "ui_mod": None,
        "done": False,
        "error": None,
    }

    def run_next_step() -> None:
        """Import one heavy module, then yield back to the Tk loop.

        Imports run on the main thread but in tiny increments — one
        package per ``after()`` callback.  Between each step the event
        loop processes the spinner animation and any window events, so
        the splash stays alive even on a cold-cache 10-second import.
        """
        if state["done"]:
            return

        i = state["step"]
        if i >= len(IMPORT_STEPS):
            # All imports done — load FragApp from the cached module ref.
            import client.ui as ui_mod  # cheap: already imported
            state["ui_mod"] = ui_mod
            elapsed_ms = (time.monotonic() - splash_started_at) * 1000
            wait_ms = max(0, int(MIN_SPLASH_MS - elapsed_ms))
            splash.root.after(wait_ms, begin_handoff)
            return

        label, module_name = IMPORT_STEPS[i]
        splash.set_status(label)
        # Force the status update + spinner tick to actually paint before
        # we lock the GIL on the next import.
        splash.root.update_idletasks()

        try:
            importlib.import_module(module_name)
        except BaseException as e:  # noqa: BLE001
            state["error"] = e
            state["done"] = True
            splash.close()
            err = tk.Tk()
            err.withdraw()
            messagebox.showerror(
                "Frag",
                f"Failed to start Frag (importing {module_name}):\n{e}",
            )
            err.destroy()
            raise

        state["step"] = i + 1
        # after(1) instead of after(0) so the Tk event loop is guaranteed
        # to drain its idle queue (animation, paint) before the next
        # blocking import.  after(0) on Windows can re-enter immediately.
        splash.root.after(1, run_next_step)

    def begin_handoff() -> None:
        if state["done"]:
            return
        state["done"] = True
        splash.set_status("starting app…")
        splash.root.update_idletasks()
        # Brief readable beat then tear the splash down and build the app.
        splash.root.after(120, build_and_run)

    def build_and_run() -> None:
        # FragApp is a ctk.CTk which insists on being the sole Tk root —
        # CTkImage references end up bound to whichever root was first,
        # and the wrong-root case raises 'image "pyimageN" doesn't exist'
        # at first paint.  So the splash root has to be fully destroyed
        # before we construct the app.
        splash.close()
        try:
            app = state["ui_mod"].FragApp()
        except BaseException as e:  # noqa: BLE001
            err = tk.Tk()
            err.withdraw()
            messagebox.showerror("Frag", f"Failed to start Frag:\n{e}")
            err.destroy()
            raise
        # Bypass ui.run() to avoid double mainloop.
        app.mainloop()

    splash.root.after(50, run_next_step)
    splash.root.mainloop()


if __name__ == "__main__":
    main()
