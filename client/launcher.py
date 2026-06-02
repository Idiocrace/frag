"""Frag entry point.

Shows a brief branded splash on the stdlib Tk so startup feels instant,
then tears it down and runs the main CustomTkinter app in the same process.
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

SPLASH_MS = 1500

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


def build_splash() -> tk.Tk:
    root = tk.Tk()
    root.title("Frag")
    root.overrideredirect(True)
    root.configure(bg=BG)
    root.attributes("-topmost", True)

    # Window/taskbar icon — best-effort, ignore if assets missing
    try:
        ico = asset_dir() / "icon.ico"
        png = asset_dir() / "icon.png"
        if ico.is_file():
            root.iconbitmap(default=str(ico))
        if png.is_file():
            root._icon_photo = tk.PhotoImage(file=str(png))  # type: ignore[attr-defined]
            root.iconphoto(True, root._icon_photo)  # type: ignore[attr-defined]
    except tk.TclError:
        pass

    w, h = 440, 240
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    # Subtle border via an outer frame in a lighter color
    border = tk.Frame(root, bg="#2A2A3E")
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
        pad,
        text="mod manager",
        fg=TEXT_DIM,
        bg=BG,
        font=("Segoe UI", 12),
    ).pack(anchor="w", pady=(6, 0))

    # Loading indicator bar
    track = tk.Frame(pad, bg="#1F1F30", height=3)
    track.pack(fill="x", pady=(28, 0))
    fill = tk.Frame(track, bg=ACCENT, height=3, width=0)
    fill.place(x=0, y=0, height=3)

    tk.Label(
        pad,
        text="loading…",
        fg=TEXT_DIM,
        bg=BG,
        font=("Segoe UI", 10),
    ).pack(anchor="w", pady=(10, 0))

    # Animate the fill bar across the splash duration
    def animate(step: int = 0, steps: int = 30) -> None:
        track.update_idletasks()
        width = int(track.winfo_width() * (step / steps))
        fill.place_configure(width=width)
        if step < steps:
            root.after(SPLASH_MS // steps, animate, step + 1, steps)

    root.after(50, animate)
    return root


def main() -> None:
    _ensure_client_importable()
    # Signal to client.ui.run() that we came through the official entry point.
    os.environ["FRAG_FROM_LAUNCHER"] = "1"

    root = build_splash()

    def go() -> None:
        root.destroy()
        try:
            from client.ui import run
            run()
        except Exception as e:
            # Show a real dialog so failures aren't silent under --windowed
            err = tk.Tk()
            err.withdraw()
            messagebox.showerror("Frag", f"Failed to start Frag:\n{e}")
            err.destroy()
            raise

    root.after(SPLASH_MS, go)
    root.mainloop()


if __name__ == "__main__":
    main()
