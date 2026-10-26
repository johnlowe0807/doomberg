"""FreeTerm plotting adapter and pane-local Sixel/Kitty/text transport.

Geometry lives in terminal_plot. Scene/Panel/Trace retain the existing view API.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field, replace
import numpy as np
from PIL import Image
from rich.segment import Segment
from rich.text import Text
from textual.events import Resize
from textual.containers import Container
from textual.message import Message
from textual_plotext import PlotextPlot
from terminal_plot import Figure, Axes, Series, Guide, snapshot, render_image, render_text
from terminal_plot.render import matched_font_size, plot_bounds, font
logging.getLogger("textual_image._terminal").setLevel(logging.ERROR)
from textual_image import renderable as image_renderable
from textual_image._terminal import CellSize, get_cell_size, get_tiocgwinsz
from textual_image._sixel import image_to_sixels
from textual_image.widget import TGPImage, SixelImage
from textual_image.widget.sixel import _ImageSixelImpl
from textual_image.renderable.tgp import _send_tgp_message
FG, DIM, UP, DOWN, YELLOW, MAGENTA = "#5FD7FF", "#3A6B80", "#00FF87", "#FF4D6D", "#FFD700", "#FF5FD2"

@dataclass
class Trace:
    label: str
    values: np.ndarray
    color: str | tuple
    axis: str = 'left'


@dataclass
class Panel:
    title: str
    traces: list[Trace] = field(default_factory=list)
    bars: np.ndarray | None = None
    bar_colors: list | None = None
    limits: tuple[float, float] | None = None
    guides: list[tuple[float, str]] = field(default_factory=list)
    log: bool = False
    independent: bool = False


@dataclass
class Scene:
    panels: list[Panel]
    dates: list[str]
    candles: np.ndarray | None = None  # open, high, low, close; one row per native bar
    ctype: str = "line"
    crosshair: int | None = None
    iex: float | None = None
    font_size: int = 12
    line_width: float = 1.
    point_size: float = 0.
    candle_width: float = .65
    font_path: str = ""


def to_figure(scene: Scene | Figure) -> Figure:
    """Translate existing financial view instructions; fetching/navigation stay in the views."""
    if isinstance(scene, Figure):
        return scene
    panels = []
    for index, panel in enumerate(scene.panels):
        axes = Axes(panel.title, limits=panel.limits, log=panel.log, independent=panel.independent,
                    xlimits=(-.5, len(scene.dates)-.5) if scene.dates else None,
                    weight=3. if index == 0 else 1.)
        axes.guides = [Guide(value, color) for value, color in panel.guides]
        if panel.bars is not None:
            axes.traces.append(Series('', panel.bars, FG, kind='bar', colors=panel.bar_colors, width=scene.candle_width))
        if index == 0 and scene.candles is not None:
            axes.traces.append(Series('', scene.candles, FG, kind='ohlc' if scene.ctype == 'ohlc' else 'candle', width=scene.candle_width))
        for j, trace in enumerate(panel.traces):
            axes.traces.append(Series(trace.label, trace.values, trace.color, trace.axis,
                                     kind='area' if index == 0 and j == 0 and scene.ctype == 'area' else 'line',
                                     baseline=None))
        if index == 0 and scene.iex is not None:
            axes.guides.append(Guide(scene.iex, YELLOW, marker_last=True, include_bounds=True))
        panels.append(axes)
    return Figure(panels, scene.dates, scene.crosshair, scene.font_size, scene.line_width,
                  scene.point_size, scene.font_path, sharex=True)


def render_chart(scene: Scene | Figure, size):
    """Compatibility export for callers that render a financial Scene directly."""
    return render_image(to_figure(scene), size)


class _TerminalPlot(PlotextPlot):
    """Keep render failures local without monkey-patching every PlotextPlot in the app."""
    def render(self):
        try:
            figure = self.parent.scene
            return render_text(figure, (self.size.width, self.size.height)) if figure is not None else Text('')
        except Exception as error:
            self.app.hub.warn(f"chart render {type(error).__name__}: {error}"[:120])
            return Text(f"chart unavailable ({type(error).__name__})", style=YELLOW)


class GraphicsFailed(Message):
    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason = reason


class PixelSizeChanged(Message):
    pass


class _TGPRenderable(image_renderable.TGPImage):
    def cleanup(self) -> None:
        if self.terminal_image_id:
            # Delete only this pane's image, including its storage, never other panes' placements.
            _send_tgp_message(a="d", d="I", i=self.terminal_image_id, q=2)
            self.terminal_image_id = None


class _SafeRenderable:
    def __init__(self, wrapped, widget):
        self.wrapped, self.widget = wrapped, widget

    def __rich_console__(self, console, options):
        try:
            yield from self.wrapped.__rich_console__(console, options)
        except Exception as e:
            self.widget.post_message(GraphicsFailed(str(e)))
            yield Segment(" ")


class _TGPImage(TGPImage, Renderable=_TGPRenderable):
    def render(self):
        try:
            rendered = super().render()
            return _SafeRenderable(rendered, self) if self.image else rendered
        except Exception as e:
            self.post_message(GraphicsFailed(str(e)))
            return ""

    def on_unmount(self) -> None:
        self.image = None  # release the terminal's image storage when a pane closes


class _SixelImpl(_ImageSixelImpl):
    def __init__(self, image, options, encoded=None):
        super().__init__(image, options)
        self._encoded = encoded

    def _image_to_sixels(self, image, options=None, background=None):
        # The common full-pane frame is encoded in the worker. Modal clipping can still
        # use the library's cropped-image path without painting over the modal.
        if self._encoded and self.image and image.size == self.image.size:
            return self._encoded
        return super()._image_to_sixels(image, options, background)

    def render_lines(self, crop):
        try:
            return super().render_lines(crop)
        except Exception as e:
            self.post_message(GraphicsFailed(str(e)))
            return []


class _SixelImage(SixelImage, Renderable=SixelImage._Renderable):
    def __init__(self, *args, **kwargs):
        self._encoded = None
        super().__init__(*args, **kwargs)

    def set_frame(self, image, encoded):
        self._encoded = encoded
        self.image = image

    def compose(self):
        yield _SixelImpl(self.image, self._sixel_options, self._encoded)


def _render_frame(scene: Scene, size: tuple[int, int], backend: str):
    image = render_chart(scene, size)
    return image, image_to_sixels(image) if backend == "sixel" else None


class ChartSurface(Container):
    """Own both backends. Keep only the newest pending raster job per pane."""
    DEFAULT_CSS = """
    ChartSurface { height: 1fr; width: 1fr; overflow: hidden hidden; }
    ChartSurface > * { height: 1fr; width: 1fr; }
    """

    def __init__(self, renderer: str = "auto", **kwargs):
        super().__init__(**kwargs)
        detected = "sixel" if image_renderable.Image is image_renderable.SixelImage else \
            "tgp" if image_renderable.Image is image_renderable.TGPImage else "text"
        self.backend = detected if renderer == "auto" else renderer if renderer in ("text", "tgp", "sixel") else detected
        self._pending = None
        self._rendering = False
        self._generation = 0
        self.last_image: Image.Image | None = None
        self.scene: Figure | None = None

    def compose(self):
        if self.app.is_headless:
            self.backend = "text"
        text = _TerminalPlot(id="plot")
        text.display = self.backend == "text"
        yield text
        if self.backend != "text":
            yield (_SixelImage if self.backend == "sixel" else _TGPImage)(id="pixels")

    def on_mount(self) -> None:
        self.query_one(PlotextPlot).theme = "clear"
        if self.pixels:
            self.app.message_signal.subscribe(self, self._terminal_event)

    def on_unmount(self) -> None:
        self._generation += 1
        self._pending = None
        self.app.message_signal.unsubscribe(self)

    def on_resize(self) -> None:
        if self.pixels:
            self.post_message(PixelSizeChanged())
        if self.scene is not None:
            self.call_after_refresh(self.redraw)

    def redraw(self) -> None:
        if self.is_mounted and self.scene is not None and self.display:
            self.submit(self.scene)

    def _terminal_event(self, event: Message) -> None:
        if isinstance(event, Resize) and event.pixel_size and event.size.width and event.size.height:
            width = event.pixel_size.width // event.size.width
            height = event.pixel_size.height // event.size.height
            if width and height:
                get_cell_size._result = CellSize(width, height)
                self.post_message(PixelSizeChanged())

    @property
    def pixels(self) -> bool:
        return self.backend != "text"

    def pixel_size(self) -> tuple[int, int]:
        cell = get_cell_size()
        # ioctl is safe while Textual owns stdin; never send a second terminal probe.
        try:
            rows, cols, width, height = get_tiocgwinsz()
            if rows and cols and width and height:
                cell = CellSize(max(1, width // cols), max(1, height // rows))
                get_cell_size._result = cell
        except (OSError, ValueError):
            pass
        return max(1, self.size.width * cell.width), max(1, self.size.height * cell.height)

    def submit(self, scene: Scene | Figure) -> None:
        scene = snapshot(to_figure(scene))
        configured = int(self.app.cfg.get("chart", "font_px", 0))
        # Text fallback uses the terminal font. Match raster glyph advances to reported cell geometry.
        size = self.pixel_size() if self.pixels else (max(1, self.size.width) * 8, max(1, self.size.height) * 16)
        width, height = size[0] // max(1, self.size.width), size[1] // max(1, self.size.height)
        path = str(self.app.cfg.get("chart", "font_path", ""))
        scene = replace(scene, font_size=max(8, min(512, configured)) if configured > 0 else matched_font_size(width, height, path),
                        font_path=path)
        self.scene = scene
        if not self.pixels:
            self.query_one(PlotextPlot).refresh()
            return
        self._generation += 1
        self._pending = (self._generation, scene, self.pixel_size())
        if not self._rendering:
            self._rendering = True
            self.run_worker(self._rasterize(), group="raster", exit_on_error=False)

    async def _rasterize(self) -> None:
        try:
            while self._pending is not None and self.pixels:
                generation, scene, size = self._pending
                self._pending = None
                image, encoded = await asyncio.to_thread(_render_frame, scene, size, self.backend)
                if generation == self._generation and self.is_mounted and self.pixels:
                    self.last_image = image
                    widget = self.query_one("#pixels")
                    if isinstance(widget, _SixelImage):
                        widget.set_frame(image, encoded)
                    else:
                        widget.image = image
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.post_message(GraphicsFailed(str(e)))
        finally:
            self._rendering = False

    def use_text(self) -> None:
        self.backend = "text"
        self._generation += 1
        self._pending = None
        for widget in self.query("#pixels"):
            widget.image = None
            widget.display = False
        self.query_one(PlotextPlot).display = True
        self.redraw()

    def on_graphics_failed(self, event: GraphicsFailed) -> None:
        if self.pixels:
            self.use_text()
            self.app.hub.warn(f"pixel chart unavailable; using text: {event.reason}"[:120])
            self.post_message(PixelSizeChanged())
        event.stop()
