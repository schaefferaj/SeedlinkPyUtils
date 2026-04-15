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


def create_filter_dropdown(fig, options, active, on_change, theme):
    """Create a native "Filter:" dropdown above the matplotlib canvas.

    Dispatches to a Tk (TkAgg) or Qt (QtAgg / PyQt5/6 / PySide2/6) backend
    helper based on the running matplotlib backend, and returns the created
    widget for lifetime management. Returns ``None`` when the backend is
    neither Tk nor Qt — callers should then fall back to an in-figure
    `RadioButtons` strip.
    """
    backend = matplotlib.get_backend().lower()
    if "tk" in backend:
        return _create_filter_dropdown_tk(fig, options, active, on_change, theme)
    if "qt" in backend:
        return _create_filter_dropdown_qt(fig, options, active, on_change, theme)
    return None


def _create_filter_dropdown_tk(fig, options, active, on_change, theme):
    """Tk backend: pack a ttk.Combobox strip above the canvas."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return None

    mgr = fig.canvas.manager
    window = getattr(mgr, "window", None)
    if window is None or not isinstance(window, (tk.Tk, tk.Toplevel)):
        return None
    try:
        canvas_widget = fig.canvas.get_tk_widget()
    except Exception:
        return None

    # Pack the filter strip above the canvas. `before=canvas_widget` slots us
    # between the matplotlib toolbar (packed first, at the top) and the
    # canvas (packed fill="both", expand=True just below it).
    frame = tk.Frame(window, bg=theme["bg"])
    frame.pack(before=canvas_widget, side="top", fill="x")

    tk.Label(frame, text="Filter:", bg=theme["bg"], fg=theme["fg"],
             font=("TkDefaultFont", 9, "bold"),
             padx=8, pady=2).pack(side="left")

    var = tk.StringVar(value=options[active])
    combo = ttk.Combobox(frame, textvariable=var, values=options,
                         state="readonly", width=14)
    combo.pack(side="left", padx=4, pady=2)

    combo.bind("<<ComboboxSelected>>", lambda _e: on_change(var.get()))
    frame._combo = combo
    return frame


def _create_filter_dropdown_qt(fig, options, active, on_change, theme):
    """Qt backend: add a QToolBar with a QComboBox to the top of the window."""
    try:
        from matplotlib.backends.qt_compat import QtCore, QtWidgets
    except Exception:
        return None

    mgr = fig.canvas.manager
    window = getattr(mgr, "window", None)
    if window is None or not hasattr(window, "addToolBar"):
        return None

    toolbar = QtWidgets.QToolBar("Filter", window)
    toolbar.setMovable(False)

    label = QtWidgets.QLabel("  Filter: ")
    # Light text on dark bg when dark-mode theme is active; leave default
    # otherwise so we inherit the desktop's native look.
    if theme.get("bg", "").lower() in ("#1a1a1a",) or theme.get("fg", "").lower() != "black":
        label.setStyleSheet(
            f"color: {theme['fg']}; background-color: {theme['bg']};"
        )
    toolbar.addWidget(label)

    combo = QtWidgets.QComboBox()
    combo.addItems(options)
    combo.setCurrentIndex(active)
    combo.currentTextChanged.connect(on_change)
    toolbar.addWidget(combo)

    window.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)
    # Stash on the toolbar so the combobox isn't garbage-collected.
    toolbar._combo = combo
    return toolbar


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
