"""Offline rendering and financial-adapter regression tests. Run with unittest discovery."""
import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from rich.text import Text
from textual.app import App, ComposeResult
from textual_plotext import PlotextPlot

from terminal_plot import Figure, Axes, Series, snapshot, subplots, render_image, render_text
from terminal_plot.render import x_axis, y_axis, independent_scales
from freeterm.pixelcharts import ChartSurface, Scene, Panel, Trace, to_figure, _render_frame


class PlotModelTests(unittest.TestCase):
    def test_supplied_clusters_and_centers(self):
        fig, ax = subplots(title='Clusters')
        ax.scatter([0, 1, 90, 100], [1, 2, 3, 4], groups=['A', 'A', 'B', 'B'])
        ax.scatter([.5, 95], [1.5, 3.5], marker='cross', label='Centers', point_size=6)
        prepared = snapshot(fig)
        self.assertEqual([t.label for t in prepared.panels[0].traces], ['Group A', 'Group B', 'Centers'])
        self.assertNotEqual(ax.traces[0].color, ax.traces[1].color)
        self.assertEqual(render_image(fig, (600, 300)).size, (600, 300))
        text = render_text(fig, (100, 25)).plain
        for label in ('Clusters', 'Group A', 'Group B', 'Centers'):
            self.assertIn(label, text)

    def test_numeric_scatter_preserves_distances_without_joining_points(self):
        fig, ax = subplots()
        ax.xlimits = (0, 100)
        ax.limits = (0, 10)
        ax.scatter([10, 11, 90], [5, 5, 5], color='#FF0000', point_size=3)
        image = np.array(render_image(fig, (1000, 400)))
        red = (image[:, :, 0] > 220) & (image[:, :, 1] < 30) & (image[:, :, 2] < 30)
        columns = np.flatnonzero(red.any(axis=0))
        self.assertLess(columns[0], 200)
        self.assertGreater(columns[-1], 850)
        self.assertFalse(red[:, 300:800].any(), 'scatter must not join separated points')

    def test_snapshot_detaches_arrays_and_labels(self):
        x, y = np.array([1., 2.]), np.array([10., 20.])
        fig, ax = subplots()
        ax.plot(x, y, label='Original')
        saved = snapshot(fig)
        x[0], y[0], ax.traces[0].label = 900, 900, 'Changed'
        np.testing.assert_equal(saved.panels[0].traces[0].x, [1, 2])
        np.testing.assert_equal(saved.panels[0].traces[0].values, [10, 20])
        self.assertEqual(saved.panels[0].traces[0].label, 'Original')
        self.assertFalse(saved.panels[0].traces[0].values.flags.writeable)

    def test_utc_datetimes_and_categories(self):
        first = datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=-5)))
        fig, ax = subplots()
        ax.plot([first, first+timedelta(days=3)], [2, 3])
        saved = snapshot(fig)
        self.assertEqual(saved.panels[0].xkind, 'datetime')
        self.assertEqual(saved.panels[0].traces[0].x[0], datetime(2026, 1, 1, 17, tzinfo=timezone.utc).timestamp())
        self.assertAlmostEqual(np.diff(saved.panels[0].traces[0].x)[0], 3*86400)
        self.assertIn('2026-', render_text(fig).plain)
        normalized = Figure([Axes(xkind='datetime', traces=[Series('UTC', [1, 2], x=['2026-01-01T12:00:00-05:00', '2026-01-02T17:00:00Z'])])])
        prepared = snapshot(normalized)
        self.assertEqual(prepared.panels[0].traces[0].x[0], first.timestamp())
        self.assertAlmostEqual(np.diff(prepared.panels[0].traces[0].x)[0], 86400)
        fig, ax = subplots()
        ax.bar(['small', 'large', 'medium'], [2, 5, 3])
        ax.plot(['medium', 'small'], [4, 1])
        saved = snapshot(fig)
        self.assertEqual(saved.panels[0].tick_labels, ('small', 'large', 'medium'))
        np.testing.assert_equal(saved.panels[0].traces[1].x, [2, 0])
        self.assertIn('medium', render_text(saved).plain)
        missing = Figure([Axes(traces=[Series('Gaps', [1, 2, 3], x=['A', None, 'C'])])])
        prepared = snapshot(missing)
        self.assertEqual(prepared.panels[0].tick_labels, ('A', 'C'))
        self.assertTrue(np.isnan(prepared.panels[0].traces[0].x[1]))
        self.assertIsInstance(render_text(missing), Text)

    def test_missing_values_break_lines_and_do_not_mutate(self):
        fig, ax = subplots()
        ax.xlimits, ax.limits = (0, 4), (0, 10)
        values = [2, 2, np.nan, 8, 8]
        ax.plot(range(5), values, color='#FF0000')
        image = np.array(render_image(fig, (900, 400)))
        red = (image[:, :, 0] > 80) & (image[:, :, 1] < 50) & (image[:, :, 2] < 50)
        self.assertTrue(red[:, 100:250].any())
        self.assertTrue(red[:, 750:].any())
        self.assertFalse(red[:, 400:500].any(), 'a NaN gap must not be connected')
        self.assertTrue(np.isnan(values[2]))
        self.assertIsInstance(render_text(fig), Text)

    def test_log_domains_limits_guides_and_right_axis(self):
        fig, ax = subplots()
        ax.log = ax.xlog = True
        ax.limits, ax.xlimits = (1, 1000), (1, 100)
        ax.plot([1, 10, 100], [1, -2, 1000])
        ax.plot([1, 10, 100], [100, 1000, 10000], axis='right')
        ax.hline(10)
        saved = snapshot(fig)
        np.testing.assert_allclose(y_axis(saved.panels[0]), [0, 3])
        np.testing.assert_allclose(x_axis(saved.panels[0])[:2], [0, 2])
        self.assertIn('1000', render_text(fig).plain)
        self.assertEqual(render_image(fig).size, (1000, 600))
        # Right-only data still has a valid left axis and renders safely.
        self.assertIsInstance(render_text(Figure([Axes(traces=[Series('Right', [1, 2], axis='right')])])), Text)

    def test_shared_x_ranges_and_separate_plotext_state(self):
        fig, axes = subplots(2)
        axes[0].plot([0, 5], [1, 2])
        axes[1].bar([10, 50], [2, 3])
        fig.sharex = True
        saved = snapshot(fig)
        self.assertEqual(saved.panels[0].xlimits, saved.panels[1].xlimits)
        self.assertEqual(saved.panels[0].xlimits, (0, 50))
        first = render_text(fig).plain
        other, ax = subplots(title='Unrelated')
        ax.scatter([100, 200], [300, 400])
        render_text(other)
        self.assertEqual(render_text(fig).plain, first)

    def test_validation_rejects_ambiguous_or_malformed_instructions(self):
        bad = [
            Figure([Axes(traces=[Series('', [1, 2], x=[1])])]),
            Figure([Axes(traces=[Series('', [[1, 2]], kind='candle')])]),
            Figure([Axes(traces=[Series('', [1], kind='unknown')])]),
            Figure([Axes(traces=[Series('', [1], x=[datetime(2026, 1, 1)])])]),
            Figure([Axes(traces=[Series('', [1, 2], colors=['red'])])]),
            Figure([Axes(limits=(2, 1))]),
            Figure([Axes(log=True, limits=(0, 1))]),
            Figure([Axes(xlog=True, traces=[Series('', [1], x=['A'])])]),
            Figure([Axes(traces=[Series('', [1], point_size=-1)])]),
            Figure([Axes(independent=True, traces=[Series('', [1], kind='scatter')])]),
        ]
        for figure in bad:
            with self.subTest(figure=figure), self.assertRaises((ValueError, TypeError)):
                snapshot(figure)
        fig, ax = subplots()
        with self.assertRaises(ValueError):
            ax.scatter([1, 2], [3, 4], groups=['A'])

    def test_empty_constant_nonfinite_and_small_canvases(self):
        for values in ([], [np.nan, np.inf], [0, 0], [2]):
            fig, ax = subplots()
            ax.plot(range(len(values)), values)
            for size in ((1, 1), (80, 30), (400, 200)):
                self.assertEqual(render_image(fig, size).size, size)
            self.assertIsInstance(render_text(fig, (80, 20)), Text)
        self.assertIn('Enlarge', render_text(Figure(), (2, 2)).plain)


