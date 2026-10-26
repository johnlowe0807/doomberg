"""CPU renderers for the data-only plotting model (Pillow pixels and plotext text)."""
from __future__ import annotations
from functools import lru_cache
from datetime import datetime, timezone
import re
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rich.text import Text
from .model import Figure, Axes, snapshot
FG, DIM, UP, DOWN, YELLOW, MAGENTA = "#5FD7FF", "#3A6B80", "#00FF87", "#FF4D6D", "#FFD700", "#FF5FD2"

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


@lru_cache(maxsize=128)
def matched_font_size(width: int, height: int, path: str = "") -> int:
    """Match a raster monospace advance to one reported terminal character cell."""
    reference = font(32, path)
    size = max(8, min(512, height * 2, round(width * 32 / max(1, reference.getlength("0")))))
    while size > 8:
        face = font(size, path)
        box = face.getbbox("0123456789Ag")
        if face.getlength("0") <= width * 1.05 and box[3] - box[1] <= height * .95:
            break
        size -= 1
    return size


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



def color_dual_axes(rendered, scene):
    """Plotext shares one tick color; style its two native numeric axes after ANSI conversion."""
    if scene is None or len(scene.panels) != 1 or not any(t.axis == 'right' for t in scene.panels[0].traces):
        return rendered
    left = next((t.color for t in scene.panels[0].traces if t.axis != 'right'), FG)
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


def independent_scales(panel: Axes, count: int = 6):
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


def render_stacked_text(scene: Figure, size, plt) -> Text:
    """Plot independent curves in one canvas and prepend native terminal text scale columns."""
    scales = independent_scales(scene.panels[0], 6 if size[1] >= 16 else 3)
    if not scales or not any(len(t.values) for t in scene.panels[0].traces):
        return Text('No data', style=FG)
    widths = [max(3, *(len(label) for label in labels)) for _, _, labels in scales]
    gutter = sum(width + 2 for width in widths)
    width, height = size[0] - gutter, size[1]
    if width < 16 or height < 8:
        return Text(f'Enlarge chart to fit {len(scales)} left-hand scales', style=FG)
    plt.clear_figure()
    plt.plotsize(width, height)
    plt._set_size(width, height)
    plt.theme('clear')
    plt.frame(True)
    plt.title(scene.panels[0].title)
    plt.ylim(0, 1)
    plt.yticks(np.linspace(0, 1, len(scales[0][2])).tolist(), [''] * len(scales[0][2]))
    plt.yticks([], yside='right')
    xlo, xhi, positions, labels = x_axis(scene.panels[0])
    plt.xlim(xlo, xhi)
    plt.xticks(positions.tolist(), labels)
    for trace, values, _ in scales:
        color = trace.color
        if isinstance(color, str) and color.startswith('#'):
            color = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        indices = np.flatnonzero(np.isfinite(values) & np.isfinite(trace.x))
        for run in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1):
            if len(run):
                plt.plot(trace.x[run].tolist(), values[run].tolist(), color=color, label=trace.label,
                         marker='█' if scene.line_width >= 2 else 'braille',
                         fillx=trace.kind == 'area')
                if scene.point_size > 0 or len(run) == 1:
                    plt.scatter(trace.x[run].tolist(), values[run].tolist(), color=color, marker='●')
    if scene.crosshair is not None:
        plt.vline(scene.crosshair, (255, 95, 210))
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


def date_labels(dates, xs, left, right, draw, face, padding):
    """Keep endpoint labels on canvas without colliding with adjacent labels."""
    for count in range(min(6, len(dates)), 0, -1):
        labels, previous_end = [], None
        for index in np.unique(np.linspace(0, len(dates) - 1, count, dtype=int)):
            label = dates[index]
            width = draw.textlength(label, font=face)
            x = max(left, min(right - width, left + xs[index] - width / 2))
            if previous_end is not None and x < previous_end + padding:
                break
            labels.append((label, x))
            previous_end = x + width
        else:
            return labels
    return []


