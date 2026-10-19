from __future__ import annotations

from tkinter import font, ttk

from PIL import Image, ImageDraw, ImageTk


BACKGROUND = "#ffffff"
SURFACE = "#ffffff"
TEXT = "#262626"
MUTED = "#777772"
ACCENT = "#292929"
SIDEBAR = "#f7f7f5"
BORDER = "#e8e8e5"


def _rounded_element(style: ttk.Style, name: str, fill: str, *, outline: str | None = None,
                     states: tuple[tuple[str, str, str | None], ...] = (), radius: int = 8) -> str:
    """Nine-slice rounded surfaces retain native ttk focus and input behavior."""
    images = getattr(style.master, "_toolbox_theme_images", None)
    if images is None:
        images = style.master._toolbox_theme_images = []
    element = f"Toolbox{len(images)}.{name}"
    bitmaps = []

    def surface(color: str, edge: str | None):
        # ttk tiles the image center rather than stretching it. A 28 px image
        # leaves only a 4 px center on cards, producing thousands of CoreGraphics
        # image draws per panel on macOS. Keep a large tile and declare the small
        # minimum size separately so controls retain their compact geometry.
        size, scale = (512 if name == "Card" else 128), 3
        # All surfaces have a known solid parent color. Precompose the corners
        # instead of making macOS alpha-blend every tiled image on each hover.
        backdrop = SIDEBAR if name.startswith("Nav") else BACKGROUND
        bitmap = Image.new("RGB", (size * scale, size * scale), backdrop)
        ImageDraw.Draw(bitmap).rounded_rectangle(
            (1, 1, size * scale - 2, size * scale - 2), radius=radius * scale,
            fill=color, outline=edge, width=scale if edge else 1,
        )
        bitmap = bitmap.resize((size, size), Image.Resampling.LANCZOS)
        if getattr(style.master, "_toolbox_collect_surface_specs", False):
            bitmaps.append(bitmap)
        result = ImageTk.PhotoImage(bitmap, master=style.master)
        images.append(result)
        return result

    default = surface(fill, outline)
    variants = [(state, surface(color, edge)) for state, color, edge in states]
    minimum = 2 * (radius + 2)
    style.element_create(element, "image", default, *variants, border=radius + 2,
                         width=minimum, height=minimum, padding=0, sticky="nsew")
    if bitmaps:
        from .surface_cache import SurfaceSpec
        specs = getattr(style.master, "_toolbox_surface_specs", None)
        if specs is None:
            specs = style.master._toolbox_surface_specs = {}
        specs[element] = SurfaceSpec(tuple(bitmaps), tuple(state for state, _color, _edge in states), radius + 2, minimum)
    return element


def _button_style(style: ttk.Style, name: str, fill: str, foreground: str,
                  *, outline: str | None = None, hover: str, pressed: str,
                  padding: tuple[int, int] = (12, 7), anchor: str = "center") -> None:
    element = _rounded_element(style, name, fill, outline=outline, states=(
        ("disabled", "#f5f5f3", "#f0f0ed" if outline else None),
        ("pressed", pressed, outline),
        ("focus", hover, "#a3a39c"),
        ("active", hover, outline),
    ))
    style.layout(name, [(element, {"sticky": "nsew", "children": [
        ("Button.padding", {"sticky": "nsew", "children": [
            ("Button.label", {"sticky": "nsew"}),
        ]}),
    ]})])
    background = SIDEBAR if name.startswith("Nav") else BACKGROUND
    style.configure(name, background=background, foreground=foreground, padding=padding, anchor=anchor,
                    width=-1, borderwidth=0, relief="flat", bordercolor=background,
                    lightcolor=background, darkcolor=background)
    style.map(name, foreground=[("disabled", "#b5b5b0"), ("!disabled", foreground)])