class FinancialCompatibilityTests(unittest.TestCase):
    def scene(self, ctype='candle'):
        x = np.arange(30.)
        ohlc = np.array([100+x, 103+x, 98+x, 101+x]).T
        return Scene([
            Panel('Price', [Trace('SMA', 100+x, '#5FD7FF')]),
            Panel('Volume', bars=x*100, bar_colors=['#00FF87']*30, limits=(0, 3000)),
            Panel('RSI', [Trace('RSI', x+30, '#FFD700')], limits=(0, 100), guides=[(30, '#00FF87'), (70, '#FF4D6D')]),
            Panel('MACD', [Trace('MACD', x/10, '#5FD7FF'), Trace('Signal', x/12, '#FFD700')], bars=x/60, guides=[(0, '#3A6B80')]),
        ], [f'2026-01-{i+1:02}' for i in range(30)], ohlc if ctype in ('candle', 'ohlc') else None,
            ctype, 12, 132, line_width=2, point_size=2, candle_width=.8)

    def test_all_existing_financial_primitives_and_no_candle_aggregation(self):
        for kind in ('candle', 'ohlc', 'line', 'area'):
            with self.subTest(kind=kind):
                scene = self.scene(kind)
                fig = snapshot(to_figure(scene))
                self.assertTrue(fig.sharex)
                self.assertEqual(len(fig.panels), 4)
                self.assertEqual(fig.crosshair, 12)
                self.assertEqual([p.weight for p in fig.panels], [3, 1, 1, 1])
                if scene.candles is not None:
                    np.testing.assert_equal(fig.panels[0].traces[0].values, scene.candles)
                    self.assertEqual(len(fig.panels[0].traces[0].values), 30)
                self.assertTrue(fig.panels[0].guides[-1].marker_last)
                self.assertEqual(render_image(fig, (1200, 800)).size, (1200, 800))
                text = render_text(fig, (150, 50)).plain
                for title in ('Price', 'Volume', 'RSI', 'MACD'):
                    self.assertIn(title, text)
                self.assertNotIn('unavailable', text)

    def test_eight_native_comparison_scales_and_gaps(self):
        traces = [Trace(f'[{i+1}] Source {i}', np.array([1., np.nan, 3.])*(i+1)**5, '#5FD7FF') for i in range(8)]
        scene = Scene([Panel('Compare STACKED', traces, independent=True)], ['A', 'B', 'C'])
        fig = snapshot(to_figure(scene))
        scales = independent_scales(fig.panels[0])
        self.assertEqual(len(scales), 8)
        for _, values, _ in scales:
            self.assertTrue(np.isnan(values[1]))
        text = render_text(fig, (180, 35)).plain
        for i in range(1, 9):
            self.assertIn(f'[{i}]', text)
        self.assertIn('Enlarge', render_text(fig, (40, 12)).plain)
        self.assertEqual(render_image(fig, (1400, 700)).size, (1400, 700))

    def test_dense_candles_emit_every_native_close_marker(self):
        count = 1000
        candles = np.tile([10., 12., 8., 11.], (count, 1))
        scene = Scene([Panel('Dense')], list(map(str, range(count))), candles, point_size=1)
        with patch('terminal_plot.render._marker') as marker:
            render_image(to_figure(scene), (300, 200))
        self.assertEqual(marker.call_count, count)

    def test_earnings_treasury_and_frame_encoding(self):
        scenes = [Scene([Panel('EPS surprise %', bars=np.array([-2., 4.]), bar_colors=['#FF4D6D', '#00FF87'], guides=[(0, '#3A6B80')])], ['Q1', 'Q2']),
                  Scene([Panel('Treasury yield %', [Trace('Now', np.array([4., 3., 4.]), '#5FD7FF'), Trace('1y', np.array([5., 4., 3.]), '#FFD700')])], ['1mo', '10y', '30y'])]
        for scene in scenes:
            fig = to_figure(scene)
            self.assertIn(scene.panels[0].title, render_text(fig).plain)
            image, encoded = _render_frame(fig, (500, 300), 'sixel')
            self.assertEqual(image.size, (500, 300))
            self.assertTrue(encoded)


class SurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_submission_resize_fallback_and_local_render_errors(self):
        class PlotApp(App):
            def __init__(self):
                super().__init__()
                self.cfg = type('Config', (), {'get': lambda _, section, key, default: default})()
                self.warnings = []
                self.hub = type('Hub', (), {'warn': self.warnings.append})()
            def compose(self) -> ComposeResult:
                yield ChartSurface(renderer='text')
        app = PlotApp()
        async with app.run_test(size=(100, 30)) as pilot:
            surface = app.query_one(ChartSurface)
            fig, ax = subplots(title='Cluster surface')
            values = [1, 5, 2]
            ax.scatter([0, 20, 90], values)
            surface.submit(fig)
            values[0] = 999
            await pilot.pause()
            self.assertEqual(surface.scene.panels[0].traces[0].values[0], 1)
            self.assertIn('Cluster surface', surface.query_one(PlotextPlot).render().plain)
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            self.assertIn('Cluster surface', surface.query_one(PlotextPlot).render().plain)
            with patch('freeterm.pixelcharts.render_text', side_effect=RuntimeError('injected')):
                self.assertIn('unavailable', surface.query_one(PlotextPlot).render().plain)
            self.assertTrue(app.warnings)
            # Failure wrapping is scoped to this surface, never a global PlotextPlot patch.
            self.assertEqual(PlotextPlot.render.__module__, 'textual_plotext.plotext_plot')
            surface.use_text()
            await pilot.pause()
            self.assertEqual(surface.backend, 'text')

    async def test_raster_worker_discards_obsolete_frames(self):
        class Harness:
            _rasterize = ChartSurface._rasterize
            def __init__(self):
                self._pending = (1, Figure(), (120, 100))
                self._generation = 1
                self.pixels = self.is_mounted = True
                self._rendering = True
                self.backend = 'tgp'
                self.last_image = None
                self.widget = type('ImageWidget', (), {'image': None})()
                self.errors = []
            def query_one(self, selector):
                return self.widget
            def post_message(self, message):
                self.errors.append(message)
        harness = Harness()
        calls = []
        def renderer(scene, size, backend):
            calls.append(size)
            if len(calls) == 1:
                harness._generation = 2
                harness._pending = (2, Figure(), (180, 120))
            return Image.new('RGB', size), None
        with patch('freeterm.pixelcharts._render_frame', side_effect=renderer):
            await harness._rasterize()
        self.assertEqual(calls, [(120, 100), (180, 120)])
        self.assertEqual(harness.widget.image.size, (180, 120))
        self.assertFalse(harness.errors)
        self.assertFalse(harness._rendering)


if __name__ == '__main__':
    unittest.main()
