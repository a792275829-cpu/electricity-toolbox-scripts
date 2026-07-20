from __future__ import annotations

from tkinter import ttk


def configure_theme(style: ttk.Style) -> None:
    style.configure("AppTitle.TLabel", font=("TkDefaultFont", 16, "bold"))
    style.configure("PageTitle.TLabel", font=("TkDefaultFont", 15, "bold"))
    style.configure("Section.TLabel", font=("TkDefaultFont", 10, "bold"), foreground="#7890ad")
    style.configure("Muted.TLabel", foreground="#607086")
    style.configure("Metric.TLabel", font=("TkDefaultFont", 20, "bold"))
    style.configure("DiagnosticStatus.TLabel", font=("TkDefaultFont", 11, "bold"))
    style.configure("Nav.TButton", anchor="w", padding=(14, 10))
    style.configure("NavSelected.TButton", anchor="w", padding=(14, 10), font=("TkDefaultFont", 9, "bold"))
    style.configure("Card.TFrame", relief="solid", borderwidth=1)
