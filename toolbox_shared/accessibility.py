"""Explicit Tk 9.1 macOS AX metadata; Tk 8.6 remains a supported no-op.

Use Tk's native commands directly: its script-level ensemble is disabled when
VoiceOver is absent, even when another accessibility client needs the controls.
This does not request, grant or bypass the controlling client's AX permission.
"""
from __future__ import annotations

import tkinter as tk


class AccessibilityElement:
    def __init__(self, widget, *, name, role, variable=None, help_text="", action=None):
        self.widget = widget
        self.name, self.role = name, role
        self.variable, self.help_text, self.action = variable, help_text, action
        self.pending = None
        self.trace = None
        self.supported = bool(widget.tk.call(
            "info", "commands", "::tk::accessible::add_acc_object"
        )) and widget.tk.call("tk", "windowingsystem") == "aqua"
        widget._accessibility_element = self
        if self.supported:
            widget.bind("<Map>", self.schedule, add="+")
            widget.bind("<Destroy>", self.close, add="+")
            if variable is not None:
                self.trace = variable.trace_add("write", self.schedule)
            self.schedule()

    def schedule(self, *_args):
        if self.pending is None:
            self.pending = self.widget.after_idle(self.refresh)

    def refresh(self):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        w = self.widget
        if not self.supported or not w.winfo_ismapped():
            return
        fields = {"role": self.role, "name": self.name,
                  "description": self.name, "help": self.help_text}
        if self.variable is not None:
            fields["value"] = self.variable.get()
        if "state" in w.keys():
            fields["state"] = str(w.cget("state"))
        if self.action is not None:
            fields["action"] = self.action
        for field, value in fields.items():
            w.tk.call(f"::tk::accessible::set_acc_{field}", w, value)
        w.tk.call("::tk::accessible::add_acc_object", w)
        w.tk.call("::tk::accessible::emit_selection_change", w)

    def close(self, event):
        if event.widget is not self.widget:
            return
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.trace is not None:
            self.variable.trace_remove("write", self.trace)
            self.trace = None


def describe(widget, *, name, role, variable=None, help_text="", invoke=False):
    """Describe a real widget, keeping its existing command and validation."""
    return AccessibilityElement(
        widget, name=name, role=role, variable=variable, help_text=help_text,
        action=(str(widget), "invoke") if invoke else None,
    )


def refresh(widget):
    element = getattr(widget, "_accessibility_element", None)
    if element is not None and element.supported:
        element.refresh()
