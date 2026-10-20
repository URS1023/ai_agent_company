"""新建临时SQLite验证复检/处置/维修和分类，不接触既有库或网络。"""
import csv
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.models import Membership, Organization, Role, User, Workspace
from agent_platform.core.settings import Settings
from agent_platform.core.tasks import TaskService
from agent_platform.modules.data.service import DataService
from agent_platform.modules.identity.models import ServiceIdentity
from agent_platform.modules.manufacturing import models as m
from agent_platform.modules.manufacturing.categories import CategoryService
from agent_platform.modules.manufacturing.maintenance_work import MaintenanceWorkService
from agent_platform.modules.manufacturing.quality_lifecycle import QualityService
from agent_platform.modules.manufacturing.quality_statistics import quality_statistics
from agent_platform.modules.manufacturing.schemas import AnalysisInput, InspectionImport
from agent_platform.modules.manufacturing.service import ManufacturingService, view
from agent_platform.modules.manufacturing.workflow import assignees, workspace_user


@pytest.fixture
def context(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'lifecycle.sqlite3').as_posix())
    Base.metadata.create_all(engine)
    settings = Settings(data_dir=tmp_path / 'new-imports')
    with Session(engine) as session:
        session.add_all([Organization(id='org', name='隔离工厂'), Workspace(id='ws', organization_id='org', name='质检车间'), Workspace(id='other', organization_id='org', name='另一车间')])
        role = Role(id='operator-role', workspace_id='ws', name='制造执行', permissions=['m11.*', 'm10.*', 'm06.*'])
        session.add(role)
        for user_id, admin in [('operator', True), ('reviewer', False), ('outsider', False), ('disabled', False)]:
            session.add(User(id=user_id, organization_id='org', username=user_id, display_name={'operator': '维修人员', 'reviewer': '复核人员'}.get(user_id, user_id), password_hash='unused-test-hash', is_admin=admin, disabled=user_id == 'disabled'))
            if user_id != 'outsider':
                session.add(Membership(workspace_id='ws', user_id=user_id, role_ids=[role.id]))
        session.flush()
        operator = Actor('operator', 'ws', frozenset({'*'}), ('admin',), 'org', True)
        reviewer = Actor('reviewer', 'ws', frozenset({'m11.*', 'm10.*', 'm06.*'}), (), 'org')
        yield session, operator, reviewer, settings
        session.rollback()
    engine.dispose()


def quality_setup(context, *, target_type='product', expected=3):
    session, actor, _, settings = context
    service, data = ManufacturingService(session, actor), DataService(session, actor, settings)
    target = service.create(m.Product if target_type == 'product' else m.Device, {'name': '检验对象'})
    metrics = [{'id': 'size', 'name': '尺寸', 'unit': 'mm', 'standard_min': '9.9', 'standard_max': '10.1'},
               {'id': 'protein', 'name': '蛋白质', 'category': 'component', 'component_name': '蛋白质', 'unit': '%', 'standard_min': '9', 'standard_max': '11'},
               {'id': 'surface', 'name': '外观', 'category': 'appearance'}]
    standard = service.create(m.InspectionTemplate, {'name': '固定三维标准', 'target_type': target_type, 'metrics': metrics})
    inspection = service.create_inspection({'name': '出厂首检', 'target_type': target_type, f'{target_type}_id': target['id'], 'template_id': standard['id'], 'template_revision': 1, 'expected_samples': expected})
    return service, data, inspection, standard


def import_quality(service, data, settings, inspection_id, revision, rows):
    content = StringIO()
    writer = csv.writer(content)
    writer.writerow(['sample_id', 'size', 'protein', 'image', 'defects'])
    writer.writerows((reference, value, '10', 'inspection/image/' + reference, '[]') for reference, value in rows)
    dataset = data.import_csv({'name': '本次受控质检记录', 'csv_text': content.getvalue()})
    query = data.execute_query({'datasource_id': dataset['datasource_id'], 'sql': f'SELECT * FROM "{dataset["table_name"]}"'})
    mapped = service.import_inspection(inspection_id, InspectionImport(revision=revision, query_id=query['id'], mappings=[
        {'metric_id': 'size', 'value_field': 'size', 'unit': 'mm'}, {'metric_id': 'protein', 'value_field': 'protein', 'unit': '%'},
        {'metric_id': 'surface', 'image_ref_field': 'image', 'defects_field': 'defects'}]), settings)
    return service.run_inspection(inspection_id, mapped['revision'])


