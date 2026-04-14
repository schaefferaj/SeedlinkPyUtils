"""GUI helpers: horizontal radio buttons, theme application, fullscreen."""

import matplotlib
import numpy as np
from matplotlib.widgets import RadioButtons


class HRadioButtons(RadioButtons):
    """RadioButtons laid out horizontally instead of vertically.

    Compatible with matplotlib < 3.7 (per-button Circle patches) and
    >= 3.7 (single PathCollection accessed via ``_buttons``).
    """

    def __init__(self, ax, labels, active=0, activecolor="C0"):
        super().__init__(ax, labels, active=active, activecolor=activecolor)
        self._relayout_horizontal()

    def _relayout_horizontal(self):
        n = len(self.labels)
        positions = [(i + 0.5) / n for i in range(n)]

        for i, label in enumerate(self.labels):
            label.set_position((positions[i] + 0.005, 0.5))
            label.set_horizontalalignment("left")
            label.set_verticalalignment("center")

        if hasattr(self, "_buttons"):
            offsets = np.array([[p - 0.03, 0.5] for p in positions])
            self._buttons.set_offsets(offsets)
            self._buttons.set_sizes([120] * n)
        elif hasattr(self, "circles"):
            for i, circle in enumerate(self.circles):
                circle.set_center((positions[i] - 0.03, 0.5))
                circle.set_radius(0.025)
        else:
            print("Warning: unknown RadioButtons internals; layout may be off.")


def apply_theme_to_axes(ax, theme):
    """Apply text / axis / spine colours from `theme` to `ax`."""
    ax.set_facecolor(theme["bg"])
    for spine in ax.spines.values():
        spine.set_color(theme["fg"])
    ax.tick_params(colors=theme["fg"], which="both")
    ax.xaxis.label.set_color(theme["fg"])
    ax.yaxis.label.set_color(theme["fg"])
    if ax.get_title():
        ax.title.set_color(theme["fg"])


def set_tk_window_bg(fig, color):
    """Tint the Tk window chrome to match the figure background."""
    try:
        fig.canvas.manager.window.configure(bg=color)
    except Exception:
        pass


def go_fullscreen(fig):
    """Make the figure window fullscreen on Linux/macOS/Windows.

    TkAgg-targeted fix with retries and an ``overrideredirect`` fallback
    for stubborn window managers.
    """
    backend = matplotlib.get_backend().lower()
    mgr = fig.canvas.manager

    for hide_attempt in (
        lambda: fig.canvas.toolbar.pack_forget(),
        lambda: fig.canvas.toolbar.setVisible(False),
        lambda: mgr.toolbar.Hide(),
    ):
        try:
            hide_attempt()
        except Exception:
            pass

    if "tk" in backend:
        w = mgr.window
        w.update_idletasks()
        w.deiconify()
        w.update()

        def _make_fullscreen():
            try:
                w.attributes("-fullscreen", True)
                w.update()
                if not bool(w.attributes("-fullscreen")):
                    raise RuntimeError("WM ignored -fullscreen")
            except Exception as e:
                print(f"Tk -fullscreen failed ({e}); falling back to overrideredirect")
                try:
                    w.overrideredirect(True)
                    sw = w.winfo_screenwidth()
                    sh = w.winfo_screenheight()
                    w.geometry(f"{sw}x{sh}+0+0")
                    w.update()
                except Exception as e2:
                    print(f"overrideredirect fallback failed: {e2}")

        _make_fullscreen()
        w.after(100, _make_fullscreen)
        w.after(500, _make_fullscreen)
        return

    try:
        if "qt" in backend:
            mgr.window.showFullScreen()
        elif "wx" in backend:
            mgr.frame.ShowFullScreen(True)
        elif "gtk" in backend:
            mgr.window.fullscreen()
        elif "macosx" in backend:
            mgr.full_screen_toggle()
        else:
            mgr.full_screen_toggle()
    except Exception as e:
        print(f"Could not enter fullscreen on backend '{backend}': {e}")
