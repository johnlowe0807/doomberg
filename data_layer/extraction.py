"""Typed extraction, safe calculations, pivots, summaries and lossless exports.

Operations run in order, in a worker, over explicitly bounded normalized records.
No eval, provider calls, pretty-number strings, implicit joins or currency conversions.
"""
from __future__ import annotations

import ast
import asyncio
import csv
from dataclasses import asdict, replace
from datetime import date, datetime
import json
import math
from pathlib import Path
import re
import shlex
import statistics

from .query_models import DataStep, ExtractQuery, ExtractedData, TableQuery, TableBatch, Result
from .tabular import Dataset, Field, select, normalize
from .query_language import dot_offsets, apply_commands, ExpressionParser
from .serialization import key
from . import field_values as scalar

VERSION='extraction-v1'
VERBS=('WHERE','SEARCH','SORT','FIELDS','LIMIT','CALC','PIVOT','STATS','CLEAR','HELP','HOME','EXPORT')
CONTEXT=('symbol','underlying','series','reference','path','namespace','source','currency','revenue_currency','metric','unit','period','statement')
SELECTION={'WHERE','SEARCH','SORT','FIELDS','LIMIT'}


def parse_steps(line):
    if len(line)>16000:raise ValueError('Command text is too long')
    line=line.strip();offsets=sorted(set([0,*dot_offsets(line)]));out=[]
    for start,end in zip(offsets,offsets[1:]+[len(line)]):
        match=re.fullmatch(r'[.]?([A-Za-z]+|\?)(?:\s+(.*))?',line[start:end].strip(),re.S)
        if not match:raise ValueError('Use .WHERE/.FIELDS/.CALC/.PIVOT/.STATS/.EXPORT; HELP lists syntax')
        op,arg=match[1].upper(),(match[2] or '').strip()
        if op not in VERBS and op!='?':raise ValueError('Unknown extraction command '+op)
        if op in ('HELP','HOME','?','EXPORT') and end!=len(line):raise ValueError(op+' must be last')
        if op in ('HELP','HOME','?') and arg:raise ValueError(op+' takes no arguments')
        if op=='CALC':calculation(arg)
        elif op=='PIVOT':pivot_args(arg)
        elif op=='STATS':stats_args(arg)
        out.append(DataStep(op,arg))
    return tuple(out)


def names(text):
    result=tuple(v.strip().casefold() for v in text.split(','))
    if not result or any(not re.fullmatch(r'[a-z_][a-z0-9_]*',v) for v in result) or len(set(result))!=len(result):
        raise ValueError('Use unique comma-separated field names')
    return result


def calculation(text):
    match=re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)',text,re.S)
    if not match:raise ValueError('Use CALC name = arithmetic expression')
    name,expression=match[1].casefold(),match[2]
    try:tree=ast.parse(expression,mode='eval')
    except (SyntaxError,RecursionError):raise ValueError('Invalid calculation expression') from None
    nodes=list(ast.walk(tree))
    allowed=(ast.Expression,ast.BinOp,ast.UnaryOp,ast.Name,ast.Constant,ast.Call,ast.Load,
        ast.Add,ast.Sub,ast.Mult,ast.Div,ast.USub,ast.UAdd)
    if len(nodes)>128 or any(not isinstance(n,allowed) for n in nodes):raise ValueError('Only numbers, fields, + - * / and documented functions are supported')
    functions={'abs','sqrt','log','coalesce','days','year','month','lag','diff','pct_change'}
    for node in nodes:
        if isinstance(node,ast.Name) and node.id.startswith('_'):raise ValueError('Private names are unavailable')
        if isinstance(node,ast.Constant) and (type(node.value) not in (int,float) or not math.isfinite(node.value)):raise ValueError('Calculations use finite numeric constants')
        if isinstance(node,ast.Call) and (not isinstance(node.func,ast.Name) or node.func.id not in functions or node.keywords):raise ValueError('Unsupported calculation function')
    return name,expression,tree.body


def pivot_args(text):
    match=re.fullmatch(r'(\w+)\s+(\w+)\s+BY\s+(.+)',text,re.I)
    if not match:raise ValueError('Use PIVOT metric value BY symbol,period_end (duplicate cells are errors)')
    return match[1].casefold(),match[2].casefold(),names(match[3])


def stats_args(text):
    parts=re.split(r'\s+BY\s+',text,maxsplit=1,flags=re.I)
    return names(parts[0]),names(parts[1]) if len(parts)>1 else ()