def quality_action(service, row, action, **fields):
    return service.action(row['id'], {'revision': row['revision'], 'action': action, 'note': '记录本次真实处理过程', **fields})


def maintenance_action(service, row, action, **fields):
    return service.action(row['id'], {'revision': row['revision'], 'action': action, 'note': '记录本次真实维修过程', **fields})


def test_quality_reinspection_keeps_standard_and_first_pass_rate(context):
    session, actor, reviewer, settings = context
    service, data, inspection, standard = quality_setup(context)
    first = import_quality(service, data, settings, inspection['id'], 1, [('A', '10'), ('B', '10.2')])
    reviewed = service.review(first['id'], {'revision': first['revision'], 'outcome': 'failed', 'note': '隔离尺寸超差与缺失样本'})['inspection']
    quality = QualityService(session, actor)
    case = quality.create_case({'inspection_id': first['id'], 'run_id': first['result']['run_id'], 'revision': reviewed['revision'], 'name': '尺寸异常处置', 'note': '记录超差并隔离现场产品'})
    case = quality_action(quality, case, 'contain')
    case = quality_action(quality, case, 'assign', assignee_id=actor.user_id)
    case = quality_action(quality, case, 'start')
    case = quality_action(quality, case, 'submit', disposition='rework', root_cause='工装偏移', corrective_action='重新调整工装并补齐样本')
    service.change(m.InspectionTemplate, standard['id'], {'name': '后续变更标准', 'metrics': [{'id': 'other', 'name': '别的指标', 'standard_max': '99'}]}, 1)
    current = service.get(m.Inspection, first['id'])
    child = quality.create_reinspection(first['id'], {'revision': current.revision, 'run_id': first['result']['run_id'], 'name': '返工复检', 'reason': '返工后复核超差与缺失样本', 'case_id': case['id']})
    assert child['standard_snapshot']['name'] == '固定三维标准' and child['template_revision'] == 1
    assert child['expected_samples'] == 2
    samples = session.scalars(select(m.InspectionSample).where(m.InspectionSample.inspection_id == child['id'])).all()
    assert [sample.external_ref for sample in samples] == ['B']
    assert not session.scalars(select(m.SampleEvidence).where(m.SampleEvidence.inspection_id == child['id'])).all()
    passed = import_quality(service, data, settings, child['id'], child['revision'], [('B', '10.0'), ('C', '10.0')])
    service.review(child['id'], {'revision': passed['revision'], 'outcome': 'passed', 'note': '复检全部指标和样本均合格'})
    case = quality.case_view(quality.case(case['id']))
    case = quality_action(quality, case, 'submit_reinspection')
    with pytest.raises(DomainError, match='另一名复核人') as self_review:
        quality_action(quality, case, 'verify')
    assert self_review.value.code == 'independent_reviewer_required'
    closed = quality_action(QualityService(session, reviewer), case, 'verify')
    assert closed['status'] == 'closed' and closed['closed_by_name'] == '复核人员'
    assert TaskService(session, reviewer).get(closed['task_id'])['status'] == 'succeeded'
    stats = quality_statistics(session, reviewer, target_type='product')
    assert stats['first_inspection']['pass_rate'] == '33.33'
    assert stats['first_inspection']['missing_samples'] == 1
    assert stats['reinspection']['pass_rate'] == '100.00' and stats['reinspection']['task_count'] == 1
    assert {row['category'] for row in stats['dimensions']} == {'numeric', 'component', 'appearance'}
    assert quality.lineage(child['id'])['total'] == 2
    assert service.get(m.Inspection, first['id']).result == first['result']
    reopened = quality_action(QualityService(session, reviewer), closed, 'reopen')
    assert reopened['task_id'] != closed['task_id'] and reopened['corrective_action'] == ''
    previous = next(event for event in reopened['events'] if event['action'] == 'submit')
    assert previous['evidence']['state_snapshot']['corrective_action'] == '重新调整工装并补齐样本'