def _choice_styles(style: ttk.Style) -> None:
    images = style.master._toolbox_theme_images

    def indicator(selected=False, disabled=False, focus=False):
        scale, size = 3, 18
        bitmap = Image.new("RGB", (size * scale, size * scale), BACKGROUND)
        draw = ImageDraw.Draw(bitmap)
        ink = "#b5b5b0" if disabled else ACCENT
        draw.rounded_rectangle((2 * scale, 2 * scale, 16 * scale, 16 * scale),
                               radius=3 * scale, fill="#f5f5f3" if disabled else SURFACE,
                               outline=ink if selected or focus else "#a3a39c", width=scale)
        if selected:
            draw.line([(5 * scale, 9 * scale), (8 * scale, 12 * scale),
                       (13 * scale, 6 * scale)], fill=ink, width=2 * scale,
                      joint="curve")
        photo = ImageTk.PhotoImage(bitmap.resize((size, size), Image.Resampling.LANCZOS), master=style.master)
        images.append(photo)
        return photo

    check = f"Toolbox{len(images)}.Check"
    style.element_create(check, "image", indicator(),
                         ("disabled", "selected", indicator(True, True)),
                         ("disabled", indicator(disabled=True)),
                         ("selected", indicator(True)),
                         ("focus", indicator(focus=True)), sticky="w", width=22)
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
        (check, {"side": "left", "sticky": "w"}),
        ("Checkbutton.focus", {"side": "left", "sticky": "nsew", "children": [
            ("Checkbutton.label", {"sticky": "nsew"}),
        ]}),
    ]})])
    style.configure("TCheckbutton", padding=(0, 6), background=BACKGROUND, foreground=TEXT)
    style.map("TCheckbutton", foreground=[("disabled", "#b5b5b0")],
              background=[("active", BACKGROUND)])

    option = _rounded_element(style, "Option", SURFACE, outline=BORDER, states=(
        ("disabled", "#f5f5f3", BORDER),
        ("selected", "#e9e9e4", "#92928a"),
        ("focus", "#f5f5f2", "#92928a"),
        ("active", "#f5f5f2", BORDER),
    ))
    style.layout("Option.TCheckbutton", [(option, {"sticky": "nsew", "children": [
        ("Checkbutton.padding", {"sticky": "nsew", "children": [
            (check, {"side": "left", "sticky": "w"}),
            ("Checkbutton.focus", {"side": "left", "sticky": "nsew", "children": [
                ("Checkbutton.label", {"sticky": "nsew"}),
            ]}),
        ]}),
    ]})])
    style.configure("Option.TCheckbutton", padding=(10, 6))

    group = _rounded_element(style, "ChoiceGroup", "#f1f1ed", radius=8)
    style.layout("ChoiceGroup.TFrame", [(group, {"sticky": "nsew", "children": [
        ("Frame.padding", {"sticky": "nsew"}),
    ]})])
    segment = _rounded_element(style, "Segment", "#f1f1ed", radius=6, states=(
        ("disabled", "#f5f5f3", None),
        ("selected", ACCENT, None),
        ("focus", "#e1e1db", "#92928a"),
        ("active", "#e7e7e1", None),
    ))
    style.layout("Segment.TRadiobutton", [(segment, {"sticky": "nsew", "children": [
        ("Radiobutton.padding", {"sticky": "nsew", "children": [
            ("Radiobutton.focus", {"sticky": "nsew", "children": [
                ("Radiobutton.label", {"sticky": "nsew"}),
            ]}),
        ]}),
    ]})])
    style.configure("Segment.TRadiobutton", padding=(12, 7), anchor="center", foreground=TEXT)
    style.map("Segment.TRadiobutton", foreground=[("disabled", "#b5b5b0"), ("selected", SURFACE)])