def render_stacked_pixels(scene: Figure, size: tuple[int, int]) -> Image.Image:
    """One independent, color-matched scale column per series, all left of the shared canvas."""
    width, height = (max(1, int(value)) for value in size)
    scale = 2
    canvas = Image.new('RGB', (width * scale, height * scale), 'black')
    draw = ImageDraw.Draw(canvas)
    scales = independent_scales(scene.panels[0], 6 if height >= 180 else 3)
    size_px = max(8, scene.font_size)
    ft = font(size_px * scale, scene.font_path)
    widths = [max(draw.textlength(label, font=ft) for label in [f'[{i}]', *labels]) + 14 * scale
              for i, (_, _, labels) in enumerate(scales, 1)]
    left = int(sum(widths) + 6 * scale)
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
    if not scales or not any(len(t.values) for t in scene.panels[0].traces) or pw < 60 * scale or gh < 25 * scale:
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
    xlo, xhi, positions, labels = x_axis(scene.panels[0])
    for trace, values, _ in scales:
        xs = (trace.x - xlo) / (xhi - xlo) * pw
        points = []
        def stroke():
            if len(points) > 1:
                if trace.kind == 'area':
                    td.polygon([(points[0][0], gh - 1)] + points + [(points[-1][0], gh - 1)], fill='#092B39')
                td.line(points, fill=trace.color, width=max(1, round(scene.line_width * scale)), joint='curve')
            if scene.point_size > 0 or len(points) == 1:
                radius = max(1, round(scene.point_size * scale))
                for xx, yy in points:
                    td.ellipse((xx - radius, yy - radius, xx + radius, yy + radius), fill=trace.color)
        for i, value in enumerate(values):
            if np.isfinite(value) and np.isfinite(xs[i]):
                points.append((int(xs[i]), round((1 - value) * (gh - 1))))
            else:
                stroke(); points = []
        stroke()
    if scene.crosshair is not None:
        xx = int((scene.crosshair - xlo) / (xhi - xlo) * pw)
        td.line((xx, 0, xx, gh - 1), fill=MAGENTA, width=scale)
    canvas.paste(tile, (left, top))
    draw.line((left, bottom, right, bottom), fill=DIM, width=scale)
    for label, xx in date_labels(labels, (positions - xlo) / (xhi - xlo) * pw, left, right, draw, ft, 16 * scale):
        draw.text((xx, bottom + 4 * scale), label, fill=FG, font=ft)
    return canvas.resize((width, height), Image.Resampling.LANCZOS)




def transform(values, log=False):
    values = np.asarray(values, dtype=float)
    if log:
        with np.errstate(divide='ignore', invalid='ignore'):
            return np.where(values > 0, np.log10(values), np.nan)
    return values


def bounds(arrays, limits=None, log=False):
    if limits is not None:
        return tuple(transform(limits, log))
    values = np.concatenate([np.asarray(a).ravel() for a in arrays]) if arrays else np.array([0., 1.])
    values = values[np.isfinite(values)]
    lo, hi = (float(values.min()), float(values.max())) if len(values) else (0., 1.)
    pad = (hi - lo) * .06 if hi > lo else max(abs(lo) * .01, .01)
    return lo - pad, hi + pad


def x_axis(panel):
    arrays = [transform(t.x, panel.xlog) for t in panel.traces]
    if panel.xlimits is not None:
        lo, hi = transform(panel.xlimits, panel.xlog)
    elif panel.xkind == 'category' and panel.tick_labels:
        lo, hi = -.5, len(panel.tick_labels) - .5
    else:
        lo, hi = bounds(arrays)
    if panel.xkind == 'category':
        positions = np.unique(np.linspace(0, max(0, len(panel.tick_labels) - 1), min(6, len(panel.tick_labels)), dtype=int))
        labels = [panel.tick_labels[i] for i in positions]
    else:
        positions = np.linspace(lo, hi, 6)
        if panel.xkind == 'datetime':
            fmt = '%m-%d %H:%M' if hi - lo < 3 * 86400 else '%Y-%m-%d'
            labels = [datetime.fromtimestamp(v, timezone.utc).strftime(fmt) for v in positions]
        else:
            labels = [number(10 ** v if panel.xlog else v) for v in positions]
    return float(lo), float(hi), np.asarray(positions), labels


def y_axis(panel, side='left'):
    arrays = [transform(t.values, panel.log)[np.isfinite(transform(t.x, panel.xlog))] for t in panel.traces if t.axis == side]
    if side == 'left':
        arrays += [transform([g.value], panel.log) for g in panel.guides if g.include_bounds]
    if not panel.log:
        arrays += [np.array([t.baseline]) for t in panel.traces if t.kind in ('bar', 'area') and t.axis == side and t.baseline is not None]
    return bounds(arrays, panel.limits if side == 'left' else None, panel.log)