def test_quality_missing_sample_selection_version_and_workspace(context):
    session, actor, _, settings = context
    service, data, inspection, _ = quality_setup(context, target_type='device', expected=1)
    first = import_quality(service, data, settings, inspection['id'], 1, [('A', '10.2')])
    reviewed = service.review(first['id'], {'revision': first['revision'], 'outcome': 'failed', 'note': '超差隔离等待处置'})['inspection']
    quality = QualityService(session, actor)
    with pytest.raises(DomainError) as error:
        quality.create_reinspection(first['id'], {'revision': reviewed['revision'], 'run_id': first['result']['run_id'], 'name': '错样本复检', 'reason': '尝试不属于原任务的样本', 'sample_ids': ['unknown']})
    assert error.value.code == 'reinspection_samples_invalid'
    child = quality.create_reinspection(first['id'], {'revision': reviewed['revision'], 'run_id': first['result']['run_id'], 'name': '设备复检', 'reason': '现场校准后执行复检'})
    with pytest.raises(DomainError) as error:
        quality.create_reinspection(first['id'], {'revision': reviewed['revision'], 'run_id': first['result']['run_id'], 'name': '旧版本复检', 'reason': '重复提交过期版本'})
    assert error.value.code == 'revision_conflict'
    with pytest.raises(DomainError) as error:
        QualityService(session, Actor('outsider', 'other', frozenset({'*'}))).lineage(child['id'])
    assert error.value.status == 404
    assert {row['id'] for row in assignees(session, actor, 'm11.write')['items']} == {'operator', 'reviewer'}


def incident_setup(context):
    session, actor, _, _ = context
    service = ManufacturingService(session, actor)
    device = service.create(m.Device, {'name': '维修循环泵'})
    rule = service.create(m.AlertRule, {'name': '电机温度超限', 'device_id': device['id'], 'operator': 'gt', 'threshold': '80.00000000000000000001', 'unit': '℃', 'dedupe_minutes': 60})
    alert = service.evaluate_alert(rule['id'], {'value': '90', 'unit': '℃', 'observed_at': datetime.now(timezone.utc).isoformat(), 'create_incident': True})
    work = MaintenanceWorkService(session, actor)
    order = work.create_order('incident', alert['incident']['id'], {'revision': alert['incident']['revision'], 'name': '温度异常维修', 'note': '检查温升和冷却回路状态'})
    order = maintenance_action(work, order, 'assign', assignee_id=actor.user_id)
    order = maintenance_action(work, order, 'start')
    return service, work, rule, order


def query_temperature(context, stamp, value='79', unit='℃'):
    session, actor, _, settings = context
    data = DataService(session, actor, settings)
    dataset = data.import_csv({'name': '维修后本地复测记录', 'csv_text': f'timestamp,value,unit\n{stamp},{value},{unit}\n'})
    return data.execute_query({'datasource_id': dataset['datasource_id'], 'sql': f'SELECT * FROM "{dataset["table_name"]}"'})


