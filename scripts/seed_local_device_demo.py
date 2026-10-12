"""Create only two separately named local demo tables and 150 labelled rows.

The operator must inspect the generated ledger before running the platform publisher.
No existing business table, data-source binding or schedule is changed.
"""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
from configurations.device_operations.fixtures import sample_rows
from configurations.device_operations.workflow import BATCH, TABLES, SQL
OUT = ROOT/'.implementation/device_operating_20260922'
STOP = ROOT/'.implementation/local_szh_acceptance/STOP_AFTER_DATABASE_ERROR.json'
assert not STOP.exists(), 'Database stop marker must be resolved first'
assert not (OUT/'demo_seed.json').exists(), 'Seed already attempted; inspect ledger before any further action'
verification = subprocess.run(['pwsh','-NoProfile','-File',str(ROOT/'scripts/verify_local_mysql.ps1')],capture_output=True,text=True,check=True,timeout=10)
target = json.loads(verification.stdout)
from app.config import settings
import pymysql
assert settings.MYSQL_HOST in {'localhost','127.0.0.1','::1'} and int(settings.MYSQL_PORT)==3306
started=time.monotonic()
ledger={'target':target,'batch':BATCH,'scope':TABLES,'budget':{'connections':1,'commands':24,'rowsInserted':150,'businessQueries':1,'querySeconds':3,'totalSeconds':30},'commands':[],'status':'started'}
connection=None
try:
    connection=pymysql.connect(host='127.0.0.1',port=3306,database='szh_new',user=settings.MYSQL_USER,password=settings.MYSQL_PASSWORD,
        connect_timeout=3,read_timeout=3,write_timeout=3,autocommit=False,local_infile=False,cursorclass=pymysql.cursors.DictCursor)
    ledger['connectionId']=connection.thread_id()
    def query(label,sql,parameters=(),many=False,maximum=300):
        assert len(ledger['commands'])<24 and time.monotonic()-started<30,'Budget exhausted'
        entry={'label':label,'verb':sql.split()[0]};ledger['commands'].append(entry)
        with connection.cursor() as cursor:
            before=time.monotonic()
            cursor.executemany(sql,parameters) if many else cursor.execute(sql,parameters)
            rows=cursor.fetchmany(maximum+1) if cursor.description else []
            assert len(rows)<=maximum
            entry.update(milliseconds=round((time.monotonic()-before)*1000,2),rows=len(rows),affectedRows=cursor.rowcount)
            return rows
    query('query-timeout','SET SESSION MAX_EXECUTION_TIME=3000')
    query('lock-timeout','SET SESSION lock_wait_timeout=3')
    query('timezone',"SET SESSION time_zone='+00:00'")
    identity=query('identity','SELECT DATABASE() db,@@hostname hostname,@@port port')[0]
    assert identity['db']=='szh_new' and identity['port']==3306 and identity['hostname'].casefold()==target['machine'].casefold()
    existing=query('existing-demo-tables',"SELECT TABLE_NAME,TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA='szh_new' AND TABLE_NAME IN ('demo_device_assets','demo_device_telemetry')")
    assert not existing,'Existing tables require explicit review; no overwrite attempted'
    query('create-demo-assets', '''CREATE TABLE demo_device_assets (
        device_id VARCHAR(32) NOT NULL PRIMARY KEY COMMENT '演示设备编号，与真实台账无关联',
        device_name VARCHAR(100) NOT NULL COMMENT '演示设备名称，前缀为演示-',
        temperature_limit_c DECIMAL(8,2) NOT NULL COMMENT '示例温度上限，摄氏度；不是生产标准',
        rpm_limit DECIMAL(10,2) NOT NULL COMMENT '示例转速上限，rpm；不是生产标准',
        is_demo TINYINT NOT NULL DEFAULT 1 COMMENT '1=明确标记的演示数据',
        sample_batch VARCHAR(40) NOT NULL COMMENT '演示样例批次',
        CHECK (is_demo=1)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='独立设备运行演示台账；不代表真实生产设备' ''')
    query('create-demo-telemetry', '''CREATE TABLE demo_device_telemetry (
        sample_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        device_id VARCHAR(32) NOT NULL COMMENT '关联 demo_device_assets 演示设备编号',
        observed_at DATETIME NOT NULL COMMENT 'UTC 采样时间；固定历史演示样例，非实时采集',
        state VARCHAR(20) NOT NULL COMMENT 'running运行/stopped停机/fault故障/offline离线',
        boot_at DATETIME NULL COMMENT 'UTC 本次或最近一次开机时间；未知为空',
        uptime_seconds BIGINT NULL COMMENT '采样时本次累计运行秒数；停机保留、离线未知为空',
        rpm DECIMAL(10,2) NULL COMMENT '转速，rpm；未知为空，不按0填补',
        temperature_c DECIMAL(8,2) NULL COMMENT '采样温度，摄氏度；未知为空',
        is_demo TINYINT NOT NULL DEFAULT 1 COMMENT '1=演示数据，不作为生产预警依据',
        sample_batch VARCHAR(40) NOT NULL COMMENT '演示样例批次',
        UNIQUE KEY uq_demo_sample (sample_batch,device_id,observed_at),
        CONSTRAINT fk_demo_device FOREIGN KEY (device_id) REFERENCES demo_device_assets(device_id),
        CHECK (is_demo=1)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='独立设备运行演示时序；示例数据、非生产采集' ''')
    assets,samples=sample_rows()
    query('insert-demo-assets','INSERT INTO demo_device_assets (device_id,device_name,temperature_limit_c,rpm_limit,is_demo,sample_batch) VALUES (%s,%s,%s,%s,%s,%s)',assets,many=True)
    query('insert-demo-samples','INSERT INTO demo_device_telemetry (device_id,observed_at,state,boot_at,uptime_seconds,rpm,temperature_c,is_demo,sample_batch) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',samples,many=True)
    connection.commit()
    statement=SQL.replace(':sample_batch','%s')+' LIMIT 201'
    plan=query('sample-plan','EXPLAIN '+statement,(BATCH,))
    assert plan and all((row.get('rows') or 0)<=300 for row in plan),'Plan exceeds scope'
    ledger['plan']=plan
    rows=query('verify-demo-read',statement,(BATCH,),maximum=200)
    assert len(rows)==144 and len({row['device_id'] for row in rows})==6
    columns=query('actual-demo-columns',"SELECT TABLE_NAME,COLUMN_NAME,DATA_TYPE,COLUMN_KEY,COLUMN_COMMENT,IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='szh_new' AND TABLE_NAME IN ('demo_device_assets','demo_device_telemetry') ORDER BY TABLE_NAME,ORDINAL_POSITION")
    tables=[]
    for name in TABLES:
        tables.append({'tableName':name,'tableComment':'独立设备运行演示数据；非生产采集','tableType':'TABLE','rowCount':6 if name==TABLES[0] else 144,'rowCountEstimated':False,
            'columns':[{'columnName':c['COLUMN_NAME'],'dataType':c['DATA_TYPE'],'comment':c['COLUMN_COMMENT'],'nullable':c['IS_NULLABLE']=='YES','primaryKey':c['COLUMN_KEY']=='PRI'} for c in columns if c['TABLE_NAME']==name],
            'foreignKeys':[] if name==TABLES[0] else [{'columns':['device_id'],'referredTable':TABLES[0],'referredColumns':['device_id']}]})
    (OUT/'demo_metadata.json').write_text(json.dumps(tables,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'demo_readback.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    ledger.update(status='passed',assets=6,samples=144)
except Exception as exc:
    ledger.update(status='failed',errorType=type(exc).__name__,errorCode=exc.args[0] if exc.args and isinstance(exc.args[0],int) else None)
    if connection:connection.rollback()
    code=ledger['errorCode']
    if code in {1205,2006,2013,2014,3024} or isinstance(exc,(TimeoutError,ConnectionError)) or 'timed out' in str(exc).lower():
        STOP.write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'connectionIds':[ledger.get('connectionId')],'failure':ledger['errorType']}),encoding='utf-8')
    raise
finally:
    if connection:connection.close()
    ledger['elapsedSeconds']=round(time.monotonic()-started,3)
    (OUT/'demo_seed.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'status':ledger['status'],'assets':ledger.get('assets'),'samples':ledger.get('samples'),'commands':len(ledger['commands']),'elapsedSeconds':ledger['elapsedSeconds']},ensure_ascii=False))