def schema_info(schema):
    return {name:{'kind':field.kind,'title':field.title or name,'unit':field.unit,'period':field.period,
                  'unit_field':'unit' if 'unit' in schema and (name in ('value','sum','mean','min','max','std') or field.unit=='row_unit') else None,
                  'currency_field':('revenue_currency' if name.startswith('revenue_') and 'revenue_currency' in schema else 'currency')
                      if 'currency' in schema and (field.unit.startswith('currency') or name=='value') else None,
                  'temporal_precision':'instant' if field.kind=='timestamp' else 'calendar_date' if field.kind=='date' else None,
                  'timezone':'UTC' if field.kind=='timestamp' else None}
            for name,field in schema.items()}


def _unit(field):return field.unit or 'unknown' if field.kind=='number' else field.kind


def _combine(a,b,op):
    if op in ('+','-'):
        if a==b:return a
        if a=='scalar':return b
        if b=='scalar':return a
        raise ValueError(f'Cannot add/subtract {a} and {b}')
    if op=='/':
        if a==b and a!='unknown':return 'ratio'
        return a if b=='scalar' else f'({a})/({b})'
    return b if a=='scalar' else a if b=='scalar' else f'({a})*({b})'


def calculate(rows,schema,argument,ordered):
    name,expression,tree=calculation(argument)
    if name in schema:raise ValueError('CALC cannot overwrite an existing field: '+name)
    partitions={};positions={}
    for index,row in enumerate(rows):
        identity=tuple(row.get(k) for k in CONTEXT if k in schema)
        group=partitions.setdefault(identity,[]);positions[index]=(group,len(group));group.append(index)
    def evaluate(node,index=None):
        if isinstance(node,ast.Constant):return node.value,'scalar'
        if isinstance(node,ast.Name):
            field=schema.get(node.id)
            if field is None:raise ValueError('Unknown calculation field '+node.id)
            unit='row_unit' if node.id=='value' and not field.unit and 'unit' in schema else _unit(field)
            return (rows[index].get(node.id) if index is not None else None),unit
        if isinstance(node,ast.UnaryOp):
            value,unit=evaluate(node.operand,index)
            if unit in ('text','date','timestamp','boolean'):raise ValueError('Arithmetic requires numeric fields')
            return None if value is None else -value if isinstance(node.op,ast.USub) else value,unit
        if isinstance(node,ast.BinOp):
            a,au=evaluate(node.left,index);b,bu=evaluate(node.right,index)
            if any(u in ('text','date','timestamp','boolean') for u in (au,bu)):raise ValueError('Use days/year/month for date calculations')
            op={ast.Add:'+',ast.Sub:'-',ast.Mult:'*',ast.Div:'/'}[type(node.op)];unit=_combine(au,bu,op)
            if a is None or b is None or op=='/' and b==0:return None,unit
            value=a+b if op=='+' else a-b if op=='-' else a*b if op=='*' else a/b
            return scalar.number(value),unit
        function=node.func.id;args=node.args
        arities={'abs':(1,),'sqrt':(1,),'log':(1,),'coalesce':(2,),'days':(2,),'year':(1,),'month':(1,),
                 'lag':(1,2),'diff':(1,2),'pct_change':(1,2)}
        if len(args) not in arities[function]:raise ValueError('Wrong argument count for '+function)
        if function in ('lag','diff','pct_change'):
            if not ordered:raise ValueError('SORT explicitly before lag/diff/pct_change')
            if not isinstance(args[0],ast.Name):raise ValueError(function+' requires a field name')
            lag=args[1].value if len(args)>1 and isinstance(args[1],ast.Constant) else 1 if len(args)==1 else None
            if type(lag) is not int or not 1<=lag<=1000:raise ValueError('Lag must be an integer 1–1000')
            current,unit=evaluate(args[0],index)
            if unit in ('text','date','timestamp','boolean'):raise ValueError('Window calculations need numeric fields')
            prior=None
            if index is not None:
                candidates,position=positions[index]
                if position>=lag:prior=rows[candidates[position-lag]].get(args[0].id)
            if function=='lag':return prior,unit
            if current is None or prior is None or function=='pct_change' and prior==0:return None,'percent' if function=='pct_change' else unit
            return scalar.number(100*(current/prior-1) if function=='pct_change' else current-prior),'percent' if function=='pct_change' else unit
        values=[evaluate(a,index) for a in args]
        if function=='coalesce':
            unit=_combine(values[0][1],values[1][1],'+')
            return next((v for v,u in values if v is not None),None),unit
        if function in ('days','year','month'):
            if any(u not in ('date','timestamp') for v,u in values):raise ValueError(function+' requires date/timestamp fields')
            if function=='days' and values[0][1]!=values[1][1]:raise ValueError('days requires two dates or two timestamps; precision cannot be mixed')
            unit='days' if function=='days' else 'year' if function=='year' else 'month'
            if any(v is None for v,u in values):return None,unit
            stamps=[datetime.fromisoformat(v) for v,u in values]
            return ((stamps[0]-stamps[1]).total_seconds()/86400 if function=='days' else stamps[0].year if function=='year' else stamps[0].month),unit
        value,unit=values[0]
        if unit in ('text','date','timestamp','boolean'):raise ValueError(function+' needs numeric fields')
        if function=='log' and unit not in ('ratio','scalar'):raise ValueError('log needs a dimensionless ratio')
        if value is None:return None,'ratio' if function=='log' else f'sqrt({unit})' if function=='sqrt' else unit
        return (abs(value) if function=='abs' else math.sqrt(value) if function=='sqrt' and value>=0 else math.log(value) if function=='log' and value>0 else None),('ratio' if function=='log' else f'sqrt({unit})' if function=='sqrt' else unit)
    _,unit=evaluate(tree)
    out=[]
    for index,row in enumerate(rows):
        value,_=evaluate(tree,index);out.append(dict(row,**{name:scalar.number(value)}))
    schema=dict(schema,**{name:Field('number',name,unit=unit)})
    return out,schema