def configure_theme(style: ttk.Style) -> None:
    style.theme_use("clam")
    family = font.nametofont("TkDefaultFont", root=style.master).actual("family")
    style.configure(".", font=(family, 11, "normal"), background=BACKGROUND, foreground=TEXT)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    _button_style(style, "TButton", SURFACE, TEXT, outline=BORDER, hover="#f5f5f2", pressed="#eaeae6")
    _button_style(style, "Primary.TButton", ACCENT, SURFACE, hover="#41413e", pressed="#151515")
    _button_style(style, "Quiet.TButton", BACKGROUND, MUTED, hover="#f4f4f1", pressed="#eaeae6", padding=(10, 5))
    _button_style(style, "Link.TButton", SURFACE, TEXT, hover="#f5f5f2", pressed="#eaeae6", padding=(0, 4), anchor="w")
    _button_style(style, "Nav.TButton", SIDEBAR, "#50504c", hover="#eeeeea", pressed="#e5e5e0", padding=(10, 6), anchor="w")
    _button_style(style, "NavSelected.TButton", "#e9e9e4", TEXT, hover="#e4e4de", pressed="#ddddd7", padding=(10, 6), anchor="w")
    _button_style(style, "NavUpdate.TButton", "#d92d20", "#ffffff", hover="#b42318", pressed="#912018", padding=(10, 3))
    style.configure("NavUpdate.TButton", font=(family, 11, "bold"))
    style.configure("Link.TButton", font=(family, 12, "normal"))

    entry = _rounded_element(style, "Entry", "#fafaf8", outline=BORDER, states=(
        ("disabled", "#f6f6f3", BORDER), ("focus", SURFACE, "#92928a"),
    ))
    style.layout("TEntry", [(entry, {"sticky": "nsew", "children": [
        ("Entry.padding", {"sticky": "nsew", "children": [
            ("Entry.textarea", {"sticky": "nsew"}),
        ]}),
    ]})])
    style.configure("TEntry", padding=(9, 7), fieldbackground="#fafaf8", foreground=TEXT, insertcolor=TEXT)
    style.configure("Search.TEntry", background=SIDEBAR)
    style.map("TEntry", fieldbackground=[("focus", SURFACE)])
    style.configure("TLabelframe", bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", foreground=MUTED, font=(family, 10, "normal"))
    style.configure("TCheckbutton", padding=(0, 3), indicatorbackground=SURFACE, indicatorforeground=TEXT)
    style.configure("TRadiobutton", padding=(0, 3), indicatorbackground=SURFACE, indicatorforeground=TEXT)
    _choice_styles(style)
    style.map("TCheckbutton", background=[("active", BACKGROUND)])
    style.map("TRadiobutton", background=[("active", BACKGROUND)])
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, rowheight=30,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.configure("Treeview.Heading", background="#f6f6f3", foreground="#666660", padding=(8, 6),
                    font=(family, 10, "normal"), bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, relief="flat")
    style.map("Treeview", background=[("selected", "#eeeee9")], foreground=[("selected", TEXT)])
    style.configure("TProgressbar", background=ACCENT, troughcolor="#f1f1ed", borderwidth=0,
                    bordercolor=BACKGROUND, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("TSeparator", background=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.configure("TScrollbar", background="#dadad4", troughcolor=BACKGROUND, bordercolor=BACKGROUND,
                    lightcolor="#dadad4", darkcolor="#dadad4", arrowcolor=MUTED, arrowsize=10)
    style.configure("PageTitle.TLabel", font=(family, 18, "bold"))
    style.configure("Hero.TLabel", font=(family, 25, "normal"))
    style.configure("PanelTitle.TLabel", font=(family, 12, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Small.TLabel", foreground=MUTED, font=(family, 10, "normal"))
    style.configure("Sidebar.TFrame", background=SIDEBAR)
    style.configure("AppTitle.TLabel", background=SIDEBAR, foreground=TEXT, font=(family, 14, "bold"))
    style.configure("SidebarMuted.TLabel", background=SIDEBAR, foreground=MUTED, font=(family, 10, "normal"))
    style.configure("Section.TLabel", background=SIDEBAR, foreground="#93938c", font=(family, 10, "normal"))
    card = _rounded_element(style, "Card", SURFACE, outline=BORDER, radius=10)
    style.layout("Card.TFrame", [(card, {"sticky": "nsew", "children": [
        ("Frame.padding", {"sticky": "nsew"}),
    ]})])
    style.configure("Card.TFrame", background=SURFACE)
    style.layout("TLabelframe", [(card, {"sticky": "nsew"})])
    style.configure("Card.TLabel", background=SURFACE)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=(family, 10, "normal"))
    style.configure("CardTitle.TLabel", background=SURFACE, font=(family, 12, "normal"))
    style.configure("Metric.TLabel", background=SURFACE, foreground=TEXT, font=(family, 11, "normal"))
    style.configure("Success.Metric.TLabel", foreground=TEXT)
    style.configure("Failure.Metric.TLabel", foreground=TEXT)
    style.configure("DiagnosticStatus.TLabel", font=(family, 10, "normal"), foreground="#62625c")
    style.configure("Status.TLabel", background=BACKGROUND, foreground="#9a9a94", font=(family, 10, "normal"))
    style.configure("Status.TFrame", background=BACKGROUND)
    for widget in ("Text", "Listbox"):
        for option, value in (("background", SURFACE), ("foreground", TEXT),
                              ("selectBackground", "#eeeee9"), ("selectForeground", TEXT),
                              ("highlightThickness", 0), ("font", (family, 11, "normal"))):
            style.master.option_add(f"*{widget}.{option}", value)
