"""Bounded local-only, read-only evidence for the quality catalogue review.

One connection and at most one business SELECT per invocation; inspect its
EXPLAIN before execution. Never opens the deployment platform database.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.implementation/local_quality_catalog'
STOP = ROOT / '.implementation/local_szh_acceptance/STOP_AFTER_DATABASE_ERROR.json'
BUSINESS = {
    'products': ("SELECT productInfoId id,c_productName name,c_productcode code "
                 "FROM t_productinfo WHERE isDeleted=0 ORDER BY autoId LIMIT 51", 50, 100),
    'templates': ("SELECT qualityInspectionTemplateId id,c_templateName name,c_product productId,"
                  "c_inspectionType inspectionType FROM t_qualityinspectiontemplate "
                  "WHERE isDeleted=0 ORDER BY autoId LIMIT 31", 30, 100),
    'standards': ("SELECT d.qITemplateDetailsId id,d.mdQualityInspectionTemplateId templateId,"
                  "d.c_xuanzezhijianxiangmu projectId,p.c_projectName name,"
                  "p.c_inspectionStandard standard,d.c_judgeRule judgeRule,"
                  "d.c_standardMin minimum,d.c_standardMax maximum "
                  "FROM t_qitemplatedetails d LEFT JOIN t_qualityinspectionproject p "
                  "ON p.qualityInspectionProjectId=d.c_xuanzezhijianxiangmu AND p.isDeleted=0 "
                  "WHERE d.isDeleted=0 ORDER BY d.autoId LIMIT 201", 200, 300),
    'form_groups': ("SELECT c_qualityInspection category,c_qualityInspectionTemplate templateId,COUNT(*) formCount "
                    "FROM t_incomingmaterialqcform WHERE isDeleted=0 "
                    "GROUP BY c_qualityInspection,c_qualityInspectionTemplate "
                    "ORDER BY c_qualityInspection,c_qualityInspectionTemplate LIMIT 51", 50, 5000),
}


def main() -> int:
    """按阶段采集质量目录的只读证据并写入 .implementation/local_quality_catalog/<阶段>-<时间>.json。

    参数：--stage schema 只读元数据（表/列/索引）；其余四个阶段（products/templates/standards/form_groups）
    先 EXPLAIN 再执行一条业务 SELECT。若 EXPLAIN 估算行数超过声明预算，直接判定失败且不执行该查询。
    前置条件：仅限本机 127.0.0.1:3306 的 szh_new（先跑 scripts/verify_local_mysql.ps1 核对身份）；
    预算为 1 条业务连接、最多 12 条命令、3 秒执行上限、总时长 20 秒。
    副作用：只读（START TRANSACTION READ ONLY + MAX_EXECUTION_TIME），只写本地报告文件；
    出现超时/断连类错误会写入 STOP_AFTER_DATABASE_ERROR.json 停止标记，后续运行必须先人工排查。
    返回值：0 表示该阶段通过，非 0 表示失败（失败报告不会被成功运行覆盖）。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=['schema', *BUSINESS], default='schema')
    args = parser.parse_args()
    if STOP.exists():
        raise SystemExit('Database stop marker requires investigation and explicit continuation authorization')
    OUT.mkdir(parents=True, exist_ok=True)
    verification = subprocess.run(['pwsh', '-NoProfile', '-File', str(ROOT / 'scripts/verify_local_mysql.ps1')],
                                  capture_output=True, text=True, check=True, timeout=10)
    target = json.loads(verification.stdout)
    sys.path.insert(0, str(ROOT / 'backend'))
    from app.config import settings
    import pymysql
    if settings.MYSQL_HOST not in {'127.0.0.1', 'localhost', '::1'} or int(settings.MYSQL_PORT) != 3306:
        raise SystemExit('Configured credentials are not associated with the verified local server')
    report = {'startedAt': datetime.now(timezone.utc).isoformat(), 'stage': args.stage, 'target': target,
              'budget': {'connections': 1, 'diagnosticConnections': 1, 'commands': 12, 'businessSelects': 1,
                         'queryTimeoutSeconds': 3, 'totalSeconds': 20, 'maximumRows': 300},
              'connections': 0, 'commands': [], 'businessSelects': 0, 'status': 'started'}
    connection = None
    connection_id = None
    started = time.monotonic()

    def connect():
        return pymysql.connect(host='127.0.0.1', port=3306, database='szh_new',
                               user=settings.MYSQL_USER, password=settings.MYSQL_PASSWORD,
                               connect_timeout=3, read_timeout=3, write_timeout=3, local_infile=False,
                               autocommit=False, cursorclass=pymysql.cursors.DictCursor)

    def query(label, sql, parameters=(), *, maximum=300):
        if len(report['commands']) >= 12 or time.monotonic() - started >= 20:
            raise TimeoutError('Total review budget exhausted; stop subsequent reads')
        item = {'label': label, 'verb': sql.split()[0], 'status': 'started'}
        report['commands'].append(item)
        before = time.monotonic()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                rows = cursor.fetchmany(maximum + 1) if cursor.description else []
                if len(rows) > maximum:
                    raise ValueError('Result exceeds the bounded review scope')
                item.update(status='ok', rows=len(rows))
                return rows
        except Exception:
            item['status'] = 'failed'
            raise
        finally:
            item['milliseconds'] = round((time.monotonic() - before) * 1000, 2)

    try:
        report['connections'] += 1
        connection = connect()
        connection_id = connection.thread_id()
        query('timeout', 'SET SESSION MAX_EXECUTION_TIME=3000')
        query('readonly', 'START TRANSACTION READ ONLY')
        identity = query('identity', 'SELECT DATABASE() db, @@hostname hostname, @@port port, @@version version', maximum=1)[0]
        assert identity['db'] == 'szh_new' and identity['port'] == 3306
        assert identity['hostname'].casefold() == target['machine'].casefold()
        report['server'] = identity
        if args.stage == 'schema':
            report['tables'] = query('tables', "SELECT TABLE_NAME,TABLE_ROWS,ENGINE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND (TABLE_NAME LIKE %s OR TABLE_NAME IN "
                "('t_qualityinspectiontemplate','t_qitemplatedetails','t_qualityinspectionproject','t_incomingmaterialqcform')) "
                "ORDER BY TABLE_NAME LIMIT 51", ('szh_new', '%product%'), maximum=50)
            relevant_tables = {'t_productinfo', 't_qualityinspectiontemplate',
                               't_qitemplatedetails', 't_qualityinspectionproject', 't_incomingmaterialqcform'}
            table_names = [row['TABLE_NAME'] for row in report['tables'] if row['TABLE_NAME'] in relevant_tables]
            placeholders = ','.join(['%s'] * len(table_names))
            if not table_names:
                raise ValueError('No quality/product tables found; do not guess another database')
            report['columns'] = query('columns', 'SELECT TABLE_NAME,COLUMN_NAME,DATA_TYPE,COLUMN_KEY,COLUMN_COMMENT '
                'FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN (' + placeholders + ') '
                'ORDER BY TABLE_NAME,ORDINAL_POSITION LIMIT 301', ('szh_new', *table_names))
            report['indexes'] = query('indexes', 'SELECT TABLE_NAME,INDEX_NAME,COLUMN_NAME,SEQ_IN_INDEX '
                'FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN (' + placeholders + ') '
                'ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX LIMIT 301', ('szh_new', *table_names))
        else:
            sql, maximum, plan_budget = BUSINESS[args.stage]
            plan = query('plan', 'EXPLAIN ' + sql)
            report['sql'], report['plan'] = sql, plan
            if not plan or any((row.get('rows') or 0) > plan_budget for row in plan):
                raise ValueError('Plan exceeds declared row budget; business read not executed')
            report['businessSelects'] += 1
            report['rows'] = query(args.stage, sql, maximum=maximum)
        report['status'] = 'passed'
    except Exception as error:
        code = error.args[0] if error.args and isinstance(error.args[0], int) else None
        report.update(status='failed', failure={'type': type(error).__name__, 'code': code})
        if code in {2006, 2013, 2014, 3024, 1205} or isinstance(error, (TimeoutError, ConnectionError)) or 'timed out' in str(error).lower():
            STOP.parent.mkdir(parents=True, exist_ok=True)
            STOP.write_text(json.dumps({'at': report['startedAt'], 'failure': report['failure'],
                                        'connectionIds': [connection_id]}, indent=2), encoding='utf-8')
    finally:
        if connection is not None:
            connection.close()
        if report['status'] == 'failed' and connection_id:
            try:
                with connect() as diagnostic:
                    with diagnostic.cursor() as cursor:
                        cursor.execute('SELECT ID,COMMAND,STATE FROM information_schema.PROCESSLIST WHERE ID=%s', (connection_id,))
                        remaining = cursor.fetchall()
                        report['diagnostic'] = {'connections': 1, 'queries': 1, 'remaining': remaining}
                        if any(row['COMMAND'] != 'Sleep' for row in remaining):
                            cursor.execute('KILL QUERY ' + str(int(connection_id)))
                            cursor.execute('SELECT ID,COMMAND,STATE FROM information_schema.PROCESSLIST WHERE ID=%s', (connection_id,))
                            report['diagnostic'].update(queries=3, remaining=cursor.fetchall())
            except Exception as error:
                report['diagnostic'] = {'errorType': type(error).__name__, 'remainingUnverified': True}
                STOP.parent.mkdir(parents=True, exist_ok=True)
                STOP.write_text(json.dumps(report['diagnostic']), encoding='utf-8')
        report['elapsedSeconds'] = round(time.monotonic() - started, 3)
        # Preserve every attempt; no successful run can overwrite a failure.
        output = OUT / (args.stage + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json')
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
        print(json.dumps({'status': report['status'], 'report': str(output), 'connections': report['connections'],
                          'commands': len(report['commands']), 'businessSelects': report['businessSelects']}, ensure_ascii=False))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