def pivot(rows,schema,argument):
    column,value,by=pivot_args(argument)
    for name in (column,value,*by):
        if name not in schema:raise ValueError('Unknown pivot field '+name)
    if schema[value].kind!='number':raise ValueError('Pivot values must be numeric')
    guards=tuple(k for k in ('namespace','source','currency','revenue_currency','period','statement','period_start','period_end') if k in schema and k not in (column,value))
    by=tuple(dict.fromkeys((*by,*guards)))
    provenance=tuple(k for k in ('fetched_at','available_at','available_date','filing_date') if k in schema and k not in by)
    out={};result_schema={k:schema[k] for k in by};units={};seen=set();lineage={};input_rows={}
    for row in rows:
        label=row.get(column)
        if label is None:continue
        label=str(label).casefold()
        if not re.fullmatch(r'[a-z_][a-z0-9_]*',label):raise ValueError('Pivot labels must be field identifiers; select canonical metric names')
        if label in (*by,*provenance,'input_lineage'):raise ValueError('Pivot label collides with a context field: '+label)
        if len(result_schema)>2000:raise ValueError('Pivot supports at most 2000 fields; narrow WHERE')
        identity=tuple(row.get(k) for k in by);cell=(identity,label)
        if cell in seen:raise ValueError('Duplicate pivot cell for '+label+'; select a source/filing/context before pivoting')
        seen.add(cell)
        unit=row.get('unit') if value=='value' else schema[value].unit
        unit=unit or 'unknown'
        if label in units and units[label]!=unit:raise ValueError('Pivot metric has incompatible units: '+label)
        units[label]=unit;result_schema[label]=Field('number',label,unit=unit)
        out.setdefault(identity,dict(zip(by,identity)))[label]=row.get(value)
        input_rows.setdefault(identity,[]).append(row)
        lineage.setdefault(identity,{})[label]={k:row.get(k) for k in ('record_id','accession','filing_date','available_date','available_at','fetched_at','unit','provider_unit') if k in schema}
    for identity,row in out.items():
        for name in provenance:
            values=[r.get(name) for r in input_rows[identity]]
            known=[v for v in values if v is not None]
            row[name]=max(known) if known and (name not in ('available_at','available_date') or len(known)==len(values)) else None
        row['input_lineage']=json.dumps(lineage[identity],sort_keys=True,separators=(',',':'))
    result_schema.update({k:schema[k] for k in provenance})
    result_schema['input_lineage']=Field('text','Input Record Lineage (JSON)')
    return list(out.values()),result_schema


