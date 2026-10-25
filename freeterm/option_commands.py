"""Read-only options-chain navigation through the shared view-command popup."""
from __future__ import annotations

import re
import shlex
from datetime import date
from data_layer.options import number

VERBS = ('EXP', 'SIDE', 'STRIKE', 'WIDTH', 'UNDERLYING', 'OPEN', 'GRAPH', 'HIST', 'BOOK', 'REFRESH')
ALIASES = {'EXPIRY': 'EXP', 'SYM': 'UNDERLYING', 'HISTORY': 'HIST'}


def help_text(view):
    expiries = '\n'.join(f'  {i + 1}: {expiry}' for i, expiry in enumerate(view.expiries)) or '  Expiries still loading / unavailable.'
    return '''Options monitor commands (read-only)
EXP NEXT|PREV|FIRST|LAST|YYYY-MM-DD|index · EXP LIST
SIDE CALL|PUT (C/P also accepted)
STRIKE ATM|NEXT|PREV|price — center the strike window / move its selection
WIDTH n — show up to n strikes on each side of the center (1–100)
UNDERLYING ticker — change the chain's underlying (SYM alias)
OPEN [CALL|PUT] [strike] — graph a contract in the displayed window
GRAPH [range] [interval] — graph the selected contract with chart controls
HIST [range] — historical OHLCV table for the selected contract (HISTORY alias)
BOOK — available bid/ask snapshot for the selected contract
REFRESH — force refresh of this underlying/expiry's cached chain
HELP or ? — this command list; EXPIRY is an alias for EXP
Examples: EXP NEXT .SIDE PUT .STRIKE ATM .WIDTH 8
Main line: /OMON AAPL .EXP NEXT .SIDE PUT · .HIST 1M
Enter also graphs the selected call/put. On a contract graph, CHAIN returns here.
History uses Yahoo's available contract data; expired/untraded contracts may have no history.
Ranges: 1D 5D 1M 3M 6M YTD 1Y 5Y MAX. GRAPH also accepts custom chart windows.
Commands apply in order and stop at the first error. Open/history/book must be last.
Available expiries (cached):
''' + expiries


def execute(view, line):
    words = shlex.split(line)
    if not words:
        raise ValueError('Enter an options command; HELP lists commands')
    starts = [0]; current = words[0].lstrip('./').upper()
    for i, word in enumerate(words[1:], 1):
        key = word.lstrip('./').upper()
        if word.startswith('.') or key in (*VERBS, *ALIASES, 'HELP', '?'):
            if ALIASES.get(current, current) == 'UNDERLYING' and i == starts[-1] + 1 and not word.startswith(('.', '/')):
                continue
            starts.append(i); current = key
    starts.append(len(words))
    navigation = {'OPEN', 'GRAPH', 'HIST', 'BOOK', 'HELP', '?'}
    if any(ALIASES.get(words[start].lstrip('./').upper(), words[start].lstrip('./').upper()) in navigation or
           (words[start].lstrip('./').upper() in ('EXP', 'EXPIRY') and [word.upper() for word in words[start + 1:start + 2]] == ['LIST']) for start in starts[:-2]):
        raise ValueError('Put OPEN/GRAPH/HIST/BOOK or HELP last in the command chain')
    for i, (start, end) in enumerate(zip(starts, starts[1:])):
        key = words[start].lstrip('./').upper(); key = ALIASES.get(key, key)
        args = words[start + 1:end]
        try:
            result = execute_one(view, key, args)
        except ValueError as error:
            if len(starts) > 2:
                raise ValueError(f'Command {i + 1} ({key}) failed: {error}. Earlier changes remain applied; later commands were skipped') from error
            raise
        if result == 'HELP':
            return result


def side(value):
    result = {'CALL': 'C', 'CALLS': 'C', 'PUT': 'P', 'PUTS': 'P'}.get(value.upper(), value.upper())
    if result not in ('C', 'P'):
        raise ValueError('Use SIDE CALL or SIDE PUT')
    return result


