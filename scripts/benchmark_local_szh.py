"""Small, read-only SQL comparison, restricted to the local szh_new instance.

No host/database override, workflow execution, remote source lookup or retry.
One query connection; a cancellation connection opens only on watchdog expiry.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import ctypes
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
HOST, PORT, DATABASE = '127.0.0.1', 3306, 'szh_new'
REPEATS, ROW_CAP, DATA_BUDGET, CALL_BUDGET = 3, 500, 60, 120


def require_local(host: str, port: int, database: str) -> None:
    if host not in {'127.0.0.1', 'localhost', '::1'} or port != PORT or database != DATABASE:
        raise ValueError('Refused: only the verified local szh_new instance is permitted')


def local_listener() -> dict:
    script = "$c=Get-NetTCPConnection -State Listen -LocalPort 3306 -ErrorAction Stop; $ids=@($c.OwningProcess | Select-Object -Unique); if($ids.Count -ne 1){throw 'Ambiguous listener'}; Get-CimInstance Win32_Process -Filter ('ProcessId='+$ids[0]) | Select-Object ProcessId,Name | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True, timeout=8, check=True)
    listener = json.loads(result.stdout)
    if listener['Name'].lower() != 'mysqld.exe':
        raise RuntimeError('Loopback listener is not a local MySQL process; possible tunnel/proxy')
    return listener


class MemoryStatus(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
               *[(name, ctypes.c_ulonglong) for name in
                 ('totalPhysical', 'availablePhysical', 'totalPageFile', 'availablePageFile',
                  'totalVirtual', 'availableVirtual', 'availableExtended')]]


def available_memory() -> int:
    state = MemoryStatus()
    state.length = ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        raise RuntimeError('Cannot check local memory')
    return state.availablePhysical


def encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode)


def canonical(rows) -> Counter:
    return Counter(dump(row) for row in rows)


def quote(name: str) -> str:
    return '`' + name.replace('`', '``') + '`'


def step(table, fields, match=None, where='', params=(), tail=''):
    return dict(table=table, fields=fields, match=match, where=where, params=params, tail=tail)


def cases():
    def device(key, requirement):
        return [
            step('t_devicemanagement', {'deviceManagementId':'deviceId','c_deviceName':'deviceName'},
                 where='deviceManagementId=%s', params=(key,)),
            step('t_inspectiondevice', {'mdDInspectionMaintenancePlanId':'planId'}, ('c_device','deviceId')),
            step('t_dinspectionmaintenanceplan', {'dInspectionMaintenancePlanId':'validPlanId',
                 'c_planName':'planName','c_jihualeixing':'planType','c_baoyangzhouqi':'cycleCode'},
                 ('dInspectionMaintenancePlanId','planId')),
            requirement,
        ]
    return [
        ('maintenance', '4#皮带机B：保养计划与润滑要求', device('0001567-1080e120bada4139bac3a9e29443b539',
            step('t_xunjianjihuarunhuabaoyang', {'xunjianjihuarunhuabaoyangId':'requirementId',
                 'c_runhuadianweimingcheng':'pointName','c_runhuazhouqi':'cycleDays',
                 'c_runhuayoupaihao':'requiredOil','c_runhualiang':'requiredAmount','c_runhuafangshi':'requiredMethod'},
                 ('mdDInspectionMaintenancePlanId','planId')))),
        ('inspection', '普通压力表：巡检计划与检查要求', device('0001567-00a2dd74972f4fb0bc6e8a4859d28ec0',
            step('t_detailsofinspectionindicators', {'detailsOfInspectionIndicatorsId':'requirementId',
                 'c_inspectionName':'itemName','c_inspectionIndicators':'indicator','c_inspectionContent':'requirement'},
                 ('mdDInspectionMaintenancePlanId','planId')))),
        ('quality', '石灰石：最新5张质检单与项目标准', [
            step('t_incomingmaterialqcform', {'incomingMaterialQcFormId':'formId','c_incomingQcNo':'formNumber',
                 'c_qualityInspectionTemplate':'templateId','c_testResults':'originalResult'},
                 where='c_qualityInspectionTemplate=%s AND c_qualityInspection=%s',
                 params=('0001328-d73088c806e744b4823f016db00af4f1',1),
                 tail=' ORDER BY createdOn DESC,incomingMaterialQcFormId DESC LIMIT 5'),
            step('t_qualityinspectiontemplate', {'qualityInspectionTemplateId':'validTemplateId',
                 'c_templateName':'templateName','c_product':'productId'}, ('qualityInspectionTemplateId','templateId')),
            step('t_qitemplatedetails', {'qITemplateDetailsId':'requirementId','c_xuanzezhijianxiangmu':'projectId',
                 'c_judgeRule':'judgeRule','c_standardMin':'minimum','c_standardMax':'maximum'},
                 ('mdQualityInspectionTemplateId','templateId')),
            step('t_qualityinspectionproject', {'qualityInspectionProjectId':'validProjectId',
                 'c_projectCode':'projectCode','c_projectName':'projectName','c_inspectionStandard':'standard'},
                 ('qualityInspectionProjectId','projectId')),
        ]),
    ]


class Probe:
    def __init__(self, out: Path):
        self.out, self.connection, self.connection_id = out, None, None
        self.report = {'startedAt':datetime.now(timezone.utc).isoformat(), 'host':HOST, 'port':PORT,
                       'database':DATABASE, 'repeats':REPEATS, 'connectionAttempts':0,
                       'dataCalls':0, 'scenarios':[], 'calls':[], 'cancellations':[]}
        self.credentials = None
        self.deadline = time.monotonic()+90
        self.min_memory = available_memory()
        self.failed = False

    def save(self):
        self.report['minimumAvailableMemoryMiB'] = round(self.min_memory/1024**2, 1)
        (self.out/'results.json').write_text(json.dumps(self.report,ensure_ascii=False,indent=2,default=encode)+'\n',encoding='utf-8')

    def connect(self):
        import pymysql
        self.report['connectionAttempts'] += 1
        return pymysql.connect(host=HOST, port=PORT, database=DATABASE, user=self.credentials[0],
            password=self.credentials[1], charset='utf8mb4', autocommit=True, local_infile=False,
            connect_timeout=2, read_timeout=4, write_timeout=3, cursorclass=pymysql.cursors.DictCursor)

    def cancel(self, active, sequence):
        if not active.is_set():
            return
        self.failed = True
        record = {'sequence':sequence, 'connectionId':self.connection_id}
        try:
            with self.connect() as control, control.cursor() as cursor:
                cursor.execute('KILL QUERY '+str(int(self.connection_id)))
            record['status'] = 'kill_query_sent'
        except Exception as exc:
            record.update(status='cancel_failed', errorType=type(exc).__name__)
        self.report['cancellations'].append(record)

    def run(self, sql, params=(), *, label, kind='business'):
        self.min_memory = min(self.min_memory, available_memory())
        if self.failed or time.monotonic()>self.deadline or self.min_memory<768*1024**2:
            raise RuntimeError('Stopped: failure, elapsed budget or low available memory')
        if len(self.report['calls'])>=CALL_BUDGET or (kind=='business' and self.report['dataCalls']>=DATA_BUDGET):
            raise RuntimeError('Statement budget exhausted')
        if len(sql)>10000:
            raise RuntimeError('Statement exceeds this small-test scope')
        sequence = len(self.report['calls'])+1
        entry = {'sequence':sequence, 'label':label, 'kind':kind, 'connectionId':self.connection_id,
                 'sqlSha256':hashlib.sha256(sql.encode()).hexdigest(), 'status':'started'}
        self.report['calls'].append(entry)
        if kind=='business':
            self.report['dataCalls'] += 1
        self.save()
        active = threading.Event()
        active.set()
        timer = threading.Timer(2.0,self.cancel,args=(active,sequence)) if self.connection_id else None
        if timer:
            timer.start()
        try:
            with self.connection.cursor() as cursor:
                start = time.perf_counter()
                cursor.execute('/*test5-local-szh-benchmark*/ '+sql,params)
                rows = cursor.fetchmany(ROW_CAP+1)
                duration = (time.perf_counter()-start)*1000
            if len(rows)>ROW_CAP or len(dump(rows).encode())>2*1024**2:
                raise RuntimeError('Result exceeds bounded sample size')
            if self.failed or duration>1000:
                raise RuntimeError('Statement exceeded 1-second stop threshold')
            entry.update(status='ok', durationMs=round(duration,4), returnedRows=len(rows))
            return rows, duration
        except Exception as exc:
            self.failed = True
            entry.update(status='failed',errorType=type(exc).__name__)
            raise
        finally:
            active.clear()
            if timer:
                timer.cancel()
                timer.join()
            self.save()

    def plan(self, sql, params, label):
        rows,_ = self.run('EXPLAIN FORMAT=JSON '+sql,params,label=label,kind='plan')
        plan = json.loads(next(iter(rows[0].values())))
        def work(value):
            if isinstance(value,dict):
                return float(value.get('rows_examined_per_scan',0))+sum(work(v) for v in value.values())
            return sum(work(v) for v in value) if isinstance(value,list) else 0
        if work(plan)>100000:
            raise RuntimeError('Estimated scan work exceeds small-test budget')
        (self.out/(label+'.plan.json')).write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')


def separate(probe, name, steps, *, prepare=False):
    upstream, elapsed = [], 0.0
    for index, item in enumerate(steps):
        fields = ','.join(quote(raw)+' AS '+quote(alias) for raw,alias in item['fields'].items())
        predicates, params = ['isDeleted=0'], list(item['params'])
        if item['where']:
            predicates.append(item['where'])
        if item['match']:
            raw, parent = item['match']
            keys = list(dict.fromkeys(row[parent] for row in upstream if row.get(parent) not in (None,'')))
            fields += ','+quote(raw)+' AS __key'
            predicates.append(quote(raw)+' IN ('+','.join(['%s']*len(keys))+')' if keys else '1=0')
            params += keys
        sql='SELECT '+fields+' FROM '+quote(item['table'])+' WHERE '+' AND '.join(predicates)+item['tail']
        label=f'{name}-separate-{index+1}'
        if prepare:
            probe.plan(sql,params,label)
            (probe.out/(label+'.sql')).write_text(sql+';\n',encoding='utf-8')
        fetched,duration=probe.run(sql,params,label=label)
        start=time.perf_counter()
        if not item['match']:
            upstream=fetched
        else:
            grouped=defaultdict(list)
            for row in fetched:
                if row['__key'] not in (None,''):
                    grouped[str(row['__key'])].append(row)
            upstream=[{**old,**{alias:match.get(alias) for alias in item['fields'].values()}}
                      for old in upstream for match in grouped.get(str(old.get(parent)),[]) or [{}]]
        elapsed+=duration+(time.perf_counter()-start)*1000
        if len(upstream)>ROW_CAP:
            raise RuntimeError('Associated rows exceed budget')
    return upstream,elapsed


def merged(steps):
    root=steps[0]
    root_sql='SELECT '+','.join(quote(f) for f in root['fields'])+' FROM '+quote(root['table'])+' WHERE isDeleted=0 AND '+root['where']+root['tail']
    sql=' FROM ('+root_sql+') b0'
    columns={alias:'b0.'+quote(raw) for raw,alias in root['fields'].items()}
    for index,item in enumerate(steps[1:],1):
        raw,parent=item['match']
        table=f'b{index}'
        sql+=' LEFT JOIN '+quote(item['table'])+' '+table+' ON '+table+'.isDeleted=0 AND '+table+'.'+quote(raw)+'='+columns[parent]
        for field,alias in item['fields'].items():
            if alias in columns:
                raise RuntimeError('Duplicate output name')
            columns[alias]=table+'.'+quote(field)
    return 'SELECT '+','.join(expression+' AS '+quote(alias) for alias,expression in columns.items())+sql,root['params']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',default='.implementation/local-szh-benchmark-20260911')
    parser.add_argument('--check-guards',action='store_true')
    args=parser.parse_args()
    if args.check_guards:
        rejected = [(host,PORT,DATABASE) for host in ('192.168.1.224','192.168.6.16','example.com','0.0.0.0')]
        rejected += [(HOST,3308,DATABASE),(HOST,PORT,'yinhao_database_copy')]
        for host,port,database in rejected:
            try:
                require_local(host,port,database)
            except ValueError:
                continue
            raise AssertionError('Unapproved target accepted')
        print('All 6 unapproved target/port/schema cases rejected without connecting')
        return
    listener=local_listener()
    sys.path.insert(0,str(ROOT/'backend'))
    from app.config import settings
    from sqlalchemy.engine import make_url
    url=make_url(settings.DATABASE_URL)
    require_local(url.host,url.port or 3306,DATABASE)
    out=Path(args.output_dir).resolve()
    out.mkdir(parents=True,exist_ok=True)
    if (out/'results.json').exists():
        raise SystemExit('Use a new output directory; existing evidence is not overwritten')
    probe=Probe(out)
    probe.credentials=(url.username,url.password)
    probe.report['listener']=listener
    try:
        probe.connection=probe.connect()
        if probe.connection._sock.getpeername()[0]!=HOST:
            raise RuntimeError('Peer is not loopback')
        identity,_=probe.run('SELECT @@hostname hostname,@@version version,@@datadir datadir,DATABASE() db,CONNECTION_ID() connectionId',label='identity',kind='control')
        identity=identity[0]
        if identity['hostname'].lower()!=os.environ['COMPUTERNAME'].lower() or identity['db']!=DATABASE:
            raise RuntimeError('Server identity is not the verified local instance')
        probe.connection_id=identity['connectionId']
        probe.report['identity']=identity
        if shutil.disk_usage(identity['datadir']).free<3*1024**3:
            raise RuntimeError('Insufficient free database disk space')
        for sql in ('SET SESSION MAX_EXECUTION_TIME=1000',
                    'SET SESSION tmp_table_size=1048576,max_heap_table_size=1048576,sort_buffer_size=262144,join_buffer_size=262144',
                    'START TRANSACTION READ ONLY'):
            probe.run(sql,label='session-setup',kind='control')
        for name,title,steps in cases():
            # Reject views, FEDERATED engines and other possible remote storage.
            names=[s['table'] for s in steps]
            engines,_=probe.run('SELECT TABLE_NAME,ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN ('+','.join(['%s']*len(names))+')',(DATABASE,*names),label=name+'-storage',kind='control')
            if len(engines)!=len(set(names)) or any(r['ENGINE']!='InnoDB' for r in engines):
                raise RuntimeError('Only verified local InnoDB base tables are allowed')
            sql,params=merged(steps)
            probe.plan(sql,params,name+'-merged')
            (out/(name+'-merged.sql')).write_text(sql+';\n',encoding='utf-8')
            expected,_=separate(probe,name,steps,prepare=True)
            actual,_=probe.run(sql,params,label=name+'-merged')
            if not expected or canonical(actual)!=canonical(expected):
                raise RuntimeError('Empty sample or result mismatch')
            samples={'separate':[],'merged':[]}
            rng=random.Random(917)
            for repeat in range(REPEATS):
                order=list(samples)
                rng.shuffle(order)
                for method in order:
                    rows,duration=separate(probe,name,steps) if method=='separate' else probe.run(sql,params,label=name+'-merged')
                    if canonical(rows)!=canonical(expected):
                        raise RuntimeError('Result mismatch; stopped without retry')
                    samples[method].append(round(duration,4))
            result={'name':name,'title':title,'readQueries':len(steps),'mergedQueries':1,'rows':len(expected),
                    'parityPassed':True,'resultSha256':hashlib.sha256(dump(sorted(canonical(expected).items())).encode()).hexdigest(),
                    'timings':{key:{'medianMs':round(statistics.median(values),4),'samplesMs':values} for key,values in samples.items()}}
            probe.report['scenarios'].append(result)
            probe.save()
            print(json.dumps(result,ensure_ascii=False),flush=True)
        probe.run('ROLLBACK',label='finish-readonly',kind='control')
        probe.run('SELECT 1 healthy',label='final-health',kind='control')
        probe.report['status']='complete'
    except Exception as exc:
        probe.report.update(status='stopped',errorType=type(exc).__name__)
        raise SystemExit('Local test stopped: '+type(exc).__name__+'; no automatic retry') from None
    finally:
        if probe.connection:
            probe.connection.close()
        probe.report['finishedAt']=datetime.now(timezone.utc).isoformat()
        probe.save()


if __name__=='__main__':
    main()
