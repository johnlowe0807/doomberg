"""Pixel chart geometry and terminal image transport, independent of text columns."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from functools import lru_cache

import numpy as np
import re
from PIL import Image, ImageDraw, ImageFont
from rich.segment import Segment
from rich.text import Text
from textual.events import Resize
from textual.containers import Container
from textual.message import Message
from textual_plotext import PlotextPlot

# A terminal that cannot report pixel dimensions is an ordinary fallback, not a startup error.
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


def plot_bounds(width: int, font_size: int) -> tuple[int, int]:
    return min(width // 3, max(64, int(font_size * 7.5))), max(1, width - 12)


@lru_cache(maxsize=16)
def font(size: int, path: str = ""):
    for name in (path, "/System/Library/Fonts/Menlo.ttc", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf"):
        if not name:
            continue
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def number(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.2f}B"
    if a >= 1e6:
        return f"{v / 1e6:.2f}M"
    if a >= 1e4:
        return f"{v / 1e3:.1f}K"
    if 0 < a < .01:
        return f"{v:.2g}"
    return f"{v:.2f}"


def render_text(scene: Scene, plot: PlotextPlot) -> None:
    """Text transport for the same geometry scene, preserving native bars and data gaps."""
    if scene.panels and scene.panels[0].independent:
        plot.refresh()  # Its render method reserves native text columns for all independent scales.
        return
    plt = plot.plt
    plt.clear_figure()
    n = len(scene.dates)
    if not n or not scene.panels:
        plot.refresh()
        return
    x = np.arange(n)
    if len(scene.panels) > 1:
        plt.subplots(len(scene.panels), 1)

    def color(value):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)) if isinstance(value, str) and value.startswith("#") else value

    axes = []
    for index, panel in enumerate(scene.panels):
        target = plt.subplot(index + 1, 1) if len(scene.panels) > 1 else plt
        if index:
            target.plotsize(None, max(5, plot.size.height // (len(scene.panels) + 2)))
        target.title(panel.title)
        target.ticks_color(color(FG))
        target.xlim(-.5, n - .5)

        def transform(values):
            values = np.asarray(values, dtype=float)
            if panel.log:
                with np.errstate(divide="ignore", invalid="ignore"):
                    values = np.where(values > 0, np.log10(values), np.nan)
            return values

        arrays = [transform(trace.values).ravel() for trace in panel.traces if trace.axis != 'right']
        if panel.bars is not None:
            arrays += [transform(panel.bars), np.array([0.])]
        if index == 0 and scene.candles is not None:
            arrays.append(transform(scene.candles).ravel())
        if index == 0 and scene.iex is not None:
            arrays.append(transform([scene.iex]))
        valid = np.concatenate(arrays) if arrays else np.array([0., 1.])
        valid = valid[np.isfinite(valid)]
        lo, hi = (float(valid.min()), float(valid.max())) if len(valid) else (0., 1.)
        if panel.limits and np.isfinite(panel.limits).all() and panel.limits[1] > panel.limits[0]:
            lo, hi = panel.limits
        else:
            pad = (hi - lo) * .06 if hi > lo else max(abs(lo) * .01, .01)
            lo, hi = lo - pad, hi + pad
        target.ylim(lo, hi)
        positions = [30., 50., 70.] if panel.limits == (0, 100) else list(np.linspace(lo, hi, 3 if index else 6))
        axes.append((target, positions, [number(10 ** value if panel.log else value) for value in positions], 'left'))
        right_values = [transform(trace.values).ravel() for trace in panel.traces if trace.axis == 'right']
        if right_values:
            valid_right = np.concatenate(right_values)
            valid_right = valid_right[np.isfinite(valid_right)]
            rlo, rhi = (float(valid_right.min()), float(valid_right.max())) if len(valid_right) else (0., 1.)
            pad = (rhi - rlo) * .06 if rhi > rlo else max(abs(rlo) * .01, .01)
            rlo, rhi = rlo - pad, rhi + pad
            target.ylim(rlo, rhi, yside='right')
            right_positions = list(np.linspace(rlo, rhi, 6))
            axes.append((target, right_positions, [number(v) for v in right_positions], 'right'))
        for value, shade in panel.guides:
            value = transform([value])[0]
            if np.isfinite(value):
                target.hline(value, color(shade))
        if panel.bars is not None:
            values = transform(panel.bars)[:n]
            shades = panel.bar_colors or [FG] * len(values)
            for shade in dict.fromkeys(shades):
                indices = [i for i, value in enumerate(values) if np.isfinite(value) and shades[i] == shade]
                if indices:
                    target.bar(indices, values[indices].tolist(), color=color(shade), width=scene.candle_width, marker="▌")
        if index == 0 and scene.candles is not None:
            values = transform(scene.candles)[:n]
            valid = np.isfinite(values).all(axis=1)
            if scene.ctype == "candle" and n > max(1, (plot.size.width - 12) // 4):
                if valid.any():
                    target.candlestick(x[valid].tolist(), dict(zip(("Open", "High", "Low", "Close"), values[valid].T.tolist())),
                                       colors=[color(UP), color(DOWN)])
            else:
                for i, (o, h, l, c) in enumerate(values):
                    if not valid[i]:
                        continue
                    shade = color(UP if c >= o else DOWN)
                    target.plot([i, i], [l, h], color=shade, marker="│")
                    if scene.ctype == "ohlc":
                        target.scatter([i], [o], color=shade, marker="╴")
                        target.scatter([i], [c], color=shade, marker="╶")
                    else:
                        per = max(1., (plot.size.width - 12) / n)
                        body_width = min(scene.candle_width, max(1, int(per) - 3) / per)
                        target.bar([i], [max(o, c)], minimum=min(o, c), width=body_width, color=shade, marker="█")
            if scene.point_size > 0:
                for up, shade in ((True, UP), (False, DOWN)):
                    indices = np.flatnonzero(valid & ((values[:, 3] >= values[:, 0]) == up))
                    if len(indices):
                        target.scatter(indices.tolist(), values[indices, 3].tolist(), color=color(shade),
                                       marker="●" if scene.point_size >= 2 else "•")
        for trace in panel.traces:
            values = transform(trace.values)[:n]
            indices = np.flatnonzero(np.isfinite(values))
            for run in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1):
                if len(run):
                    target.plot(run.tolist(), values[run].tolist(), color=color(trace.color),
                                yside=trace.axis,
                                label=trace.label, marker="█" if scene.line_width >= 2 else "braille",
                                fillx=index == 0 and scene.ctype == "area" and trace is panel.traces[0])
                    if scene.point_size > 0:
                        target.scatter(run.tolist(), values[run].tolist(), color=color(trace.color),
                                       yside=trace.axis,
                                       marker="●" if scene.point_size >= 2 else "•")
        if index == 0 and scene.iex is not None:
            value = transform([scene.iex])[0]
            if np.isfinite(value):
                target.scatter(list(range(0, n, 3)), [value] * len(range(0, n, 3)), color=color(YELLOW), marker="─")
                target.scatter([n - 1], [value], color=color(YELLOW), marker="◆")
        if scene.crosshair is not None:
            target.vline(min(n - 1, max(0, scene.crosshair)), color(MAGENTA))
        step = max(1, int(np.ceil(n / max(1, min(6, plot.size.width // 12)))))
        target.xticks(x[::step].tolist(), scene.dates[::step] if index == len(scene.panels) - 1 else [""] * len(x[::step]))
    widths = {side: max((len(label) for _, _, labels, axis in axes if axis == side for label in labels), default=1)
              for side in ('left', 'right')}
    for target, positions, labels, side in axes:
        target.yticks(positions, [label.rjust(widths[side]) for label in labels], yside=side)
    plot.refresh()


def color_dual_axes(rendered, scene):
    """Plotext shares one tick color; style its two native numeric axes after ANSI conversion."""
    if scene is None or len(scene.panels) != 1 or not any(t.axis == 'right' for t in scene.panels[0].traces):
        return rendered
    left = next(t.color for t in scene.panels[0].traces if t.axis != 'right')
    right = next(t.color for t in scene.panels[0].traces if t.axis == 'right')
    def shade(value):
        return value if isinstance(value, str) else '#%02x%02x%02x' % value
    offset = 0
    for line in rendered.plain.splitlines(keepends=True):
        for pattern, color in ((r'^[ \-−+\d.eKMB%]+(?=[│┤├┼])', left),
                               (r'(?<=[│┤├┼])[ \-−+\d.eKMB%]+(?=\n?$)', right)):
            found = re.search(pattern, line)
            if found and any(c.isdigit() for c in found[0]):
                rendered.stylize(shade(color), offset + found.start(), offset + found.end())
        offset += len(line)
    return rendered


def independent_scales(panel: Panel, count: int = 6):
    """Each native series maps to the same geometry window, with its own labelled bounds."""
    scales = []
    for trace in panel.traces:
        values = np.asarray(trace.values, dtype=float)
        finite = values[np.isfinite(values)]
        lo, hi = (float(finite.min()), float(finite.max())) if len(finite) else (0., 1.)
        pad = (hi - lo) * .06 if hi > lo else max(abs(lo) * .01, .01)
        lo, hi = lo - pad, hi + pad
        labels = [number(value) if len(finite) else 'N/A' for value in np.linspace(hi, lo, count)]
        scales.append((trace, (values - lo) / (hi - lo), labels))
    return scales


def _rich_color(value):
    return value if isinstance(value, str) else '#%02x%02x%02x' % value


def render_stacked_text(plot: PlotextPlot, scene: Scene) -> Text:
    """Plot independent curves in one canvas and prepend native terminal text scale columns."""
    scales = independent_scales(scene.panels[0], 6 if plot.size.height >= 16 else 3)
    if not scales or not scene.dates:
        return Text('No data', style=FG)
    widths = [max(3, *(len(label) for label in labels)) for _, _, labels in scales]
    gutter = sum(width + 2 for width in widths)
    width, height = plot.size.width - gutter, plot.size.height
    if width < 16 or height < 8:
        return Text(f'Enlarge chart to fit {len(scales)} left-hand scales', style=FG)
    plt = plot.plt
    plt.clear_figure()
    plt.plotsize(width, height)
    plt._set_size(width, height)
    plt.theme(plot._get_plotext_theme_name(plot.app.theme))
    plt.frame(True)
    plt.title(scene.panels[0].title)
    plt.ylim(0, 1)
    plt.yticks(np.linspace(0, 1, len(scales[0][2])).tolist(), [''] * len(scales[0][2]))
    plt.yticks([], yside='right')
    n = len(scene.dates)
    plt.xlim(-.5, n - .5)
    step = max(1, int(np.ceil(n / max(1, min(6, width // 12)))))
    plt.xticks(list(range(0, n, step)), scene.dates[::step])
    for trace, values, _ in scales:
        color = trace.color
        if isinstance(color, str) and color.startswith('#'):
            color = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        indices = np.flatnonzero(np.isfinite(values))
        for run in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1):
            if len(run):
                plt.plot(run.tolist(), values[run].tolist(), color=color, label=trace.label,
                         marker='█' if scene.line_width >= 2 else 'braille',
                         fillx=scene.ctype == 'area' and trace is scales[0][0])
                if scene.point_size > 0 or len(run) == 1:
                    plt.scatter(run.tolist(), values[run].tolist(), color=color, marker='●')
    if scene.crosshair is not None:
        plt.vline(min(n - 1, max(0, scene.crosshair)), (255, 95, 210))
    lines = Text.from_ansi(plt.build()).split('\n')
    if lines and not lines[-1].plain:
        lines.pop()
    top = next((i for i, line in enumerate(lines) if '┌' in line.plain), 1)
    bottom = next((i for i in range(len(lines) - 1, -1, -1) if '└' in lines[i].plain), len(lines) - 2)
    ticks = {round(top + 1 + fraction * max(0, bottom - top - 2)): index
             for index, fraction in enumerate(np.linspace(0, 1, len(scales[0][2])))}
    result = Text()
    for row, line in enumerate(lines):
        for index, ((trace, _, labels), field_width) in enumerate(zip(scales, widths), 1):
            label = f'[{index}]' if row == top else labels[ticks[row]] if row in ticks else ''
            marker = '┤' if row in ticks else '│' if top < row < bottom else ' '
            result.append(label.rjust(field_width) + marker + ' ', style=_rich_color(trace.color))
        result.append_text(line)
        if row < len(lines) - 1:
            result.append('\n')
    return result


def render_stacked_pixels(scene: Scene, size: tuple[int, int]) -> Image.Image:
    """One independent, color-matched scale column per series, all left of the shared canvas."""
    width, height = (max(1, int(value)) for value in size)
    scale = 2
    canvas = Image.new('RGB', (width * scale, height * scale), 'black')
    draw = ImageDraw.Draw(canvas)
    scales = independent_scales(scene.panels[0], 6 if height >= 180 else 3)
    size_px = max(8, scene.font_size)
    while True:
        ft = font(size_px * scale, scene.font_path)
        widths = [max(draw.textlength(label, font=ft) for label in [f'[{i}]', *labels]) + 14 * scale
                  for i, (_, _, labels) in enumerate(scales, 1)]
        left = int(sum(widths) + 6 * scale)
        if left + 100 * scale <= width * scale or size_px <= 8:
            break
        size_px -= 1
    right = (width - 12) * scale
    pw = right - left
    line_h = (size_px + 5) * scale
    # Every series remains identifiable even when its full name needs to wrap.
    legend = [(scene.panels[0].title, FG)] + [(trace.label, trace.color) for trace, _, _ in scales]
    legend_rows, x = 1, left
    layout = []
    for label, color in legend:
        tw = draw.textlength(label, font=ft)
        if tw > pw:
            label = label.split(' ', 1)[0]
            tw = draw.textlength(label, font=ft)
        if x > left and x + tw > right:
            legend_rows += 1; x = left
        layout.append((x, (legend_rows - 1) * line_h, label, color))
        x += tw + 12 * scale
    top, bottom = legend_rows * line_h, height * scale - line_h - 6 * scale
    gh = bottom - top
    if not scales or not scene.dates or pw < 60 * scale or gh < 25 * scale:
        draw.text((8, 8), f'Enlarge chart to fit {len(scales)} left-hand scales', font=ft, fill=FG)
        return canvas.resize((width, height), Image.Resampling.LANCZOS)
    for x, y, label, color in layout:
        draw.text((x, y), label, fill=color, font=ft)
    x = 0
    for i, ((trace, _, labels), field_width) in enumerate(zip(scales, widths), 1):
        edge = x + field_width - 6 * scale
        draw.text((x, 0), f'[{i}]', fill=trace.color, font=ft)
        draw.line((edge, top, edge, bottom), fill=trace.color, width=scale)
        for fraction, label in zip(np.linspace(0, 1, len(labels)), labels):
            yy = top + fraction * (gh - 1)
            text_y = max(top, min(bottom - line_h, yy - line_h / 2))
            draw.text((edge - 5 * scale - draw.textlength(label, font=ft), text_y), label, fill=trace.color, font=ft)
            draw.line((edge - 3 * scale, yy, edge, yy), fill=trace.color, width=scale)
        x += field_width
    tile = Image.new('RGB', (pw, gh), 'black')
    td = ImageDraw.Draw(tile)
    for fraction in np.linspace(0, 1, len(scales[0][2])):
        yy = round(fraction * (gh - 1))
        td.line((0, yy, pw - 1, yy), fill='#10232D', width=scale)
    n = len(scene.dates)
    xs = (np.arange(n) + .5) * pw / n
    for trace, values, _ in scales:
        points = []
        def stroke():
            if len(points) > 1:
                if scene.ctype == 'area' and trace is scales[0][0]:
                    td.polygon([(points[0][0], gh - 1)] + points + [(points[-1][0], gh - 1)], fill='#092B39')
                td.line(points, fill=trace.color, width=max(1, round(scene.line_width * scale)), joint='curve')
            if scene.point_size > 0 or len(points) == 1:
                radius = max(1, round(scene.point_size * scale))
                for xx, yy in points:
                    td.ellipse((xx - radius, yy - radius, xx + radius, yy + radius), fill=trace.color)
        for i, value in enumerate(values):
            if np.isfinite(value):
                points.append((int(xs[i]), round((1 - value) * (gh - 1))))
            else:
                stroke(); points = []
        stroke()
    if scene.crosshair is not None:
        xx = int(xs[min(n - 1, max(0, scene.crosshair))])
        td.line((xx, 0, xx, gh - 1), fill=MAGENTA, width=scale)
    canvas.paste(tile, (left, top))
    draw.line((left, bottom, right, bottom), fill=DIM, width=scale)
    label_width = max(draw.textlength(label, font=ft) for label in scene.dates)
    count = max(1, min(6, int(pw / (label_width + 16 * scale))))
    for i in np.unique(np.linspace(0, n - 1, count, dtype=int)):
        label = scene.dates[i]
        xx = min(right - draw.textlength(label, font=ft), max(left, left + xs[i] - draw.textlength(label, font=ft) / 2))
        draw.text((xx, bottom + 4 * scale), label, fill=FG, font=ft)
    return canvas.resize((width, height), Image.Resampling.LANCZOS)


def render_chart(scene: Scene, size: tuple[int, int]) -> Image.Image:
    """Rasterize a snapshot off the UI thread. Nonfinite values leave gaps."""
    if scene.panels and scene.panels[0].independent:
        return render_stacked_pixels(scene, size)
    scale = 2
    width, height = size = tuple(max(1, int(value)) for value in size)
    canvas = Image.new("RGB", (max(1, width) * scale, max(1, height) * scale), "black")
    d = ImageDraw.Draw(canvas)
    ft = font(scene.font_size * scale, scene.font_path)
    if width < 110 or height < max(70, len(scene.panels) * (scene.font_size + 20)) or not scene.dates or not scene.panels:
        d.text((8, 8), "No data" if not scene.dates or not scene.panels else "Enlarge chart", fill=FG, font=ft)
        return canvas.resize(size, Image.Resampling.LANCZOS)
    left, right = (v * scale for v in plot_bounds(width, scene.font_size))
    if any(trace.axis == 'right' for panel in scene.panels for trace in panel.traces):
        right = width * scale - left
    pw = right - left
    n = len(scene.dates)
    xs = (np.arange(n) + .5) * pw / n
    gap, label_h = 8 * scale, (scene.font_size + 6) * scale
    footer = label_h + 6 * scale
    available = height * scale - footer - gap * (len(scene.panels) - 1)
    weights = [3] + [1] * (len(scene.panels) - 1)
    top = 0
    for pi, (panel, weight) in enumerate(zip(scene.panels, weights)):
        ph = max(label_h + 2, int(available * weight / sum(weights)))
        bottom = min(height * scale - footer, top + ph)
        graph_top = top + label_h
        gh = max(2, bottom - graph_top)
        tile = Image.new("RGB", (pw, gh), "black")
        td = ImageDraw.Draw(tile)

        def transform(values):
            a = np.asarray(values, dtype=float)
            if panel.log:
                with np.errstate(divide="ignore", invalid="ignore"):
                    a = np.where(a > 0, np.log10(a), np.nan)
            return a

        arrays = [transform(t.values) for t in panel.traces if t.axis != 'right']
        if panel.bars is not None:
            arrays += [transform(panel.bars), np.array([0.])]
        if pi == 0 and scene.candles is not None:
            arrays.append(transform(scene.candles).ravel())
        if pi == 0 and scene.iex is not None:
            arrays.append(transform([scene.iex]))
        valid = np.concatenate([a.ravel() for a in arrays]) if arrays else np.array([0., 1.])
        valid = valid[np.isfinite(valid)]
        lo, hi = (float(valid.min()), float(valid.max())) if len(valid) else (0., 1.)
        if panel.limits and np.isfinite(panel.limits).all() and panel.limits[1] > panel.limits[0]:
            lo, hi = panel.limits
        else:
            pad = (hi - lo) * .06 if hi > lo else max(abs(lo) * .01, .01)
            lo, hi = lo - pad, hi + pad

        def y(value):
            return int(round((hi - value) / (hi - lo) * (gh - 1)))

        right_traces = [trace for trace in panel.traces if trace.axis == 'right']
        left_color = next((trace.color for trace in panel.traces if trace.axis != 'right'), FG) if right_traces else FG
        right_color = right_traces[0].color if right_traces else FG
        if right_traces:
            valid_right = np.concatenate([transform(trace.values) for trace in right_traces])
            valid_right = valid_right[np.isfinite(valid_right)]
            rlo, rhi = (float(valid_right.min()), float(valid_right.max())) if len(valid_right) else (0., 1.)
            pad = (rhi - rlo) * .06 if rhi > rlo else max(abs(rlo) * .01, .01)
            rlo, rhi = rlo - pad, rhi + pad
        def yr(value):
            return int(round((rhi - value) / (rhi - rlo) * (gh - 1)))

        tick_count = max(2, min(6 if pi == 0 else 3, gh // max(1, label_h)))
        ticks = [30., 50., 70.] if panel.limits == (0, 100) else np.linspace(lo, hi, tick_count)
        for value in ticks:
            yy = y(value)
            td.line((0, yy, pw - 1, yy), fill="#10232D", width=scale)
            label = number(10 ** value if panel.log else value)
            tw = d.textlength(label, font=ft)
            text_y = max(graph_top, min(bottom - label_h, graph_top + yy - label_h / 2))
            d.text((max(0, left - tw - 7 * scale), text_y), label, fill=left_color, font=ft)
        if right_traces:
            for value in np.linspace(rlo, rhi, tick_count):
                text_y = max(graph_top, min(bottom - label_h, graph_top + yr(value) - label_h / 2))
                d.text((right + 7 * scale, text_y), number(value), fill=right_color, font=ft)
        for value, color in panel.guides:
            yy = y(value)
            for xx in range(0, pw, 9 * scale):
                td.line((xx, yy, xx + 4 * scale, yy), fill=color, width=scale)
        body = max(1, int(pw / n * scene.candle_width))
        line_width = max(1, round(scene.line_width * scale))
        stroke_width = max(1, min(line_width, int(pw / n * .25)))
        if panel.bars is not None:
            for i, value in enumerate(transform(panel.bars)):
                if np.isfinite(value):
                    yy, zero = y(value), y(0)
                    td.rectangle((int(xs[i] - body / 2), min(yy, zero),
                                  int(xs[i] + body / 2), max(yy, zero)),
                                 fill=panel.bar_colors[i] if panel.bar_colors else FG)
        if pi == 0 and scene.candles is not None:
            for i, (o, h, l, c) in enumerate(transform(scene.candles)):
                if not np.isfinite([o, h, l, c]).all():
                    continue
                xx, color = int(xs[i]), UP if c >= o else DOWN
                td.line((xx, y(h), xx, y(l)), fill=color, width=stroke_width)
                if scene.ctype == "ohlc":
                    td.line((xx - body // 2, y(o), xx, y(o)), fill=color, width=stroke_width)
                    td.line((xx, y(c), xx + body // 2, y(c)), fill=color, width=stroke_width)
                else:
                    y1, y2 = sorted((y(o), y(c)))
                    td.rectangle((xx - body // 2, y1, xx + body // 2, max(y1 + scale - 1, y2)), fill=color)
                if scene.point_size > 0:
                    radius = max(1, round(scene.point_size * scale))
                    yy = y(c)
                    td.ellipse((xx - radius, yy - radius, xx + radius, yy + radius), fill=color)
        for trace in panel.traces:
            values = transform(trace.values)
            trace_y = yr if trace.axis == 'right' else y
            points = []

            def stroke():
                if len(points) > 1:
                    if pi == 0 and scene.ctype == "area" and trace is panel.traces[0]:
                        td.polygon([(points[0][0], gh - 1)] + points + [(points[-1][0], gh - 1)], fill="#092B39")
                    td.line(points, fill=trace.color, width=line_width, joint="curve")
                    if scene.point_size > 0:
                        radius = max(1, round(scene.point_size * scale))
                        for xx, yy in points:
                            td.ellipse((xx - radius, yy - radius, xx + radius, yy + radius), fill=trace.color)
                elif points:
                    radius = max(scale, round(scene.point_size * scale))
                    td.ellipse((points[0][0] - radius, points[0][1] - radius,
                                points[0][0] + radius, points[0][1] + radius), fill=trace.color)

            for i, value in enumerate(values):
                if np.isfinite(value):
                    points.append((int(xs[i]), trace_y(value)))
                else:
                    stroke()
                    points = []
            stroke()
        if pi == 0 and scene.iex is not None:
            yy = y(transform([scene.iex])[0])
            for xx in range(0, pw, 10 * scale):
                td.line((xx, yy, xx + 5 * scale, yy), fill=YELLOW, width=scale)
            xx = int(xs[-1])
            td.polygon([(xx, yy - 4 * scale), (xx + 4 * scale, yy),
                        (xx, yy + 4 * scale), (xx - 4 * scale, yy)], fill=YELLOW)
        if scene.crosshair is not None:
            xx = int(xs[min(n - 1, max(0, scene.crosshair))])
            td.line((xx, 0, xx, gh), fill=MAGENTA, width=scale)
        canvas.paste(tile, (left, graph_top))
        d.line((left, graph_top, left, bottom), fill=left_color if right_traces else DIM, width=scale)
        if right_traces:
            d.line((right, graph_top, right, bottom), fill=right_color, width=scale)
        d.line((left, bottom, right, bottom), fill=DIM, width=scale)
        # Legend wraps inside its panel rather than consuming terminal rows.
        xx = left
        for label, color in [(panel.title, FG)] + [(t.label, t.color) for t in panel.traces if t.label != panel.title]:
            tw = d.textlength(label, font=ft)
            if xx + tw > right:
                break
            d.text((xx, top), label, fill=color, font=ft)
            xx += tw + 14 * scale
        top = bottom + gap
    # One shared date axis; reserve enough pixel width to avoid overlapping dates.
    label_width = max(d.textlength(s, font=ft) for s in scene.dates)
    count = max(1, min(6, int(pw / (label_width + 16 * scale))))
    indices = np.unique(np.linspace(0, n - 1, count, dtype=int))
    for i in indices:
        label = scene.dates[i]
        tw = d.textlength(label, font=ft)
        xx = min(right - tw, max(left, left + xs[i] - tw / 2))
        d.text((xx, height * scale - footer + 4 * scale), label, fill=FG, font=ft)
    return canvas.resize(size, Image.Resampling.LANCZOS)


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
        self.scene: Scene | None = None

    def compose(self):
        if self.app.is_headless:
            self.backend = "text"
        text = PlotextPlot(id="plot")
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

    def submit(self, scene: Scene) -> None:
        configured = int(self.app.cfg.get("chart", "font_px", 0))
        # Text fallback already uses the terminal font. Match raster labels to its physical cell height.
        height = self.pixel_size()[1] / max(1, self.size.height) if self.pixels else 16
        scene = replace(scene, font_size=max(8, min(48, configured or round(height * .8))),
                        font_path=str(self.app.cfg.get("chart", "font_path", "")))
        self.scene = scene
        if not self.pixels:
            render_text(scene, self.query_one(PlotextPlot))
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
