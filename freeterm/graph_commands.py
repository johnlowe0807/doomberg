"""Commands for a single graph, shared by its popup and the main command bar."""
from __future__ import annotations

import math
import re
import shlex

from textual import on
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

VERBS = ("IND", "CMP", "MODE", "IEX", "TIME", "SIZE", "TF", "GOTO", "DES", "CHAIN")
INDICATORS = {"SMA": ("s", "sma"), "EMA": ("e", "ema"), "BB": ("b", "bb"),
              "VWAP": ("w", None), "RSI": ("r", "rsi"), "MACD": ("m", "macd"),
              "VOLUME": ("v", None), "IEX": ("i", None)}
INDICATOR_ALIASES = {"VOL": "VOLUME", "MA": "SMA", "BOLL": "BB", "BOLLINGER": "BB",
                     "BOLLINGERBANDS": "BB", "BOLLINGER_BANDS": "BB"}
HELP = (
    "IND indicator... [ON|OFF] [_p=ordered parameter groups]\n"
    "Indicators: SMA EMA BB (BOLLINGER) VWAP RSI MACD VOLUME (VOL) IEX · IND CLEAR\n"
    "IND VWAP RSI MACD   IND RSI MACD _p=14;12,26,9\n"
    "Repeat: changed/default parameters update; unchanged toggles off. Skip VWAP/VOLUME/IEX in _p= groups.\n"
    "IND BB _p=20,2   IND RSI _p=14   IND MACD _p=12,26,9\n"
    "IND VOLUME RSI ON · IND IEX ON requires validated keys (global). CLEAR also disables volume/IEX.\n"
    "Inside IND, IEX is an indicator; .IEX starts a separate chained command.\n"
    "CMP AAPL AMZN   CMP OFF   IEX [ON|OFF|TOGGLE]\n"
    "TIME 2Y [1d]   TIME 18M   TIME 90D\n"
    "SIZE 2d   SIZE 2M (months; m = minutes)   SIZE 0.5 years   SIZE RESET\n"
    "TIME sets the full window; SIZE sets each candle/data point interval. RESET uses the window default.\n"
    "Intervals: 1 minute–366 days or 1–12 calendar months; source history limits also apply.\n"
    "Chain in order: TIME 2Y SIZE 2d IND VWAP RSI. Errors stop the chain after earlier changes.\n"
    "Main line: AAPL .TIME 1Y .SIZE 2wk opens the view first; .IND BB edits the active graph.\n"
    "GOTO YYYYMMDD [HHMM] · GOTO LEFT 50 · GOTO +25 · Ctrl+arrows jump halfway to the visible edge.\n"
    "HELP or ? lists commands. The popup accepts bare or .prefixed commands."
)


def available_verbs(chart):
    if getattr(chart, 'view_commands_enabled', False):
        from .option_commands import VERBS as OPTIONS_VERBS, ALIASES
        return (*OPTIONS_VERBS, *ALIASES)
    if not chart.graph_commands_enabled:
        return ()
    if chart.mode == "series":
        return ("CMP", "TIME", "TF", "GOTO", "DES")
    if chart.mode == "comp":
        return ("CMP", "MODE", "TIME", "TF", "GOTO") + (("SIZE",) if chart.uses_price_intervals else ())
    from .live import is_us_equity
    from .options import contract
    return tuple(verb for verb in VERBS if verb != 'MODE' and (verb != 'CHAIN' or contract(chart.sec)) and (verb != "IEX" or is_us_equity(chart.sec)))


def available_indicators(chart):
    if getattr(chart, 'mode', None) != "px":
        return ()
    from .live import is_us_equity
    return tuple(name for name in INDICATORS if
                 (name != "IEX" or is_us_equity(chart.sec)) and
                 (name not in ("VWAP", "VOLUME") or not chart.sec.startswith("^")))