def test_maintenance_assignment_fresh_evidence_independent_review_and_dedup(context):
    session, actor, reviewer, _ = context
    service, work, rule, order = incident_setup(context)
    old = query_temperature(context, datetime.now(timezone.utc).isoformat())
    order = maintenance_action(work, order, 'repair', root_cause='冷却回路过滤器堵塞')
    verification = {'verification_query_id': old['id'], 'value_field': 'value', 'time_field': 'timestamp', 'unit_field': 'unit'}
    with pytest.raises(DomainError) as error:
        maintenance_action(work, order, 'verify', **verification)
    assert error.value.code == 'independent_reviewer_required'
    review = MaintenanceWorkService(session, reviewer)
    with pytest.raises(DomainError) as error:
        maintenance_action(review, order, 'verify', **verification)
    assert error.value.code == 'verification_source_not_fresh'
    repeated = service.evaluate_alert(rule['id'], {'value': '91', 'unit': '℃', 'observed_at': datetime.now(timezone.utc).isoformat(), 'create_incident': True})
    assert repeated['deduplicated'] and repeated['incident']['id'] == order['incident_id']
    service.change(m.AlertRule, rule['id'], {'threshold': '999'}, rule['revision'])
    still_bad = query_temperature(context, datetime.now(timezone.utc).isoformat(), value='81')
    with pytest.raises(DomainError) as error:
        maintenance_action(review, order, 'verify', **{**verification, 'verification_query_id': still_bad['id']})
    assert error.value.code == 'verification_not_recovered'
    good = query_temperature(context, datetime.now(timezone.utc).isoformat())
    closed = maintenance_action(review, order, 'verify', **{**verification, 'verification_query_id': good['id']})
    assert closed['status'] == 'resolved' and closed['verified_by_name'] == '复核人员'
    assert closed['verification_snapshot']['threshold'] == '80.00000000000000000001'
    assert service.get(m.Incident, order['incident_id']).status == 'resolved'
    assert TaskService(session, reviewer).get(order['task_id'])['status'] == 'succeeded'
    assert [event['action'] for event in closed['events']] == ['created', 'assign', 'start', 'repair', 'verify']
    reopened = maintenance_action(review, closed, 'reopen')
    assert reopened['repaired_at'] is None and reopened['repair_note'] == ''
    assert next(event for event in reopened['events'] if event['action'] == 'repair')['evidence']['state_snapshot']['root_cause'] == '冷却回路过滤器堵塞'


def test_maintenance_rejects_wrong_assignee_units_and_stale_observations(context):
    session, actor, reviewer, _ = context
    _, work, _, order = incident_setup(context)
    with pytest.raises(DomainError) as error:
        maintenance_action(work, order, 'assign', assignee_id='outsider')
    assert error.value.code == 'assignee_unavailable'
    order = maintenance_action(work, order, 'repair', root_cause='散热器清洁不及时')
    review = MaintenanceWorkService(session, reviewer)
    for stamp, unit, code in [(datetime.now(timezone.utc).isoformat(), 'F', 'unit_mismatch'), ((datetime.now(timezone.utc)-timedelta(days=1)).isoformat(), '℃', 'verification_observation_old')]:
        query = query_temperature(context, stamp, unit=unit)
        with pytest.raises(DomainError) as error:
            maintenance_action(review, order, 'verify', verification_query_id=query['id'], value_field='value', time_field='timestamp', unit_field='unit')
        assert error.value.code == code


def test_category_tree_binding_cas_and_restore(context):
    session, actor, _, _ = context
    service = CategoryService(session, actor)
    root = service.save({'name': '食品', 'code': 'food', 'target_type': 'product'})
    child = service.save({'name': '乳制品', 'target_type': 'product', 'parent_id': root['id']})
    with pytest.raises(DomainError) as error:
        service.save({'name': '食品', 'target_type': 'product', 'parent_id': child['id'], 'revision': root['revision']}, root['id'])
    assert error.value.code == 'category_cycle'
    product = service.create(m.Product, {'name': '牛奶'})
    binding = service.bind({'category_id': child['id'], 'resource_type': 'product', 'resource_id': product['id']})
    assert service.resources('product', 'product', child['id'])['items'][0]['category_name'] == '乳制品'
    with pytest.raises(DomainError) as error:
        service.remove(child['id'], child['revision'])
    assert error.value.code == 'category_in_use'
    unbound = service.change(m.QualityCategoryBinding, binding['id'], {'archived': True}, binding['revision'])
    resource = service.resources('product', 'product')['items'][0]
    assert resource['category_id'] is None and resource['binding_revision'] == unbound['revision']
    restored = service.bind({'category_id': root['id'], 'resource_type': 'product', 'resource_id': product['id'], 'revision': resource['binding_revision']})
    assert restored['revision'] == unbound['revision']+1 and restored['archived'] is False
    device = service.create(m.Device, {'name': '制冷设备'})
    with pytest.raises(DomainError) as error:
        service.bind({'category_id': root['id'], 'resource_type': 'device', 'resource_id': device['id']})
    assert error.value.code == 'category_target_mismatch'


