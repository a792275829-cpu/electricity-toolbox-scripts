"""Shared choice controls retaining ttk variables, commands and keyboard input."""
from tkinter import ttk


class OptionCheckbutton(ttk.Checkbutton):
    """Independent multi-select option with a selected surface and check mark."""
    def __init__(self, parent, **kwargs):
        kwargs.setdefault("style", "Option.TCheckbutton")
        super().__init__(parent, **kwargs)


class SettingCheckbutton(ttk.Checkbutton):
    """Single boolean setting using the application's standard check indicator."""
    pass


class SegmentedGroup(ttk.Frame):
    """Mutually exclusive choices; expose native radios for busy-state handling."""
    def __init__(self, parent, *, variable, choices, command=None):
        super().__init__(parent, style="ChoiceGroup.TFrame", padding=2)
        self.buttons = []
        for index, (value, label) in enumerate(choices):
            button = ttk.Radiobutton(self, text=label, variable=variable, value=value,
                                     command=command, style="Segment.TRadiobutton")
            button.grid(row=0, column=index, sticky="nsew")
            self.columnconfigure(index, weight=1, uniform="choice")
            self.buttons.append(button)