def summarize(rows,schema,argument):
    columns,by=stats_args(argument)
    for name in (*columns,*by):
        if name not in schema:raise ValueError('Unknown summary field '+name)
    if any(schema[name].kind!='number' for name in columns):raise ValueError('STATS needs numeric fields')
    by=tuple(dict.fromkeys((*by,*(k for k in CONTEXT if k in schema and k not in columns))))
    if set(by)&{'field','count','missing','sum','mean','min','max','std'}:raise ValueError('Summary grouping name collides with an output field; derive a differently named group first')
    groups={}
    for row in rows:groups.setdefault(tuple(row.get(k) for k in by),[]).append(row)
    out=[]
    for identity,group in groups.items():
        for name in columns:
            values=[r[name] for r in group if r.get(name) is not None]
            out.append(dict(zip(by,identity),field=name,count=len(values),missing=len(group)-len(values),
                sum=math.fsum(values) if values else None,mean=statistics.fmean(values) if values else None,
                min=min(values) if values else None,max=max(values) if values else None,
                std=statistics.stdev(values) if len(values)>1 else None,
                unit=group[0].get('unit') if name=='value' and 'unit' in schema else schema[name].unit or 'unknown'))
    out_schema={k:schema[k] for k in by}
    out_schema.update(field=Field('text','Field'),count=Field('number','Count',unit='count'),missing=Field('number','Missing',unit='count'),
        **{k:Field('number',k.title(),unit='row_unit') for k in ('sum','mean','min','max','std')},unit=Field('text','Unit'))
    return out,out_schema


def process(rows,schema,steps):
    rows=[dict(r) for r in rows];schema=dict(schema);ordered=False
    for step in steps:
        op,arg=step.operation.upper(),step.argument
        if op in SELECTION:
            dataset=Dataset(schema,tuple(schema),tuple(k for k,f in schema.items() if f.kind=='text'),None)
            query,_=apply_commands(TableQuery('stage',limit=10000),'.'+op+' '+arg,dataset)
            query,required=normalize(query,dataset)
            rows=list(select(query,dataset,required,TableBatch(tuple(rows),'stage',len(rows))).rows)
            schema={name:schema[name] for name in query.fields}
            if op=='SORT':ordered=True
        elif op=='CALC':rows,schema=calculate(rows,schema,arg,ordered)
        elif op=='PIVOT':rows,schema=pivot(rows,schema,arg);ordered=False
        elif op=='STATS':rows,schema=summarize(rows,schema,arg);ordered=False
        else:raise ValueError('Unsupported data transformation '+op)
    return rows,schema


async def execute(service,request,*,force=False,stored_only=False):
    from copy import deepcopy
    request=deepcopy(request)
    if type(request.max_rows) is not int or not 1<=request.max_rows<=10000:raise ValueError('Extraction max_rows must be 1–10000')
    dataset=service.table_dataset(request.source.dataset)
    steps=list(request.steps)
    # Leading filters push down before the extraction cap. Later operations keep their explicit order.
    source=request.source
    while steps and steps[0].operation in ('WHERE','SEARCH','LIMIT'):
        source,_=apply_commands(source,'.'+steps[0].operation+' '+steps[0].argument,dataset);steps.pop(0)
    # Validate syntax and static schema before any provider I/O. Pivot columns depend on returned metric names.
    dynamic=False;prefix=[]
    for step in steps:
        if step.operation=='PIVOT':
            if not dynamic:process([],dataset.fields,tuple(prefix))
            pivot_args(step.argument);dynamic=True
        elif not dynamic:prefix.append(step)
        elif step.operation=='CALC':calculation(step.argument)
        elif step.operation=='STATS':stats_args(step.argument)
        elif step.operation not in SELECTION:raise ValueError('Unknown extraction operation '+step.operation)
    if not dynamic:process([],dataset.fields,tuple(prefix))
    # Market screens enrich financial fields on demand. Ordinary price calculations must not
    # trigger every financial plug just because those fields exist in the discoverable schema.
    market={'equity','etf','fund','bonds','crypto','futures','cmdty','fx','index','most_gainers','most_losers','most_active'}
    if source.dataset in market:
        wanted=set(source.fields or dataset.defaults)
        for step in request.steps:
            if step.operation=='FIELDS' and step.argument=='*':wanted.update(dataset.fields)
            wanted.update(name.casefold() for name in re.findall(r'[A-Za-z_][A-Za-z0-9_]*',step.argument) if name.casefold() in dataset.fields)
        wanted.update(k for k in CONTEXT if k in dataset.fields)
        source_fields=tuple(k for k in dataset.fields if k in wanted)
    else:source_fields=tuple(dataset.fields)
    source=replace(source,fields=source_fields,limit=min(source.limit,request.max_rows))
    result=await service.query(source,force=force,stored_only=stored_only or request.stored_only)
    if result.data is None:return result
    rows,schema=await asyncio.to_thread(process,result.data.rows,{k:dataset.fields[k] for k in source_fields},tuple(steps))
    capped=result.data.matched>len(result.data.rows) or not result.data.matched_exact
    complete=result.data.complete and not capped and result.meta.complete is not False
    scope=(f'{len(result.data.rows)} input rows / {result.data.matched} matched; selection limit {source.limit}, safety cap {request.max_rows}. '
           'Operations run in order on this selection; summaries are not market-wide unless coverage is complete.')
    table=replace(result.data,rows=tuple(rows),fields=tuple(schema),matched=len(rows),missing={k:sum(r.get(k) is None for r in rows) for k in schema},
        complete=complete,ranking_complete=complete,execution='extraction')
    meta=replace(result.meta,version=key((VERSION,request,result.meta.version,rows)),complete=complete,
        reason='; '.join(filter(None,(result.meta.reason,'Extraction input was limited' if capped else ''))))
    coverage=dict(input_rows=len(result.data.rows),input_matched=result.data.matched,input_matched_exact=result.data.matched_exact,
        source_total=result.data.total,source_available=result.data.available,source_complete=result.data.complete,
        input_complete=complete,input_limited=capped,output_rows=len(rows),selection_limit=source.limit,safety_cap=request.max_rows)
    return Result(ExtractedData(table,schema,scope,request,coverage),meta,result.inputs)


