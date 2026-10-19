"""Opt-in, size-specific backgrounds for the rendering prototype.

The native ttk labels, entry fields, state maps and keyboard bindings remain
intact. Only the nine-slice background is composed ahead of time.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from tkinter import ttk

from PIL import Image, ImageTk


@dataclass(frozen=True)
class SurfaceSpec:
    bitmaps: tuple[Image.Image, ...]
    states: tuple[str, ...]
    border: int
    minimum: int


def compose_surface(source: Image.Image, width: int, height: int, border: int) -> Image.Image:
    """Replicate ttk's nine regions without resampling their original pixels."""
    result = Image.new(source.mode, (width, height))
    sx = (0, border, source.width - border, source.width)
    sy = (0, border, source.height - border, source.height)
    dx = (0, border, width - border, width)
    dy = (0, border, height - border, height)
    for row in range(3):
        for col in range(3):
            tile = source.crop((sx[col], sy[row], sx[col + 1], sy[row + 1]))
            right, bottom = dx[col + 1], dy[row + 1]
            for x in range(dx[col], right, tile.width):
                for y in range(dy[row], bottom, tile.height):
                    part = tile.crop((0, 0, min(tile.width, right - x), min(tile.height, bottom - y)))
                    result.paste(part, (x, y))
    return result


def _cached_bitmap(source: Image.Image, width: int, height: int, border: int) -> Image.Image:
    # ttk 8.6's tile loop includes y == bottom. A source exactly as high as its
    # parcel causes an extra zero-height image draw on macOS. An invisible guard
    # row/column keeps the full-size background on the single clipped-copy path.
    result = Image.new(source.mode, (width + 1, height + 1))
    result.paste(compose_surface(source, width, height, border), (0, 0))
    return result


@dataclass
class _Entry:
    key: tuple[str, int, int]
    style: str
    photos: tuple[ImageTk.PhotoImage, ...]
    pixels: int


@dataclass
class _Widget:
    base: str
    assigned: tuple[str, int, int] | None = None
    after_id: str | None = None
    bindings: tuple[str, str] | None = None
    prepared: bool = False
    geometry: tuple[int, int] | None = None