def graph_help(chart):
    if getattr(chart, 'view_commands_enabled', False):
        from .option_commands import help_text
        return help_text(chart)
    kind = "FRED native series" if chart.mode == "series" else chart.compare_mode + " comparison" if chart.mode == "comp" else "security price"
    lines = [f"Available commands · {kind}", "TIME 2Y · TIME 18M · TIME 90D — set a rolling window"]
    if "SIZE" in available_verbs(chart):
        lines += ["SIZE 2d · SIZE 2M (months; m = minutes) · SIZE 0.5 years · SIZE RESET",
                  "SIZE sets each candle/data point interval; RESET restores the window default.",
                  "Intervals: 1 minute–366 days or 1–12 calendar months; source limits apply."]
    else:
        lines += ["Source series keep native observations; timeframe controls preserve their frequency."]
    lines += ["TF YYYYMMDD [HHMM] YYYYMMDD [HHMM] · TF YYYYMMDD [HHMM] length",
              "TF 20240101 20240630 · TF 20240101 3M · TF 20261023 0930 4h",
              "ISO dates/timestamps also work: TF 2024-01-01 2024-06-30.",
              "Times: New York. Date-only ends include that day; timestamp ends are exclusive.",
              "Lengths: min/m minutes, h hours, D days, W weeks, M/mo months, Y years."]
    lines += ["GOTO 20261023 1030 · GOTO 2026-10-23T10:30 · GOTO LEFT 50 · GOTO RIGHT 25",
              "GOTO -50 / +25 shifts by loaded candles; default selection is the visible rightmost point.",
              "Datetimes use New York time unless an offset/Z is supplied; dates select the containing candle.",
              "Gaps select the previous observation; out-of-range dates fail. Off-screen selections pan into view.",
              "←/→ steps one point; Ctrl+←/→ jumps halfway toward the visible edge (rounded toward the edge)."]
    if chart.mode == "px":
        aliases = "; ".join(f"{alias}={name}" for alias, name in INDICATOR_ALIASES.items() if name in available_indicators(chart))
        lines += ["IND " + " ".join(available_indicators(chart)) + " [ON|OFF] · IND CLEAR",
                  "Aliases: " + aliases,
                  "IND RSI MACD _p=14;12,26,9 · IND BB _p=20,2 · SMA/EMA accept period lists.",
                  "Parameter groups follow indicator order; skip VWAP/VOLUME/IEX.",
                  "Changed/default parameters update; unchanged active indicators toggle off.",
                  "CLEAR disables technical indicators, volume and global IEX."]
    if "CMP" in available_verbs(chart):
        lines += ["CMP AAPL AMZN · CMP AAPL _fred=DGS10 _mode=REAL · CMP OFF",
                  "Sources: securities (all asset types/options), FRED:series, ERN:ticker (EPS surprise %).",
                  "_fred=DGS10,UNRATE and _ern=AAPL add source series. Up to 8 series.",
                  "Modes: NORMALIZED (% from first common observation), REAL (native values, one scale),",
                  "STACKED / STACK (1–8 series, independent color-matched scales all on the left).",
                  "No backward filling or extension beyond the last source observation."]
    if 'MODE' in available_verbs(chart):
        lines += ['MODE NORMALIZED|REAL|STACKED changes comparison scaling.']
    if 'DES' in available_verbs(chart):
        lines += ['DES opens the active series/security description.']
    if 'CHAIN' in available_verbs(chart):
        lines += ['CHAIN returns to the options monitor at this contract’s expiry, side and strike.']
    if "IEX" in available_verbs(chart):
        lines += ["IEX ON|OFF|TOGGLE (global; validated Alpaca keys required).",
                  "Inside IND, IEX is an indicator; .IEX starts a separate chained command."]
    lines += ["Commands chain left to right; the first error stops the remaining commands.",
              "Dot-only commands edit the active graph; /FRED DGS10 .TIME 2Y opens a series first.",
              "HELP or ? lists commands for this graph type. Esc/Enter/q returns to the prompt."]
    return "\n".join(lines)


def parse_command(line):
    try:
        words = shlex.split(line)
    except ValueError as error:
        raise ValueError(str(error)) from error
    if not words:
        raise ValueError("Enter a graph command; use HELP or ? for the command list")
    verb = words.pop(0).lstrip("/.").upper()
    if verb == "?":
        verb = "HELP"
    if verb not in VERBS and verb not in ("HELP", "COMP"):
        raise ValueError("Unknown graph command; use HELP or ? for the command list")
    args, options = [], {}
    for word in words:
        match = re.fullmatch(r"_([A-Za-z]+)=(.*)", word)
        if not match:
            args.append(word if verb in ("SIZE", "TF") or (verb == "TIME" and args) else word.upper())
            continue
        key, value = match.groups()
        key = key.lower()
        if key in options or not value:
            raise ValueError(f"Supply one nonempty _{key}= value")
        options[key] = value
    return ("CMP" if verb == "COMP" else verb), args, options