def export_result(result,request,path):
    """Create a new file; JSON carries schemas/provenance, CSV has an accompanying metadata JSON."""
    if result.data is None:raise ValueError('No extraction result to export')
    if result.data.request is not None and result.data.request!=request:raise ValueError('Export request does not match the displayed result; wait for the selected query')
    path=Path(path).expanduser()
    if path.suffix.lower() not in ('.json','.csv'):raise ValueError('EXPORT needs a .json or .csv path')
    data=result.data;table=data.table
    envelope={'version':VERSION,'request':asdict(request),'schema':schema_info(data.schema),'metadata':asdict(result.meta),
        'inputs':[asdict(m) for m in result.inputs],'scope':data.scope,
        'coverage':dict(data.coverage,**{k:getattr(table,k) for k in ('total','available','matched','complete','matched_exact')}),'rows':list(table.rows)}
    created=[]
    try:
        if path.suffix.lower()=='.json':
            with path.open('x',encoding='utf-8') as f:
                created.append(path);json.dump(envelope,f,ensure_ascii=False,allow_nan=False,indent=2)
        else:
            metadata=path.with_suffix(path.suffix+'.meta.json')
            with metadata.open('x',encoding='utf-8') as f:
                created.append(metadata);json.dump(dict(envelope,rows=None),f,ensure_ascii=False,allow_nan=False,indent=2)
            with path.open('x',encoding='utf-8',newline='') as f:
                created.append(path);writer=csv.DictWriter(f,fieldnames=table.fields);writer.writeheader();writer.writerows(table.rows)
    except Exception:
        for own in created:own.unlink(missing_ok=True)
        raise
    return str(path)


def to_frame(result):
    """A quant-ready DataFrame; calendar dates stay dates, instants become UTC, nulls stay nullable."""
    import pandas as pd
    if result.data is None:raise ValueError('No extraction data: '+result.meta.reason)
    data=result.data
    frame=pd.DataFrame.from_records(data.table.rows,columns=data.table.fields)
    for name,spec in data.schema.items():
        if spec.kind=='timestamp':frame[name]=pd.to_datetime(frame[name],utc=True)
        elif spec.kind=='date':frame[name]=frame[name].map(lambda value:date.fromisoformat(value) if value else None)
        elif spec.kind=='number':frame[name]=pd.to_numeric(frame[name]).astype('Float64')
        elif spec.kind=='boolean':frame[name]=frame[name].astype('boolean')
        else:frame[name]=frame[name].astype('string')
    frame.attrs.update(schema=schema_info(data.schema),metadata=asdict(result.meta),scope=data.scope,
                       complete=data.table.complete,coverage=dict(data.coverage))
    if data.request is not None:frame.attrs['request']=asdict(data.request)
    return frame
