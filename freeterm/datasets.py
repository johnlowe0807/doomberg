"""One extraction client for every registered normalized dataset."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import shlex

from rich.text import Text
from textual.binding import Binding
from textual.widgets import DataTable, Static

from .app import View, VIEWS
from data_layer.query_models import ExtractQuery, TableQuery
from data_layer.tabular import Dataset
from data_layer.access import parameters
from data_layer.extraction import parse_steps, process, export_result, schema_info

HELP='''Normalized data extraction
/DATA — catalog of datasets and all identified fields; Enter opens dataset help.
/DATA dataset [symbol] [_parameter=value ...] — select an existing normalized dataset.
Examples:
  /DATA reporting AAPL _statement=balance
  /DATA reporting AAPL _source=yahoo _statement=balance _period=annual
  /DATA facts AAPL _as_of=2024-12-31
  /DATA statements AAPL _statement=balance _period=quarterly
  /DATA prices AAPL _range=1Y _interval=1d
  /DATA events _kind=economic   /DATA articles _symbols=AAPL,MSFT
  /DATA options AAPL _expiry=2026-12-18
  /DATA profile_fields AAPL — all provider profile scalar fields and structural paths.
  /EXTRACT — open the active screen's underlying dataset with its context.
Commands run in order on normalized numbers/dates, before display formatting:
  .WHERE metric IN ('assets','liabilities','stockholders_equity')
  .FIELDS symbol,metric,value,unit,period_end,filing_date,currency
  .SORT period_end ASC .LIMIT 100 .SEARCH "text"
  .PIVOT metric value BY symbol,period_end
  .CALC debt_ratio = liabilities / assets
  .CALC growth = pct_change(assets) — explicitly SORT first; partitioned by source/security/units/period.
  .CALC age = days(filing_date,period_end) — two dates or two UTC instants.
  .STATS value BY metric — count/missing/sum/mean/min/max/sample std; semantic context retained.
  .EXPORT "/tmp/extract.json" — schema, raw values, provenance and coverage.
  .EXPORT "/tmp/extract.csv" — raw columns and adjacent .csv.meta.json; existing files never overwritten.
  .CLEAR — reset operations; source security/period/parameters remain.
CALC: + - * /, abs, sqrt, log, coalesce, days, year, month, lag, diff, pct_change.
Missing operands and zero denominators stay null. Incompatible additions are rejected.
PIVOT rejects duplicate cells; currency/source/actual period boundaries remain separate.
Leading WHERE/SEARCH/LIMIT clauses apply at the source before the 10,000-row extraction cap.
EXTRACT preserves the active table selection limit; DATA starts with a 10,000-row limit.
Later operations follow their written order. WHERE after PIVOT filters pivoted records.
Coverage and input counts describe the selected source, never an implied whole-market calculation.
Calendar dates, reporting periods, publication dates and UTC instants remain distinct.
SEC reporting includes every eligible reported concept/unit, with latest filings per actual period;
/facts retains amendments and context. SEC duration facts do not identify every statement grouping.
Use Yahoo statements for provider-classified income/cashflow line items; unknown units remain explicit.
Enter opens full record details. ? lists all fields and dataset parameters. Esc returns to the original view.
'''


def request_from_args(service,args,sec=None):
    if not args:return None
    name=args[0].casefold();dataset=service.table_dataset(name);allowed=parameters(service,name)
    supplied={};positional=[]
    for token in args[1:]:
        if token.startswith('_') and '=' in token:
            field,value=token[1:].split('=',1);field=field.casefold()
            if field in supplied:raise ValueError('Duplicate parameter '+field)
            if field not in allowed and field!='stored_only':raise ValueError('Unknown parameter '+field+'; available: '+', '.join(allowed))
            if field in ('symbols','references','feeds'):value=tuple(v for v in value.split(',') if v)
            elif field in ('cik','year','max_pages','limit'):
                try:value=int(value)
                except ValueError:raise ValueError(field+' needs an integer') from None
            elif field in ('stored','stored_only'):
                if value.casefold() not in ('true','false'):raise ValueError(field+' needs true/false')
                value=value.casefold()=='true'
            elif field in ('source','statement','period','kind','scope','dataset','domain','operation'):
                value=value.casefold()
            supplied[field]=value
        else:positional.append(token)
    if sec:positional.insert(0,sec)
    if positional:
        field='symbol' if 'symbol' in allowed else 'series' if 'series' in allowed else 'symbols' if 'symbols' in allowed else 'references' if 'references' in allowed else None
        if field is None or field in supplied or len(positional)!=1:raise ValueError('Use named _parameter=value arguments for this dataset')
        supplied[field]=tuple(positional[0].split(',')) if field in ('symbols','references') else positional[0]
    aliases=getattr(service,'aliases',{})
    if supplied.get('symbol'):supplied['symbol']=aliases.get(supplied['symbol'].upper(),supplied['symbol'].upper())
    if supplied.get('symbols'):supplied['symbols']=tuple(aliases.get(v.upper(),v.upper()) for v in supplied['symbols'])
    stored=supplied.pop('stored_only',False)
    source=TableQuery(name,parameters=supplied,limit=10000)
    if dataset.validate:dataset.validate(source,dataset)
    return ExtractQuery(source,stored_only=stored)


class DataView(View):
    FUNC='DATA'
    view_commands_enabled=table_commands_enabled=True
    extraction_commands_enabled=True
    refresh_secs=None
    hints='. commands  ? schema/help  Enter full record  Esc back  Ctrl+R refresh'
    BINDINGS=[Binding('question_mark','data_help',show=False)]
    DEFAULT_CSS='''DataView > Static {height: auto; max-height: 4;} DataView > DataTable {height: 1fr;}'''

    def __init__(self,hub,sec,args):
        super().__init__(hub,sec,args)
        self.request=request_from_args(hub.queries,args,sec)
        self._pending_request=None
        self._revision=0
        self.result=None;self.command_history=[];self._schema={};self._rows=()

    @property
    def table_query(self):return self.request.source if self.request else TableQuery('catalog')

    @property
    def dataset(self):
        if self._schema:return Dataset(self._schema,tuple(self._schema),(),None)
        if self.request:return self.hub.queries.table_dataset(self.request.source.dataset)
        return Dataset({},(),(),None)

    @property
    def title(self):
        return 'DATA '+(self.request.source.dataset+' '+str(self.request.source.parameters.get('symbol') or '') if self.request else 'CATALOG')

    def compose(self):
        yield Static('',id='data-settings',markup=False)
        yield Static('Loading normalized records…',id='data-info',markup=False)
        yield DataTable(cursor_type='row')

    def help_text(self):
        if not self.request:return HELP
        source=self.hub.queries.table_dataset(self.request.source.dataset).fields
        fields='\n'.join(f'{name:<30} {f.kind:<10} {f.unit or ""}' for name,f in source.items())
        current='\n'.join(f'{name:<30} {f.kind:<10} {f.unit or ""}' for name,f in self.dataset.fields.items())
        return HELP+'\nDataset parameters (None means required/context-dependent):\n'+json.dumps(parameters(self.hub.queries,self.request.source.dataset),default=list,indent=2)+'\n\nAll identified source fields:\n'+fields+'\n\nCurrent output fields:\n'+current

    def action_data_help(self):
        from .graph_commands import GraphHelpScreen
        self.app.push_screen(GraphHelpScreen(self))

    def open_graph_commands(self):
        from .graph_commands import GraphCommandScreen
        self.app.push_screen(GraphCommandScreen(self))

    def execute_graph_command(self,line):
        steps=parse_steps(line)
        if not self.request:
            self.action_data_help();return 'HELP'
        candidate=self._pending_request or self.request
        for step in steps:
            if step.operation in ('HELP','HOME','?'):continue
            if step.operation=='EXPORT':continue
            if step.operation=='CLEAR':
                if step.argument:raise ValueError('CLEAR resets extraction operations; dataset parameters remain')
                candidate=replace(candidate,steps=())
            else:candidate=replace(candidate,steps=(*candidate.steps,step))
        # Validate without I/O using the same planner. Dynamic pivot columns are checked against the result on load.
        dynamic=False
        for step in candidate.steps:
            if step.operation=='PIVOT':dynamic=True
        if not dynamic:
            process([],self.hub.queries.table_dataset(candidate.source.dataset).fields,candidate.steps)
        export=steps[-1] if steps[-1].operation=='EXPORT' else None
        self._revision+=1
        if export:
            path=shlex.split(export.argument)
            if len(path)!=1:raise ValueError('EXPORT needs one quoted .json/.csv path')
            if candidate!=self.request or self.result is None or self._result_request!=candidate:
                self.run_worker(self._apply_export(candidate,path[0],self._revision),group='data',exclusive=True,exit_on_error=False)
            else:self.run_worker(self._write_export(self.result,candidate,path[0]),group='data-export',exit_on_error=False)
        elif candidate!=self.request:
            self._pending_request=candidate;self.reload()
        if steps[-1].operation in ('HELP','HOME','?'):
            self.action_data_help();return 'HELP'

    async def _write_export(self,result,request,path):
        try:
            saved=await asyncio.to_thread(export_result,result,request,path)
            if self.is_mounted and self.request==request:
                self.query_one('#data-info',Static).update('Exported '+saved+' · '+result.data.scope)
        except Exception as error:self.hub.warn('EXPORT: '+str(error))

    async def _apply_export(self,request,path,revision):
        try:
            result=await self.hub.query(request)
            if result.data is None:raise ValueError(result.meta.reason)
            if self.is_mounted and self._revision==revision:
                self.request=request;self._pending_request=None;self.show_result(result,request)
            await self._write_export(result,request,path)
        except Exception as error:self.hub.warn('Extraction: '+str(error))

    async def load(self):
        if not self.request:
            table=self.query_one(DataTable);table.clear(columns=True);table.add_columns('Dataset','Fields','Parameters')
            self._catalog=list(self.hub.queries.tables)
            for name in self._catalog:
                table.add_row(name,str(len(self.hub.queries.table_dataset(name).fields)),', '.join(parameters(self.hub.queries,name)),key=name)
            self.query_one('#data-info',Static).update('Every registered normalized dataset · Enter shows all fields/parameters · /DATA dataset [symbol]')
            return
        request=self._pending_request or self.request
        try:result=await self.hub.query(request)
        except (ValueError,OverflowError,TypeError) as error:
            if self._pending_request==request:self._pending_request=None
            self.hub.warn('Extraction: '+str(error));return
        if request!=(self._pending_request or self.request):return
        self.request=request;self._pending_request=None
        self.show_result(result,request)

    def show_result(self,result,request):
        self.result=result;self._result_request=request
        self.query_one('#data-settings',Static).update(' '.join('.'+s.operation+' '+s.argument for s in request.steps) or str(request.source.parameters))
        if result.data is None:
            self.query_one('#data-info',Static).update('N/A · '+result.meta.reason);self._rows=();self.query_one(DataTable).clear(columns=True);return
        data=result.data;self._schema=data.schema;self._rows=data.table.rows
        structural=any(s.operation in ('FIELDS','PIVOT','STATS') for s in request.steps)
        defaults=self.hub.queries.table_dataset(request.source.dataset).defaults
        calculated=tuple(s.argument.split('=',1)[0].strip().casefold() for s in request.steps if s.operation=='CALC')
        fields=data.table.fields if structural else tuple(dict.fromkeys(f for f in (*defaults,*calculated) if f in data.schema))
        # The extraction result retains all fields; only the initial presentation uses dataset defaults.
        table=self.query_one(DataTable);cursor=table.cursor_row;table.clear(columns=True)
        for name in fields:table.add_column(data.schema[name].title or name,key=name)
        display_limit=min(1000,max(1,20000//max(1,len(fields))))
        for i,row in enumerate(self._rows[:display_limit]):
            cells=[]
            for name in fields:
                value=row.get(name)
                cells.append(Text('N/A' if value is None else str(value),justify='right' if data.schema[name].kind=='number' else 'left'))
            table.add_row(*cells,key=str(i))
        if table.row_count:table.move_cursor(row=min(cursor,table.row_count-1))
        completeness='COMPLETE selection' if data.table.complete else 'PARTIAL/BOUNDED source'
        self.query_one('#data-info',Static).update(f'{len(self._rows)} output records · {completeness} · '+data.scope+' · '+result.meta.source+' '+result.meta.freshness.upper()+
            (f' · display capped at {display_limit} rows; exports retain the full selected result' if len(self._rows)>display_limit else '')+
            ('\n'+result.meta.reason if result.meta.reason else ''))

    def on_data_table_row_selected(self,event):
        from .fundamentals import Pager
        if not self.request:
            name=event.row_key.value;dataset=self.hub.queries.table_dataset(name)
            text='Parameters: '+json.dumps(parameters(self.hub.queries,name),default=list,indent=2)+'\n\n'+json.dumps(schema_info(dataset.fields),indent=2)
            self.app.push_screen(Pager('DATA '+name,text));return
        index=int(event.row_key.value)
        if index<len(self._rows):self.app.push_screen(Pager(self.title,json.dumps(self._rows[index],ensure_ascii=False,indent=2)))

    def on_key(self,event):
        if (event.character or event.key)=='.':self.open_graph_commands();event.stop()
        elif (event.character or event.key)=='?':self.action_data_help();event.stop()


def context_request(view):
    """Preserve active source identity, timeframe, filters and selected contract expiry."""
    if view is None:raise ValueError('Open a data screen first, or choose a dataset with /DATA')
    if isinstance(view,DataView):return view.request
    query=getattr(view,'table_query',None)
    if query is not None:return ExtractQuery(query)
    func=view.FUNC;symbol=view.sec
    params={'symbol':symbol} if symbol else {}
    if func=='FA':
        name='statements' if getattr(view,'src','').startswith('yfinance') else 'statement_values'
        params.update(statement='cashflow' if view.stmt=='cash' else view.stmt,period='annual' if view.annual else 'quarterly')
    elif func=='FIL':name='filings';params.update(form=getattr(view,'form',None) or None,year=int(view.year) if getattr(view,'year',None) else None)
    elif func=='OMON':name='options';params.update(expiry=getattr(view,'loaded_expiry',None))
    elif func=='BOOK':name='book'
    elif func=='DAT':name='statistics';params.update(range=view.rng,interval=view.iv)
    elif func=='DES':name='profile_fields'
    elif func=='FRED DES':name='series_metadata';params={'series':symbol}
    elif func in ('CN','TOP'):name='news'
    elif func=='ERN':name='company';params.update(dataset='earnings_history')
    elif func=='EVTS':
        from .config import load_watchlist
        name='calendars';params={'symbols':tuple(s for s in load_watchlist() if not any(c in s for c in '^=') and not s.endswith('-USD'))}
    elif func=='AUCT':name='auctions';params={}
    elif func=='GC':name='series';params={'series':'DGS1MO,DGS3MO,DGS6MO,DGS1,DGS2,DGS3,DGS5,DGS7,DGS10,DGS20,DGS30'}
    elif func=='BTMM':name='series';params={'series':','.join(sid for _,sid in view.FRED_ROWS)}
    elif func in ('MON','HOME'):name='quotes';params={'symbols':tuple(view.hub.watch)}
    elif getattr(view,'mode',None)=='series':name='series';params={'series':view.series[0]}
    elif getattr(view,'mode',None)=='comp':
        name='comparison';params={'references':tuple(view.compare_refs),'range':view.rng,'interval':view.iv,'mode':view.compare_mode}
    elif hasattr(view,'rng'):name='prices';params.update(range=view.rng,interval=view.iv)
    elif symbol:name='profile_fields'
    else:raise ValueError('Choose a dataset with /DATA; this screen has several independent sources')
    return ExtractQuery(TableQuery(name,parameters=params,limit=10000))


VIEWS['DATA']=DataView