def color(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)) if isinstance(value, str) and value.startswith('#') else value


def runs(x, y):
    indices = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    return [run for run in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1) if len(run)]


def spacing(trace, panel):
    finite = np.unique(transform(trace.x, panel.xlog))
    finite = finite[np.isfinite(finite)]
    return float(np.min(np.diff(finite))) if len(finite) > 1 else 1.


def _marker(draw, x, y, radius, shade, marker, scale):
    if marker == 'cross':
        draw.line((x-radius, y-radius, x+radius, y+radius), fill=shade, width=max(1, scale))
        draw.line((x-radius, y+radius, x+radius, y-radius), fill=shade, width=max(1, scale))
    elif marker == 'diamond':
        draw.polygon([(x, y-radius), (x+radius, y), (x, y+radius), (x-radius, y)], fill=shade)
    elif marker == 'square':
        draw.rectangle((x-radius, y-radius, x+radius, y+radius), fill=shade)
    else:
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=shade)


def render_image(figure: Figure, size=(1000, 600)) -> Image.Image:
    """Render an independent image; no terminal, UI or display is required."""
    return _render_image(snapshot(figure), size)


def _render_image(scene, size):
    if scene.panels and scene.panels[0].independent:
        return render_stacked_pixels(scene, size)
    scale = 2
    width, height = size = tuple(max(1, int(v)) for v in size)
    canvas = Image.new('RGB', (width*scale, height*scale), 'black')
    d = ImageDraw.Draw(canvas)
    ft = font(round(scene.font_size*scale), scene.font_path)
    has_data = any(len(t.values) for p in scene.panels for t in p.traces)
    if width < 110 or height < max(70, len(scene.panels)*(scene.font_size+20)) or not has_data:
        d.text((8, 8), 'No data' if not has_data else 'Enlarge chart', fill=FG, font=ft)
        return canvas.resize(size, Image.Resampling.LANCZOS)
    left, right = (v*scale for v in plot_bounds(width, scene.font_size))
    if any(t.axis == 'right' for p in scene.panels for t in p.traces):
        right = width*scale-left
    if right <= left:
        d.text((8, 8), 'Enlarge chart', fill=FG, font=ft)
        return canvas.resize(size, Image.Resampling.LANCZOS)
    pw = right-left
    label_h = round((scene.font_size+6)*scale)
    gap = 8*scale + (label_h*(2 if any(p.xlabel for p in scene.panels[:-1]) else 1) if not scene.sharex else 0)
    footer = label_h+6*scale
    if any(p.xlabel for p in scene.panels):
        footer += label_h
    available = height*scale-footer-gap*(len(scene.panels)-1)
    weights = [p.weight for p in scene.panels]
    top = 0
    for pi, panel in enumerate(scene.panels):
        xlo, xhi, xticks, xlabels = x_axis(panel)
        def xp(value):
            return (value-xlo)/(xhi-xlo)*pw
        ph = max(label_h+2, int(available*panel.weight/sum(weights)))
        bottom = min(height*scale-footer, top+ph)
        graph_top = top+label_h
        gh = max(2, bottom-graph_top)
        tile = Image.new('RGB', (pw, gh), 'black')
        td = ImageDraw.Draw(tile)
        lo, hi = y_axis(panel)
        rlo, rhi = y_axis(panel, 'right')
        def yp(value, side='left'):
            a, b = (rlo, rhi) if side == 'right' else (lo, hi)
            return int(round((b-value)/(b-a)*(gh-1)))
        right_traces = [t for t in panel.traces if t.axis == 'right']
        left_color = next((t.color for t in panel.traces if t.axis != 'right'), FG) if right_traces else FG
        right_color = right_traces[0].color if right_traces else FG
        count = max(2, min(6 if pi == 0 else 3, gh//max(1, label_h)))
        ticks = [30., 50., 70.] if panel.limits == (0, 100) and not panel.log else np.linspace(lo, hi, count)
        for value in ticks:
            yy = yp(value)
            td.line((0, yy, pw-1, yy), fill='#10232D', width=scale)
            label = number(10**value if panel.log else value)
            text_y = max(graph_top, min(bottom-label_h, graph_top+yy-label_h/2))
            d.text((max(0, left-d.textlength(label, font=ft)-7*scale), text_y), label, fill=left_color, font=ft)
        if right_traces:
            for value in np.linspace(rlo, rhi, count):
                text_y = max(graph_top, min(bottom-label_h, graph_top+yp(value, 'right')-label_h/2))
                d.text((right+7*scale, text_y), number(10**value if panel.log else value), fill=right_color, font=ft)
        def draw_guide(guide):
            value = transform([guide.value], panel.log)[0]
            if not np.isfinite(value):
                return
            yy = yp(value)
            for xx in range(0, pw, 10*scale if guide.marker_last else 9*scale):
                td.line((xx, yy, xx+(5 if guide.marker_last else 4)*scale, yy), fill=guide.color, width=scale)
            if guide.marker_last:
                last_x = next((t.x[-1] for t in panel.traces if len(t.x)), None)
                if last_x is not None:
                    _marker(td, int(xp(transform([last_x], panel.xlog)[0])), yy, 4*scale, guide.color, 'diamond', scale)
        for guide in panel.guides:
            if not guide.marker_last:
                draw_guide(guide)
        for trace in panel.traces:
            values, xs = transform(trace.values, panel.log), transform(trace.x, panel.xlog)
            line_width = max(1, round((trace.line_width if trace.line_width is not None else scene.line_width)*scale))
            point_size = trace.point_size if trace.point_size is not None else (3 if trace.kind == 'scatter' else scene.point_size)
            radius = max(1, round(point_size*scale))
            body = max(1, int(spacing(trace, panel)*trace.width/(xhi-xlo)*pw))
            if trace.kind in ('bar', 'candle', 'ohlc'):
                for i, value in enumerate(values):
                    if not np.isfinite(xs[i]) or not np.isfinite(value).all():
                        continue
                    xx = int(xp(xs[i]))
                    if trace.kind == 'bar':
                        baseline = transform([trace.baseline], panel.log)[0] if trace.baseline is not None else np.nan
                        yy, zero = yp(value, trace.axis), yp(baseline if np.isfinite(baseline) else (rlo if trace.axis == 'right' else lo), trace.axis)
                        td.rectangle((xx-body//2, min(yy, zero), xx+body//2, max(yy, zero)),
                                     fill=trace.colors[i] if trace.colors is not None else trace.color)
                    else:
                        o, h, l, c = value
                        shade = UP if c >= o else DOWN
                        stroke = max(1, min(line_width, int(body/trace.width*.25) if trace.width else 1))
                        td.line((xx, yp(h, trace.axis), xx, yp(l, trace.axis)), fill=shade, width=stroke)
                        if trace.kind == 'ohlc':
                            td.line((xx-body//2, yp(o, trace.axis), xx, yp(o, trace.axis)), fill=shade, width=stroke)
                            td.line((xx, yp(c, trace.axis), xx+body//2, yp(c, trace.axis)), fill=shade, width=stroke)
                        else:
                            y1, y2 = sorted((yp(o, trace.axis), yp(c, trace.axis)))
                            td.rectangle((xx-body//2, y1, xx+body//2, max(y1+scale-1, y2)), fill=shade)
                        if point_size > 0:
                            _marker(td, xx, yp(c, trace.axis), radius, shade, trace.marker, scale)
                continue
            # Each finite run is separate; a missing x or y breaks lines and area fills.
            for run in runs(xs, values):
                points = [(int(xp(xs[i])), yp(values[i], trace.axis)) for i in run]
                if trace.kind != 'scatter' and len(points) > 1:
                    if trace.kind == 'area':
                        baseline = transform([trace.baseline], panel.log)[0] if trace.baseline is not None else np.nan
                        zero = yp(baseline if np.isfinite(baseline) else (rlo if trace.axis == 'right' else lo), trace.axis)
                        td.polygon([(points[0][0], zero)]+points+[(points[-1][0], zero)], fill='#092B39')
                    td.line(points, fill=trace.color, width=line_width, joint='curve')
                if trace.kind == 'scatter' or point_size > 0 or len(points) == 1:
                    for i, (xx, yy) in zip(run, points):
                        shade = trace.colors[i] if trace.colors is not None else trace.color
                        _marker(td, xx, yy, max(scale, radius) if len(points) == 1 else radius, shade, trace.marker, scale)
        for guide in panel.guides:
            if guide.marker_last:
                draw_guide(guide)
        if scene.crosshair is not None:
            value = transform([scene.crosshair], panel.xlog)[0]
            if np.isfinite(value):
                xx = int(xp(value))
                td.line((xx, 0, xx, gh), fill=MAGENTA, width=scale)
        canvas.paste(tile, (left, graph_top))
        d.line((left, graph_top, left, bottom), fill=left_color if right_traces else DIM, width=scale)
        if right_traces:
            d.line((right, graph_top, right, bottom), fill=right_color, width=scale)
        d.line((left, bottom, right, bottom), fill=DIM, width=scale)
        xx = left
        legend = [(panel.title + (f' · {panel.ylabel}' if panel.ylabel else ''), FG)]
        legend += [(t.label, t.color) for t in panel.traces if t.label and t.label != panel.title]
        legend += [(g.label, g.color) for g in panel.guides if g.label]
        for label, shade in legend:
            tw = d.textlength(label, font=ft)
            if xx+tw > right:
                break
            d.text((xx, top), label, fill=shade, font=ft)
            xx += tw+14*scale
        # Distinct panels can have distinct x axes; shared categorical panels align exactly.
        if pi == len(scene.panels)-1 or not scene.sharex:
            for label, xx in date_labels(xlabels, [xp(v) for v in xticks], left, right, d, ft, 16*scale):
                d.text((xx, bottom+4*scale), label, fill=FG, font=ft)
        if panel.xlabel and (pi == len(scene.panels)-1 or not scene.sharex):
            d.text((left, height*scale-label_h if pi == len(scene.panels)-1 else bottom+label_h), panel.xlabel, fill=FG, font=ft)
        top = bottom+gap
    return canvas.resize(size, Image.Resampling.LANCZOS)


def render_text(figure: Figure, size=(100, 30)) -> Text:
    """Return colored terminal text with independent plotext state per call."""
    from plotext._figure import _figure_class
    # plotext exposes its independent figure through this class; keep private API use here.
    scene = snapshot(figure)
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    if width < 16 or height < 6:
        return Text('Enlarge chart', style=FG)
    plt = _figure_class()
    plt.plotsize(width, height)
    plt._set_size(width, height)
    plt.theme('clear')
    if scene.panels and scene.panels[0].independent:
        return render_stacked_text(scene, (width, height), plt)
    if not any(len(t.values) for p in scene.panels for t in p.traces):
        return Text('No data', style=FG)
    if len(scene.panels) > 1:
        plt.subplots(len(scene.panels), 1)
    axes = []
    for index, panel in enumerate(scene.panels):
        target = plt.subplot(index+1, 1) if len(scene.panels) > 1 else plt
        if index:
            target.plotsize(None, max(5, height//(len(scene.panels)+2)))
        target.title(panel.title)
        target.ticks_color(color(FG))
        if panel.xlabel:
            target.xlabel(panel.xlabel)
        if panel.ylabel:
            target.ylabel(panel.ylabel)
        xlo, xhi, positions, labels = x_axis(panel)
        target.xlim(xlo, xhi)
        for side in ('left', 'right'):
            if side == 'right' and not any(t.axis == 'right' for t in panel.traces):
                continue
            lo, hi = y_axis(panel, side)
            target.ylim(lo, hi, yside=side)
            ticks = [30., 50., 70.] if panel.limits == (0, 100) and not panel.log else list(np.linspace(lo, hi, 3 if index else 6))
            axes.append((target, ticks, [number(10**v if panel.log else v) for v in ticks], side))
        for guide in panel.guides:
            value = transform([guide.value], panel.log)[0]
            if np.isfinite(value):
                if guide.marker_last:
                    xs = next((t.x for t in panel.traces if len(t.x)), [])
                    if len(xs):
                        xs = transform(xs, panel.xlog)
                        valid = xs[np.isfinite(xs)]
                        target.scatter(valid[::3].tolist(), [value]*len(valid[::3]), color=color(guide.color), marker='─')
                        if len(valid):
                            target.scatter([valid[-1]], [value], color=color(guide.color), marker='◆')
                else:
                    target.hline(value, color(guide.color))
        for trace in panel.traces:
            xs, values = transform(trace.x, panel.xlog), transform(trace.values, panel.log)
            lw = trace.line_width if trace.line_width is not None else scene.line_width
            ps = trace.point_size if trace.point_size is not None else scene.point_size
            options = dict(color=color(trace.color), yside=trace.axis)
            if trace.kind == 'bar':
                shades = trace.colors if trace.colors is not None else [trace.color]*len(values)
                for shade in dict.fromkeys(shades):
                    valid = np.flatnonzero(np.isfinite(xs) & np.isfinite(values) & np.array([c == shade for c in shades]))
                    if len(valid):
                        base = transform([trace.baseline], panel.log)[0] if trace.baseline is not None else np.nan
                        target.bar(xs[valid].tolist(), values[valid].tolist(), width=trace.width,
                                   minimum=base if np.isfinite(base) else y_axis(panel, trace.axis)[0],
                                   color=color(shade), yside=trace.axis, marker='▌')
            elif trace.kind in ('candle', 'ohlc'):
                valid = np.isfinite(xs) & np.isfinite(values).all(axis=1)
                if trace.kind == 'candle' and len(xs) > max(1, (width-12)//4) and trace.axis == 'left':
                    if valid.any():
                        target.candlestick(xs[valid].tolist(), dict(zip(('Open', 'High', 'Low', 'Close'), values[valid].T.tolist())), colors=[color(UP), color(DOWN)])
                else:
                    for i in np.flatnonzero(valid):
                        x, (o, h, l, c) = xs[i], values[i]
                        shade = color(UP if c >= o else DOWN)
                        target.plot([x, x], [l, h], color=shade, marker='│', yside=trace.axis)
                        if trace.kind == 'ohlc':
                            target.scatter([x], [o], color=shade, marker='╴', yside=trace.axis)
                            target.scatter([x], [c], color=shade, marker='╶', yside=trace.axis)
                        else:
                            per = max(1., (width-12)/max(1, len(xs)))
                            target.bar([x], [max(o, c)], minimum=min(o, c), width=min(trace.width, max(1, int(per)-3)/per), color=shade, marker='█', yside=trace.axis)
                if ps > 0:
                    for up, shade in ((True, UP), (False, DOWN)):
                        indices = np.flatnonzero(valid & ((values[:, 3] >= values[:, 0]) == up))
                        if len(indices):
                            target.scatter(xs[indices].tolist(), values[indices, 3].tolist(), color=color(shade), yside=trace.axis, marker='●' if ps >= 2 else '•')
            else:
                marker = {'circle':'●', 'cross':'×', 'square':'■', 'diamond':'◆'}[trace.marker]
                labelled = False
                for run in runs(xs, values):
                    label = '' if labelled else trace.label
                    if trace.kind == 'scatter':
                        if trace.colors is None:
                            target.scatter(xs[run].tolist(), values[run].tolist(), marker=marker, label=label, **options)
                        else:
                            for j, i in enumerate(run):
                                target.scatter([xs[i]], [values[i]], color=color(trace.colors[i]), yside=trace.axis, marker=marker, label=label if j == 0 else '')
                    else:
                        target.plot(xs[run].tolist(), values[run].tolist(), marker='█' if lw >= 2 else 'braille',
                                    fillx=trace.kind == 'area', label=label, **options)
                        if ps > 0 or len(run) == 1:
                            target.scatter(xs[run].tolist(), values[run].tolist(), marker=marker if ps >= 2 else '•', **options)
                    labelled = True
        if scene.crosshair is not None:
            value = transform([scene.crosshair], panel.xlog)[0]
            if np.isfinite(value):
                target.vline(value, color(MAGENTA))
        # plotext.bar replaces ticks. Install the requested axis after every primitive.
        target.xlim(xlo, xhi)
        target.xticks(positions.tolist(), labels if index == len(scene.panels)-1 or not scene.sharex else ['']*len(labels))
    widths = {side:max((len(label) for _, _, labels, axis in axes if axis == side for label in labels), default=1) for side in ('left', 'right')}
    for target, positions, labels, side in axes:
        target.yticks(positions, [label.rjust(widths[side]) for label in labels], yside=side)
    return color_dual_axes(Text.from_ansi(plt.build()), scene)