def command_starts(words):
    """IEX inside IND is an indicator; .IEX or /IEX explicitly starts a separate command."""
    starts = [0] if words else []
    current = words[0].lstrip("/.").upper() if words else ""
    for index, word in enumerate(words[1:], 1):
        verb = word.lstrip("/.").upper()
        if verb in (*VERBS, "COMP", "HELP", "?") or re.fullmatch(r"\.[A-Za-z?]+", word):
            if current == "IND" and verb == "IEX" and not word.startswith(("/", ".")):
                continue
            starts.append(index)
            current = verb
    return starts


def execute(chart, line):
    """Run graph commands in input order; stop at the first failure, retaining earlier changes."""
    if getattr(chart, 'view_commands_enabled', False):
        from .option_commands import execute as execute_options
        return execute_options(chart, line)
    words = shlex.split(line)
    if not words:
        raise ValueError("Enter a graph command; use HELP or ? for the command list")
    starts = command_starts(words)
    starts.append(len(words))
    if any(words[start].lstrip("/.").upper() in ("HELP", "?", 'DES', 'CHAIN') for start in starts[:-2]):
        raise ValueError("Put HELP, DES or CHAIN last in a command chain")
    for index, (start, end) in enumerate(zip(starts, starts[1:])):
        try:
            result = execute_one(chart, shlex.join(words[start:end]))
        except ValueError as error:
            if len(starts) > 2:
                raise ValueError(f"Command {index + 1} ({words[start]}) failed: {error}. Earlier commands remain applied; later commands were skipped") from error
            raise
        if result == "HELP":
            return result