def test_analysis_work_order_needs_new_normal_analysis_same_criteria(context):
    session, actor, reviewer, settings = context
    service = ManufacturingService(session, actor)
    device = service.create(m.Device, {'name': '同工况设备'})
    origin = datetime.now(timezone.utc) - timedelta(hours=1)
    def analyze_rows(recent, **options):
        rows = [{'timestamp': (origin+timedelta(minutes=i)).isoformat(), 'period': 'baseline' if i<5 else 'recent', 'load': 50, 'value': 10 if i<5 else recent} for i in range(10)]
        return service.create_analysis(AnalysisInput(device_id=device['id'], rows=rows, **options), settings)
    bad = analyze_rows(20)
    work = MaintenanceWorkService(session, actor)
    order = work.create_order('analysis', bad['id'], {'revision': bad['revision'], 'name': '工况异常维修', 'note': '检查同工况温升异常原因'})
    order = maintenance_action(work, order, 'assign', assignee_id=actor.user_id)
    order = maintenance_action(work, order, 'start')
    old_normal = analyze_rows(10)
    order = maintenance_action(work, order, 'repair', root_cause='过滤器堵塞引起温升')
    review = MaintenanceWorkService(session, reviewer)
    with pytest.raises(DomainError) as error:
        maintenance_action(review, order, 'verify', verification_analysis_id=old_normal['id'])
    assert error.value.code == 'verification_source_not_fresh'
    changed = analyze_rows(10, change_threshold=0.99)
    with pytest.raises(DomainError) as error:
        maintenance_action(review, order, 'verify', verification_analysis_id=changed['id'])
    assert error.value.code == 'verification_criteria_changed'
    good = analyze_rows(10)
    closed = maintenance_action(review, order, 'verify', verification_analysis_id=good['id'])
    assert closed['status'] == 'resolved' and closed['verification_snapshot']['result']['status'] == 'normal'


def test_statistics_date_scope_applies_before_task_budget(context):
    session, actor, _, _ = context
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    # 只插入无证据的计划任务验证筛选顺序，不产生大规模源查询或运行。
    session.add_all(m.Inspection(workspace_id='ws', name='历史计划', target_type='device', template_id='unused', template_revision=1, standard_snapshot={'metrics': []}, expected_samples=1, created_at=old) for _ in range(2001))
    session.add(m.Inspection(workspace_id='ws', name='当日计划', target_type='device', template_id='unused', template_revision=1, standard_snapshot={'metrics': []}, expected_samples=1))
    session.flush()
    now = datetime.now(timezone.utc)
    result = quality_statistics(session, actor, start_at=(now-timedelta(hours=1)).isoformat(), end_at=(now+timedelta(hours=1)).isoformat())
    assert result['pending_tasks'] == 1 and result['first_inspection']['denominator'] == 0


def manual_quality_result(context, service, inspection_id, reference, *, passed=False):
    sample = service.add_sample(inspection_id, {'external_ref': reference})
    evidence = service.add_evidence(inspection_id, {'revision': sample['inspection_revision'], 'sample_id': sample['id'], 'observations': [
        {'metric_id': 'size', 'value': '10' if passed else '11', 'unit': 'mm'},
        {'metric_id': 'protein', 'value': '10', 'unit': '%'},
        {'metric_id': 'surface', 'image_ref': 'manual/image', 'defects': []}]})
    result = service.run_inspection(inspection_id, evidence['inspection_revision'])
    service.review(inspection_id, {'revision': result['revision'], 'outcome': result['outcome'], 'note': '现场补录证据复核已完成'})
    return service.get(m.Inspection, inspection_id)