class SurfaceCache:
    def __init__(self, root, *, max_entries: int = 32, max_pixels: int = 4_000_000) -> None:
        self.root = root
        self.style = ttk.Style(root)
        self.max_entries = max_entries
        self.max_pixels = max_pixels
        self.entries: dict[tuple[str, int, int], _Entry] = {}
        self.widgets: dict[object, _Widget] = {}
        self.pixel_count = 0
        self.build_count = 0
        self.closed = False
        self._slots: list[_Entry] = []
        self._spares: dict[str, list[_Entry]] = {}

    def prepare_styles(self, sizes: dict[str, int]) -> None:
        """Register a bounded pool before widgets exist to avoid theme-wide
        invalidation when a module is opened for the first time."""
        for base, count in sizes.items():
            self._spares[base] = []
            for _ in range(count):
                if len(self._slots) >= self.max_entries:
                    break
                self._spares[base].append(self._new_slot(base))

    def _new_slot(self, base: str) -> _Entry:
        layout = self.style.layout(base)
        spec = self.root._toolbox_surface_specs[layout[0][0]]
        photos = tuple(ImageTk.PhotoImage(Image.new(bitmap.mode, (1, 1)), master=self.root)
                       for bitmap in spec.bitmaps)
        number = len(self._slots)
        element = f"SizedSurface{number}"
        cached_style = f"SizedSurface{number}.{base}"
        self.style.element_create(element, "image", photos[0], *zip(spec.states, photos[1:]),
                                  border=0, width=spec.minimum, height=spec.minimum,
                                  padding=0, sticky="nsew")
        cached_layout = deepcopy(layout)
        cached_layout[0] = (element, cached_layout[0][1])
        self.style.layout(cached_style, cached_layout)
        entry = _Entry((base, 0, 0), cached_style, photos, len(photos))
        self._slots.append(entry)
        self.pixel_count += entry.pixels
        return entry

    def track(self, widget) -> None:
        if widget in self.widgets:
            return
        base = str(widget.cget("style")) or widget.winfo_class()
        if isinstance(widget, ttk.LabelFrame) and (
            str(widget.cget("labelanchor")) not in ("n", "nw", "ne")
            or widget.cget("labelwidget")
            or not self.style.lookup(base, "labeloutside")
        ):
            return
        layout = self.style.layout(base)
        if not layout or layout[0][0] not in getattr(self.root, "_toolbox_surface_specs", {}):
            return
        record = self.widgets[widget] = _Widget(base)
        record.bindings = (
            widget.bind("<Configure>", lambda _event: self._resize(widget), add="+"),
            widget.bind("<Destroy>", lambda event: self._remove(widget) if event.widget is widget else None, add="+"),
        )
        self._resize(widget)

    def track_tree(self, parent) -> None:
        for child in parent.winfo_children():
            if isinstance(child, (ttk.Button, ttk.Entry, ttk.LabelFrame)):
                self.track(child)
            self.track_tree(child)

    def set_style(self, widget, base: str) -> None:
        record = self.widgets.get(widget)
        if record is None:
            widget.configure(style=base)
            return
        record.base = base
        record.assigned = None
        self._apply(widget)

    def _resize(self, widget) -> None:
        if self.closed:
            return
        record = self.widgets[widget]
        if record.assigned is not None and record.geometry == (widget.winfo_width(), widget.winfo_height()):
            return
        if record.after_id is not None:
            widget.after_cancel(record.after_id)
        # During a resize use the original scalable skin until the dimensions
        # settle; an old full-size bitmap must never tile into a different size.
        if record.assigned is not None:
            record.assigned = None
            widget.configure(style=record.base)
        if not record.prepared and min(widget.winfo_width(), widget.winfo_height()) > 20:
            self._apply(widget)
        else:
            record.after_id = widget.after(40, lambda: self._apply(widget))

    def _entry(self, base: str, width: int, height: int) -> _Entry | None:
        key = (base, width, height)
        if key in self.entries:
            return self.entries[key]
        layout = self.style.layout(base)
        spec = self.root._toolbox_surface_specs[layout[0][0]]
        if min(width, height) < spec.minimum:
            return None
        pixels = (width + 1) * (height + 1) * len(spec.bitmaps)
        if pixels > self.max_pixels:
            return None
        pinned = {record.assigned for record in self.widgets.values()}
        reusable = next((entry for entry in self.entries.values()
                         if entry.key[0] == base and entry.key not in pinned
                         and self.pixel_count - entry.pixels + pixels <= self.max_pixels), None)
        spares = self._spares.get(base, [])
        spare = spares[-1] if spares else None
        if spare is not None and self.pixel_count - spare.pixels + pixels <= self.max_pixels:
            entry = spares.pop()
        elif reusable is not None:
            entry = reusable
            del self.entries[entry.key]
        elif base in self._spares or len(self._slots) >= self.max_entries or self.pixel_count + pixels > self.max_pixels:
            # A prepared style never adds more elements at runtime. If every
            # slot is pinned, retain the original identical scalable background.
            return None
        else:
            entry = self._new_slot(base)
            if self.pixel_count - entry.pixels + pixels > self.max_pixels:
                return None
        self.pixel_count -= entry.pixels
        for photo, bitmap in zip(entry.photos, spec.bitmaps):
            photo.tk.call(str(photo), "configure", "-width", width + 1, "-height", height + 1)
            photo.paste(_cached_bitmap(bitmap, width, height, spec.border))
        entry.key, entry.pixels = key, pixels
        self.entries[key] = entry
        self.pixel_count += pixels
        self.build_count += 1
        return entry

    def _apply(self, widget) -> None:
        if self.closed or widget not in self.widgets:
            return
        record = self.widgets[widget]
        if record.after_id is not None:
            widget.after_cancel(record.after_id)
            record.after_id = None
        geometry = (widget.winfo_width(), widget.winfo_height())
        if record.assigned is not None and record.geometry == geometry:
            return
        entry = self._entry(record.base, *self._size(widget))
        target_style = entry.style if entry else record.base
        if str(widget.cget("style")) != target_style:
            widget.configure(style=target_style)
        record.assigned = entry.key if entry else None
        record.prepared = record.prepared or entry is not None
        record.geometry = geometry

    def _size(self, widget) -> tuple[int, int]:
        width, height = widget.winfo_width(), widget.winfo_height()
        if isinstance(widget, ttk.LabelFrame) and widget.cget("text") and height > 1:
            # ttk places the border below the label's top margin. Its image
            # parcel is shorter than the widget, even with identical geometry.
            # Derive it before the first draw; identify() is unavailable until
            # the native layout has been placed and would force a second paint.
            base = self.widgets[widget].base
            label_font = self.style.lookup(base + ".Label", "font")
            label_height = int(widget.tk.call("font", "metrics", label_font, "-linespace"))
            margins = self.style.lookup(base, "labelmargins")
            parts = [widget.winfo_pixels(value) for value in widget.tk.splitlist(margins)] if margins else [8, 0, 8, 0]
            if len(parts) == 1:
                parts *= 4
            elif len(parts) == 2:
                parts *= 2
            elif len(parts) == 3:
                parts.append(parts[1])
            height -= label_height + parts[1] + parts[3]
        return width, height

    def flush(self) -> None:
        """Prepare settled sizes, also used by repeatable screenshot benchmarks."""
        for widget in tuple(self.widgets):
            self._apply(widget)

    def _remove(self, widget) -> None:
        record = self.widgets.pop(widget, None)
        if record is not None and record.after_id is not None:
            widget.after_cancel(record.after_id)

    def close(self) -> None:
        self.closed = True
        for widget, record in tuple(self.widgets.items()):
            if record.after_id is not None:
                widget.after_cancel(record.after_id)
            for event, binding in zip(("<Configure>", "<Destroy>"), record.bindings or ()):
                widget.unbind(event, binding)
            widget.configure(style=record.base)
        self.widgets.clear()