def execute_one(chart, line):
    from .charts import default_interval
    from .data import candle_interval, range_days, slice_range, parse_timeframe
    from .live import set_iex
    verb, args, options = parse_command(line)
    if verb == "HELP":
        if args or options:
            raise ValueError("Use HELP or ? without arguments to list graph commands")
        chart.app.push_screen(GraphHelpScreen(chart))
        return "HELP"
    if verb not in available_verbs(chart):
        raise ValueError(f"{verb} is unavailable for this graph type; HELP lists available commands")
    if options and verb != 'CMP' and (verb != "IND" or any(key != "p" for key in options)):
        raise ValueError("Unknown argument; IND accepts _p=, CMP accepts _fred=/_ern=/_mode=")
    if verb == "IND":
        if chart.mode != "px":
            raise ValueError("Indicators require a security price graph")
        if args == ["CLEAR"] and not options:
            chart.ind.clear()
            chart.vol = False
            set_iex(chart.hub, "OFF")
            chart.app.tick()
        else:
            action = args[-1] if args and args[-1] in ("ON", "OFF") else None
            names = [INDICATOR_ALIASES.get(name, name) for name in (args[:-1] if action else args)]
            if not names or any(name not in INDICATORS for name in names) or len(set(names)) != len(names):
                raise ValueError("Use IND VWAP RSI MACD [ON|OFF] [_p=14;12,26,9], or IND CLEAR; list each indicator once")
            if any(name not in available_indicators(chart) for name in names):
                raise ValueError("Available indicators for this graph: " + " ".join(available_indicators(chart)))
            parameter_names = [INDICATORS[name][1] for name in names if INDICATORS[name][1]]
            supplied = {}
            if "p" in options:
                groups = options["p"].split(";")
                if len(groups) != len(parameter_names) or not parameter_names:
                    raise ValueError("Supply one semicolon-separated parameter group per parameterized indicator, in order; skip VWAP/VOLUME/IEX")
                for parameter, group in zip(parameter_names, groups):
                    try:
                        values = [float(value) for value in group.split(",")]
                    except ValueError as error:
                        raise ValueError("Indicator parameters must be positive numbers separated by commas") from error
                    if not values or len(values) > 8 or not all(math.isfinite(value) and 0 < value <= 10000 for value in values):
                        raise ValueError("Use 1–8 finite positive parameters, each at most 10000")
                    if parameter == "bb":
                        valid = len(values) == 2 and values[0].is_integer()
                    else:
                        valid = all(value.is_integer() for value in values) and (
                            len(values) == 1 if parameter == "rsi" else len(values) == 3 and values[0] < values[1] if parameter == "macd" else True)
                    if not valid:
                        raise ValueError("SMA/EMA: integer periods; BB: period,deviations; RSI: period; MACD: fast,slow,signal (fast < slow)")
                    supplied[parameter] = ",".join(f"{value:g}" for value in values)
            # Compute and validate every change before mutating the batch, including IEX auth.
            changes = []
            for name in names:
                key, parameter = INDICATORS[name]
                desired = supplied.get(parameter, chart.default_parameters.get(parameter)) if parameter else None
                changed = parameter is not None and [float(v) for v in chart.parameters[parameter].split(",")] != [float(v) for v in desired.split(",")]
                active = chart.vol if key == "v" else chart.hub.cfg.get("iex", "enabled", False) if key == "i" else key in chart.ind
                enabled = action == "ON" or (action != "OFF" and (not active or changed))
                if key == "i" and enabled and not chart.hub.alpaca_ok:
                    raise ValueError(f"IEX overlay unavailable: {chart.hub.alpaca_reason}. Set valid ALPACA_API_KEY/ALPACA_API_SECRET and restart.")
                changes.append((key, parameter, desired, enabled))
            for key, parameter, desired, enabled in changes:
                if parameter:
                    chart.parameters[parameter] = desired
                if key == "v":
                    chart.vol = enabled
                elif key == "i":
                    set_iex(chart.hub, "ON" if enabled else "OFF")
                    chart.app.tick()
                else:
                    chart.ind.add(key) if enabled else chart.ind.discard(key)
        chart.draw()
    elif verb == "IEX":
        if len(args) > 1 or (args and args[0] not in ("ON", "OFF", "TOGGLE")):
            raise ValueError("Use IEX ON, IEX OFF or IEX TOGGLE")
        set_iex(chart.hub, args[0] if args else "TOGGLE")
        chart.draw()
        chart.app.tick()
    elif verb == "CMP":
        from .comparison import parse, securities
        if args == ["OFF"]:
            if options:
                raise ValueError('CMP OFF takes no arguments')
            if chart.mode != 'comp':
                return
            if getattr(chart, 'restore_ref', '').startswith('ERN:'):
                chart.app.run_command('/ERN ' + chart.restore_ref[4:])
                return
            chart.mode, chart.sec, series, chart.ctype, chart.vol = chart.restore_state
            chart.series = list(series)
            chart.compare = []
        else:
            defaults = chart.compare_refs if chart.mode == 'comp' else ['FRED:' + sid for sid in chart.series] if chart.mode == 'series' else [chart.sec]
            refs, selected_mode = parse(args, chart.app.aliases, defaults,
                                         chart.compare_mode if chart.mode == 'comp' else 'NORMALIZED', options)
            if chart.mode != 'comp':
                chart.restore_state = (chart.mode, chart.sec, list(chart.series), chart.ctype, chart.vol)
                chart.restore_ref = ''
            chart.compare_refs, chart.compare_mode = refs, selected_mode
            prices = securities(refs)
            chart.sec, chart.compare = (prices[0] if prices else None), prices[1:]
            chart.mode, chart.ctype, chart.vol = "comp", "line", False
        chart.df = None
        chart.raw_comparison = None
        chart.pending_gotos.clear()
        chart.zoom, chart.cx, chart.fit_pending = (0., 1.), None, True
        chart.render_rangebar()
        chart.retitle()
        chart.reload()
    elif verb == 'MODE':
        from .comparison import mode, validate_mode, normalized
        if len(args) != 1:
            raise ValueError('Use MODE NORMALIZED, MODE REAL or MODE STACKED')
        selected_mode = mode(args[0]); validate_mode(selected_mode, chart.compare_refs)
        raw = getattr(chart, 'raw_comparison', None)
        frame = normalized(raw) if selected_mode == 'NORMALIZED' and raw is not None else raw
        if selected_mode == 'STACKED' and raw is not None and len(raw.columns) != len(chart.compare_refs):
            raise ValueError('STACKED needs data for every selected series')
        chart.compare_mode = selected_mode
        chart.pending_gotos.clear()
        chart.zoom, chart.cx, chart.fit_pending = (0., 1.), None, True
        if frame is not None:
            chart.df = frame.copy(); chart.fit_view(); chart.draw()
        else:
            chart.reload()
        chart.retitle()
    elif verb == 'DES':
        if args:
            raise ValueError('DES takes no arguments; describes the active security or first FRED series')
        chart.app.run_command('/DES _fred=' + chart.series[0] if chart.mode == 'series' else '/DES ' + chart.sec)
    elif verb == 'CHAIN':
        if args:
            raise ValueError('CHAIN takes no arguments')
        chart.app.run_command('/OMON ' + chart.sec)
    elif verb == "GOTO":
        from .chart_navigation import parse_goto
        chart.goto(parse_goto(args))
    elif verb in ("TIME", "TF"):
        if verb == "TF":
            args = [parse_timeframe(args)]
        if not 1 <= len(args) <= 2:
            raise ValueError("Use TIME 2Y [1d], TIME 18M or TIME 90D")
        if not chart.uses_price_intervals and len(args) > 1:
            raise ValueError("Source series keep native observations; use TIME 2Y without an interval")
        rng = args[0]
        range_days(rng)
        # Validate the calendar offset before changing any chart state.
        import pandas as pd
        slice_range(pd.DataFrame(index=pd.DatetimeIndex([pd.Timestamp.now()])), rng)
        interval = candle_interval(args[1] if len(args) == 2 else default_interval(rng)).value
        if verb == "TF" and chart.uses_price_intervals and chart.hub.base_for(rng, interval) is None:
            from .data import IVS
            interval = next((iv for iv in IVS if chart.hub.base_for(rng, iv) is not None), interval)
        if chart.uses_price_intervals and chart.hub.base_for(rng, interval) is None:
            raise ValueError(f"{interval} history is unavailable for {rng}; use a coarser interval")
        chart.rng, chart.iv = rng, interval
        chart.df = None
        chart.raw_comparison = None
        chart.pending_gotos.clear()
        chart.zoom, chart.cx, chart.fit_pending = (0., 1.), None, True
        chart.render_rangebar()
        chart.retitle()
        chart.reload()
    elif verb == "SIZE":
        if not 1 <= len(args) <= 2:
            raise ValueError("Use SIZE 2d, SIZE 2 weeks, SIZE 0.5 years, or SIZE RESET")
        if not chart.uses_price_intervals:
            raise ValueError("Source series keep native observations; use TIME to change the window")
        interval = candle_interval(default_interval(chart.rng) if [arg.upper() for arg in args] == ["RESET"] else " ".join(args)).value
        if chart.hub.base_for(chart.rng, interval) is None:
            raise ValueError(f"{interval} history is unavailable for {chart.rng}; choose another SIZE or TIME window")
        chart.iv = interval
        chart.df = None
        chart.raw_comparison = None
        chart.pending_gotos.clear()
        chart.zoom, chart.cx, chart.fit_pending = (0., 1.), None, True
        chart.render_rangebar()
        chart.retitle()
        chart.reload()


class GraphHelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,enter,q,question_mark", "dismiss", show=False)]
    DEFAULT_CSS = """
    GraphHelpScreen { align: center middle; background: #000000 70%; }
    GraphHelpScreen > VerticalScroll { width: 100; max-width: 100%; height: auto; max-height: 100%;
        border: solid #FF5FD2; background: #000000; padding: 1 2; }
    GraphHelpScreen Static { height: auto; color: #5FD7FF; }
    """

    def __init__(self, chart):
        super().__init__()
        self.help_text = graph_help(chart)
        self.label = 'Options commands' if getattr(chart, 'view_commands_enabled', False) else 'Graph commands'

    def compose(self):
        with VerticalScroll():
            yield Static(self.label, markup=False)
            yield Static(self.help_text, markup=False)
            yield Static("Up/Down scroll · Esc/Enter/q return to the command line", markup=False)

    def on_mount(self):
        self.query_one(VerticalScroll).focus()


class GraphCommandScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", show=False)]
    DEFAULT_CSS = """
    GraphCommandScreen { align: center middle; background: #000000 70%; }
    GraphCommandScreen > Vertical { width: 80; max-width: 100%; height: auto; max-height: 100%;
        border: solid #FF5FD2; background: #000000; padding: 1 2; }
    GraphCommandScreen Static { height: auto; color: #5FD7FF; }
    GraphCommandScreen #graph-error { color: #FFD700; }
    GraphCommandScreen #graph-command, GraphCommandScreen #graph-command:focus {
        height: 1; border: none; padding: 0; color: #FF5FD2; background: #1a0016; }
    """

    def __init__(self, chart):
        super().__init__()
        self.chart = chart
        self.history_index = len(chart.command_history)
        self.completion = None

    def compose(self):
        with Vertical():
            yield Static(f"{'Options' if getattr(self.chart, 'view_commands_enabled', False) else 'Graph'} commands · {self.chart.title}")
            yield Input(placeholder="Enter a view command", id="graph-command", select_on_focus=False, compact=True)
            yield Static("HELP or ? for commands · Enter apply · Tab complete · Esc cancel", markup=False)
            yield Static("", id="graph-error", markup=False)

    def on_mount(self):
        self.query_one(Input).focus()

    @on(Input.Submitted, "#graph-command")
    def submitted(self, event):
        event.stop()
        line = event.value.strip()
        try:
            result = execute(self.chart, line)
        except (ValueError, OverflowError) as error:
            self.query_one("#graph-error", Static).update(str(error))
            return
        if result == "HELP":
            self.query_one(Input).value = ""
            self.query_one("#graph-error", Static).update("")
            self.completion = None
            return
        self.chart.command_history.append(line)
        self.dismiss()

    def complete_command(self):
        entry = self.query_one(Input)
        value = entry.value.upper()
        # Complete the current word, preserving earlier commands in the chain.
        head, separator, partial = value.rpartition(" ")
        if not separator:
            head, partial = "", value
        prefix = partial[0] if partial.startswith(("/", ".")) else ""
        stem_prefix = head + " " if separator else ""
        if separator:
            stem_prefix = entry.value.rpartition(' ')[0] + ' '
        choices = [stem_prefix + prefix + verb for verb in (*available_verbs(self.chart), "HELP", "?")]
        current = head.split()[-1].lstrip('./') if head.split() else ''
        if getattr(self.chart, 'view_commands_enabled', False):
            from .data import RANGES
            values = {'EXP': ['NEXT', 'PREV', 'FIRST', 'LAST', 'LIST', *self.chart.expiries],
                      'EXPIRY': ['NEXT', 'PREV', 'FIRST', 'LAST', 'LIST', *self.chart.expiries],
                      'SIDE': ['CALL', 'PUT'], 'STRIKE': ['ATM', 'NEXT', 'PREV'], 'HIST': RANGES, 'GRAPH': RANGES}
            choices = [stem_prefix + word for word in values.get(current, [])] + choices
        elif current == 'GOTO':
            choices = [stem_prefix + word for word in ('LEFT', 'RIGHT', '+10', '-10')] + choices
        elif current == 'MODE':
            from .comparison import MODES
            choices = [stem_prefix + word for word in MODES] + choices
        tokens = head.split()
        boundaries = command_starts(tokens)
        if boundaries and tokens[boundaries[-1]].lstrip("/.") == "IND":
            used = {INDICATOR_ALIASES.get(name, name) for name in tokens[boundaries[-1] + 1:]}
            choices = [stem_prefix + name for name in (*available_indicators(self.chart), *INDICATOR_ALIASES)
                       if INDICATOR_ALIASES.get(name, name) in available_indicators(self.chart)
                       and INDICATOR_ALIASES.get(name, name) not in used] + choices
        if self.completion is not None and entry.value == self.completion[1][self.completion[2]]:
            stem, options, index = self.completion
            index = (index + 1) % len(options)
        else:
            stem, options, index = value, [choice for choice in choices if choice.upper().startswith(value)], 0
        if options:
            self.completion = (stem, options, index)
            entry.value = options[index]
            entry.cursor_position = len(entry.value)

    def on_key(self, event):
        if event.key in ("up", "down") and self.chart.command_history:
            self.history_index = max(0, min(len(self.chart.command_history), self.history_index + (-1 if event.key == "up" else 1)))
            entry = self.query_one(Input)
            entry.value = self.chart.command_history[self.history_index] if self.history_index < len(self.chart.command_history) else ""
            entry.cursor_position = len(entry.value)
            event.stop()