def execute_one(view, verb, args):
    from .graph_commands import GraphHelpScreen
    from data_layer.windows import is_range, candle_interval
    if verb in ('HELP', '?'):
        if args:
            raise ValueError('HELP takes no arguments')
        view.app.push_screen(GraphHelpScreen(view)); return 'HELP'
    if verb not in VERBS:
        raise ValueError('Unknown options command; HELP lists available commands')
    if verb == 'EXP':
        if len(args) != 1:
            raise ValueError('Use EXP NEXT, EXP YYYY-MM-DD, EXP index or EXP LIST')
        value = args[0].upper()
        if value == 'LIST':
            view.app.push_screen(GraphHelpScreen(view)); return 'HELP'
        expiries = view.expiries
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            try: date.fromisoformat(value)
            except ValueError as error: raise ValueError('Expiry must be a valid YYYY-MM-DD date') from error
            if expiries and value not in expiries:
                raise ValueError(f'Expiry {value} is not listed; use EXP LIST')
            view.requested_expiry = value
            if expiries: view.exp_i = expiries.index(value)
        else:
            if not expiries:
                if value not in ('NEXT', 'PREV', 'FIRST', 'LAST') and not (value.isdigit() and 1 <= int(value) <= 10000):
                    raise ValueError('Use EXP NEXT|PREV|FIRST|LAST|date|index|LIST')
                view.requested_expiry = value
                view.invalidate_chain(); view.reload(); return
            choices = {'NEXT': view.exp_i + 1, 'PREV': view.exp_i - 1, 'FIRST': 0, 'LAST': len(expiries) - 1}
            if value.isdigit():
                index = int(value) - 1
                if not 0 <= index < len(expiries): raise ValueError(f'Expiry index must be 1–{len(expiries)}')
            elif value in choices: index = max(0, min(len(expiries) - 1, choices[value]))
            else: raise ValueError('Use EXP NEXT|PREV|FIRST|LAST|date|index|LIST')
            view.exp_i, view.requested_expiry = index, expiries[index]
        view.invalidate_chain(); view.reload()
    elif verb == 'SIDE':
        if len(args) != 1: raise ValueError('Use SIDE CALL or SIDE PUT')
        view.side = side(args[0]); view.render_header()
    elif verb == 'STRIKE':
        if len(args) != 1: raise ValueError('Use STRIKE ATM, STRIKE NEXT/PREV or STRIKE price')
        value = args[0].upper()
        if value in ('NEXT', 'PREV'):
            table = view.query_one('#chain')
            if not table.row_count: raise ValueError('Chain is still loading / unavailable')
            table.move_cursor(row=max(0, min(table.row_count - 1, table.cursor_row + (1 if value == 'NEXT' else -1))))
        else:
            value = None if value == 'ATM' else number(value)
            if args[0].upper() != 'ATM' and (value is None or value <= 0):
                raise ValueError('Strike must be a finite positive price, or ATM')
            view.center = value; view.selected_strike = value
            view.invalidate_chain(); view.reload()
    elif verb == 'WIDTH':
        if len(args) != 1 or not args[0].isdigit() or not 1 <= int(args[0]) <= 100:
            raise ValueError('WIDTH must be an integer from 1 to 100')
        view.width = int(args[0]); view.reload()
    elif verb == 'UNDERLYING':
        if len(args) != 1 or not re.fullmatch(r'[A-Za-z^][A-Za-z0-9.^=\-]{0,31}', args[0]):
            raise ValueError('Use UNDERLYING ticker')
        view.sec = view.app.aliases.get(args[0].upper(), args[0].upper())
        view.exp_i, view.expiries, view.requested_expiry = 0, [], None
        view.current_expiry = None
        view.center, view.selected_strike = None, None
        view.invalidate_chain(); view.retitle(); view.reload()
    elif verb == 'OPEN':
        chosen_side = view.side
        if args and args[0].upper() in ('C', 'P', 'CALL', 'PUT', 'CALLS', 'PUTS'):
            chosen_side, args = side(args[0]), args[1:]
        if len(args) > 1 or (args and (number(args[0]) is None or number(args[0]) <= 0)):
            raise ValueError('Use OPEN [CALL|PUT] [strike]')
        symbol = view.selected_contract(number(args[0]) if args else None, chosen_side)
        view.app.run_command(symbol)
    elif verb in ('GRAPH', 'HIST', 'BOOK'):
        if verb == 'BOOK' and args:
            raise ValueError('BOOK takes no arguments')
        if verb == 'HIST' and (len(args) > 1 or (args and args[0].upper() not in ('1D','5D','1M','3M','6M','YTD','1Y','5Y','MAX'))):
            raise ValueError('Use HIST [1D|5D|1M|3M|6M|YTD|1Y|5Y|MAX]')
        if verb == 'GRAPH' and (len(args) > 2 or (args and not is_range(args[0]))):
            raise ValueError('Use GRAPH [range] [interval]')
        if verb == 'GRAPH' and len(args) == 2:
            interval = candle_interval(args[1]).value
            if view.hub.base_for(args[0].upper(), interval) is None:
                raise ValueError('That range/interval is unavailable from Yahoo')
        symbol = view.selected_contract()
        if verb == 'GRAPH':
            command = symbol + (' .TIME ' + args[0].upper() if args else '') + (' .SIZE ' + args[1] if len(args) == 2 else '')
        else:
            command = symbol + (' /HP ' + (args[0].upper() if args else '3M') if verb == 'HIST' else ' /BOOK')
        view.app.run_command(command)
    elif verb == 'REFRESH':
        if args: raise ValueError('REFRESH takes no arguments')
        view.hub.invalidate_resource('yf', 'expiries', symbol=view.sec)
        expiry = view.loaded_expiry or view.current_expiry
        if expiry:
            view.hub.invalidate_resource('yf', 'chain', symbol=view.sec, expiry=expiry)
        view.reload()