def test_failed_reinspection_can_be_replaced_without_erasing_history(context):
    session, actor, _, _ = context
    service, _, inspection, _ = quality_setup(context, expected=1)
    parent = manual_quality_result(context, service, inspection['id'], 'A')
    quality = QualityService(session, actor)
    case = quality.create_case({'inspection_id': parent.id, 'run_id': parent.result['run_id'], 'revision': parent.revision, 'name': '返工处置', 'note': '超差产品返工并计划复检'})
    case = quality_action(quality, case, 'assign', assignee_id=actor.user_id)
    case = quality_action(quality, case, 'start')
    case = quality_action(quality, case, 'submit', disposition='rework', root_cause='定位工装松动', corrective_action='调整定位并准备再次检验')
    def create_child():
        return quality.create_reinspection(parent.id, {'revision': parent.revision, 'run_id': parent.result['run_id'], 'name': '后续复检', 'reason': '再次返工后重新检验', 'case_id': case['id']})
    first = create_child()
    # 子任务已自动建立原样本，直接补录新证据，不能复制原始不合格测量。
    sample = session.scalar(select(m.InspectionSample).where(m.InspectionSample.inspection_id == first['id']))
    evidence = service.add_evidence(first['id'], {'revision': first['revision'], 'sample_id': sample.id, 'observations': []})
    failed = service.run_inspection(first['id'], evidence['inspection_revision'])
    service.review(first['id'], {'revision': failed['revision'], 'outcome': 'failed', 'note': '复检证据不足重新安排返工'})
    second = create_child()
    assert quality.case(case['id']).reinspection_id == second['id']
    assert quality.lineage(parent.id)['total'] == 3
    assert quality.inspection(first['id']).status == 'reviewed'
    with pytest.raises(DomainError) as error:
        service.archive(m.Inspection, parent.id, parent.revision)
    assert error.value.code == 'inspection_history_in_use'


def test_revoked_source_hidden_from_case_stats_and_inspection_counts(context, monkeypatch):
    session, actor, _, _ = context
    service, _, inspection, _ = quality_setup(context, expected=1)
    parent = manual_quality_result(context, service, inspection['id'], 'A')
    quality = QualityService(session, actor)
    case = quality.create_case({'inspection_id': parent.id, 'run_id': parent.result['run_id'], 'revision': parent.revision, 'name': '需要字段权限的处置', 'note': '保留原始来源授权后处置'})
    changed = deepcopy(parent.result)
    changed['samples'][0]['metrics'][0]['provenance'] = {'query_id': 'revoked-source'}
    parent.result = deepcopy(changed)
    session.get(m.InspectionRun, changed['run_id']).result = deepcopy(changed)
    session.flush()
    def denied(*args, **kwargs):
        raise DomainError(403, 'permission_denied', '原字段授权已撤销')
    monkeypatch.setattr(DataService, 'get_query', denied)
    with pytest.raises(DomainError) as error:
        quality.case(case['id'])
    assert error.value.status == 403
    assert quality.cases() == {'items': [], 'total': 0}
    assert service.list(m.Inspection) == {'items': [], 'total': 0}
    assert quality_statistics(session, actor)['first_inspection']['task_count'] == 0


def test_service_accounts_are_not_human_assignees_even_with_same_role(context):
    session, actor, _, _ = context
    session.add(User(id='robot', organization_id='org', username='normal-looking-name', display_name='自动运行', password_hash='unused'))
    session.add(Membership(workspace_id='ws', user_id='robot', role_ids=['operator-role']))
    session.add(ServiceIdentity(workspace_id='ws', user_id='robot', role_id='operator-role', name='流程调用', permissions=['m10.write','m11.write'], created_by=actor.user_id))
    session.flush()
    for permission in ('m10.write', 'm11.write'):
        assert 'robot' not in {row['id'] for row in assignees(session, actor, permission)['items']}
        with pytest.raises(DomainError) as error:
            workspace_user(session, actor, 'robot', permission)
        assert error.value.code == 'assignee_unavailable'
