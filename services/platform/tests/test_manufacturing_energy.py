"""纯能耗计算与新临时SQLite来源测试，不访问既有业务库、网络、模型或设备。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_platform.core.context import Actor
from agent_platform.core.database import Base
from agent_platform.core.errors import DomainError
from agent_platform.core.settings import Settings
from agent_platform.core.tasks import TaskService
from agent_platform.modules.data.service import DataService
from agent_platform.modules.data.sql_safety import SqlFieldValidationError
from agent_platform.modules.manufacturing import models as m
from agent_platform.modules.manufacturing.energy.analysis import analyze_energy
from agent_platform.modules.manufacturing.energy.models import EnergyAnalysis
from agent_platform.modules.manufacturing.energy.schemas import EnergyInput
from agent_platform.modules.manufacturing.energy.service import EnergyService
from agent_platform.modules.manufacturing.service import ManufacturingService


def records():
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{"timestamp": (origin + timedelta(hours=index % 2, days=index // 2)).isoformat(),
             "period": "baseline" if index < 2 else "recent", "energy": energy, "production": production,
             "load": "50", "recipe": "A", "hours": "1", "price": "1", "energy_unit": "kWh", "production_unit": "件"}
            for index, (energy, production) in enumerate([("100", "10"), ("100", "90"), ("120", "30"), ("120", "70")])]


def config(**kwargs):
    return EnergyInput(device_id="test-device", query_id="test-query", min_samples=2, condition_fields=["recipe"], **kwargs)


def test_unit_energy_uses_production_weight_not_mean_of_ratios():
    result = analyze_energy(records(), config(duration_field="hours", price_field="price", planned_hours="10"))
    assert result["status"] == "opportunity"
    assert Decimal(result["baseline_sec"]) == Decimal("2")
    assert Decimal(result["recent_sec"]) == Decimal("2.4")
    assert Decimal(result["relative_change"]) == Decimal("0.2")
    assert Decimal(result["saving_space_kwh"]) == Decimal("40")
    assert result["economics"]["saving_space_cny"] == "40.00"
    assert Decimal(result["economics"]["projection"]["saving_space_kwh"]) == Decimal("200")
    assert result["economics"]["projection"]["saving_space_cny"] == "200.00"
    assert result["suggestions"][0]["evidence"]["bin_ids"] == ["bin-1"]
    assert "历史基准" in result["suggestions"][0]["boundary"]


def test_partial_price_and_missing_runtime_never_fill_or_annualize():
    rows = records()
    rows[-1]["price"] = None
    result = analyze_energy(rows, config(price_field="price", planned_hours="4000"))
    assert result["saving_space_kwh"] == "40"
    assert result["economics"]["saving_space_cny"] is None
    assert result["economics"]["priced_samples"] == 1
    assert result["economics"]["runtime_hours"] is None
    assert result["economics"]["projection"] is None
    rows[-1]["price"] = "1"
    rows[-2]["price"] = "2"
    result = analyze_energy(rows, config(price_field="price"))
    # 实际360元、历史单位能耗按同期产量/电价反事实260元，不能用均价随意乘40kWh。
    assert result["economics"]["saving_space_cny"] == "100.00"


def test_power_requires_average_power_and_duration_unit_conversion():
    rows = records()
    for row in rows:
        row["energy"] = str(Decimal(row["energy"]) * 1000)
        row["hours"] = "60"
    result = analyze_energy(rows, config(energy_mode="power", energy_unit="W", duration_field="hours", duration_unit="min"))
    assert result["saving_space_kwh"] == "40.000"
    assert Decimal(result["recent_sec"]) == Decimal("2.4")
    with pytest.raises(ValidationError):
        config(energy_mode="power", energy_unit="kW")
    with pytest.raises(ValidationError):
        config(energy_mode="interval", energy_unit="kW")


def test_cumulative_meter_differences_anchors_and_counter_reset():
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [{"timestamp": (origin + timedelta(hours=index % 4, days=index // 4)).isoformat(),
             "period": "baseline" if index < 4 else "recent", "energy": value, "production": "50", "load": "50", "recipe": "A"}
            for index, value in enumerate(["1000", "1100", "1200", "1300", "2000", "2120", "20", "140"])]
    result = analyze_energy(rows, config(energy_mode="cumulative"))
    assert result["status"] == "opportunity"
    assert result["issues"]["counter_anchors"] == 2
    assert result["issues"]["counter_resets"] == 1
    assert result["candidate_intervals"] == 6 and result["valid_count"] == 5
    assert result["bins"][0]["baseline_energy_kwh"] == "300"
    assert result["bins"][0]["recent_energy_kwh"] == "240"
    assert Decimal(result["saving_space_kwh"]) == Decimal("40")
    assert result["periods"]["recent"]["interval_start_original"] == rows[4]["timestamp"]


def test_missing_output_idle_unknown_conditions_and_zero_baseline_do_not_claim_savings():
    for mutation in ("production_missing", "idle", "recipe_changed", "zero_baseline"):
        rows = records()
        for index, row in enumerate(rows):
            if mutation == "production_missing": row["production"] = None
            if mutation == "idle": row["production"] = "0"
            if mutation == "recipe_changed" and index >= 2: row["recipe"] = "B"
            if mutation == "zero_baseline" and index < 2: row["energy"] = "0"
        result = analyze_energy(rows, config(price_field="price", planned_hours="4000"))
        assert result["status"] == "insufficient_data", mutation
        assert result["saving_space_kwh"] is None and result["saving_space_percent"] is None
        assert result["economics"]["saving_space_cny"] is None and result["economics"]["projection"] is None
        assert all(suggestion.get("evidence") is not None and suggestion.get("boundary") for suggestion in result["suggestions"])
        if mutation == "idle":
            assert result["zero_output_recent_energy_kwh"] == "240"
            assert any(row["kind"] == "zero_output_energy" for row in result["suggestions"])


def test_duplicates_conflicts_unit_mismatch_and_device_scope_are_visible():
    rows = records()
    conflict = {**rows[0], "energy": "101"}
    result = analyze_energy(rows + [deepcopy(rows[0]), conflict], config())
    assert result["issues"]["duplicate_rows"] == 2 and result["issues"]["conflicting_timestamps"] == 1
    assert result["status"] == "insufficient_data"
    rows = records()
    rows[-1]["production_unit"] = "kg"
    result = analyze_energy(rows, config(production_unit_field="production_unit"))
    assert result["issues"]["unit_mismatch"] == 1
    rows = [{**row, "asset": "PUMP-01"} for row in records()]
    rows.append({**rows[-1], "asset": "PUMP-02", "energy": "99999"})
    result = analyze_energy(rows, config(device_field="asset", device_value="PUMP-01"))
    assert result["scoped_rows"] == 4 and result["issues"]["other_device"] == 1
    assert result["coverage"] == "1" and result["saving_space_kwh"] == "40"


def test_shanghai_natural_day_bounds_keep_original_offsets_and_period_labels():
    rows = records()
    for index, row in enumerate(rows):
        row["period"] = "基准" if index < 2 else "近期"
        row["timestamp"] = ["2026-10-18T16:10:00Z", "2026-10-19T15:50:00Z", "2026-10-19T16:10:00Z", "2026-10-20T15:50:00Z"][index]
    result = analyze_energy(rows, config(baseline_value="基准", recent_value="近期"))
    recent = result["periods"]["recent"]
    assert recent["first_original"] == "2026-10-19T16:10:00Z"
    assert recent["first_local"] == "2026-10-20T00:10:00+08:00"
    assert recent["containing_days_start_local"] == "2026-10-20T00:00:00+08:00"
    assert recent["containing_days_end_exclusive_local"] == "2026-10-21T00:00:00+08:00"


def test_overlapping_periods_cannot_be_reported_as_historical_saving_space():
    rows = records()
    for index in (2, 3):
        rows[index]["timestamp"] = rows[index - 2]["timestamp"]
    result = analyze_energy(rows, config(price_field="price", duration_field="hours", planned_hours="4000"))
    assert result["status"] == "insufficient_data" and result["coverage_detail"]["period_order"] == "0"
    assert result["saving_space_kwh"] is None and result["economics"]["saving_space_cny"] is None
    assert result["economics"]["projection"] is None


def test_counter_reset_and_condition_crossing_are_both_reported():
    rows = records()
    rows[0]["energy"], rows[1]["energy"] = "100", "10"
    rows[1]["recipe"] = "B"
    result = analyze_energy(rows, config(energy_mode="cumulative"))
    assert result["issues"]["counter_resets"] == 1
    assert result["issues"]["counter_condition_boundaries"] == 1
    assert result["saving_space_kwh"] is None


def test_large_finite_amount_does_not_fail_at_cent_rounding():
    rows = records()
    for index, row in enumerate(rows):
        row["energy"] = "1e100" if index < 2 else "2e100"
        row["hours"] = "1e100"
        row["price"] = "1e100"
    result = analyze_energy(rows, config(energy_mode="power", energy_unit="kW", duration_field="hours", price_field="price"))
    assert Decimal(result["economics"]["saving_space_cny"]) == Decimal("2e300")


def test_money_rounds_half_cent_explicitly():
    rows = records()
    for row in rows:
        row["price"] = "0.000125"
    result = analyze_energy(rows, config(price_field="price"))
    assert result["saving_space_kwh"] == "40"
    assert result["economics"]["saving_space_cny"] == "0.01"


@pytest.fixture
def database(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "new-energy.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    actor = Actor("energy-user", "energy-workspace", frozenset({"*"}), ("admin",))
    settings = Settings(data_dir=tmp_path / "new-energy-data")
    with Session(engine) as session:
        base = ManufacturingService(session, actor)
        device = base.create(m.Device, {"name": "本轮能耗测试设备"})
        data = DataService(session, actor, settings)
        csv = "timestamp,period,energy,production,load,recipe,hours,price\n2026-01-01T00:00:00Z,baseline,100,10,50,A,1,1\n2026-01-01T01:00:00Z,baseline,100,90,50,A,1,1\n2026-01-02T00:00:00Z,recent,120,30,50,A,1,1\n2026-01-02T01:00:00Z,recent,120,70,50,A,1,1\n"
        dataset = data.import_csv({"name": "本轮新能耗记录", "csv_text": csv})
        query = data.execute_query({"name": "本轮新能耗查询", "datasource_id": dataset["datasource_id"], "sql": f'SELECT * FROM "{dataset["table_name"]}"'})
        yield EnergyService(session, actor, settings), data, device, query
        session.rollback()
    engine.dispose()


def test_database_analysis_immutable_result_task_rename_and_export(database):
    service, _, device, query = database
    result = service.calculate(EnergyInput(device_id=device["id"], query_id=query["id"], min_samples=2, condition_fields=["recipe"], duration_field="hours", price_field="price"))
    assert result["query_id"] != query["id"] and result["provenance"]["source_query_id"] == query["id"]
    assert result["result"]["saving_space_kwh"] == "40" and result["result"]["economics"]["saving_space_cny"] == "40.00"
    assert TaskService(service.session, service.actor).get(result["task_id"])["status"] == "succeeded"
    renamed = service.rename(result["id"], {"revision": result["revision"], "name": "已核对能耗记录"})
    assert renamed["result"] == result["result"] and renamed["provenance"] == result["provenance"]
    assert "baseline_sec" in service.export(result["id"]) and "已核对能耗记录" in service.export(result["id"])
    with pytest.raises(DomainError) as error:
        service.rename(result["id"], {"revision": result["revision"], "name": "过期改名"})
    assert error.value.code == "revision_conflict"
    service.remove(result["id"], renamed["revision"])
    assert service.listing() == {"items": [], "total": 0}
    assert service.session.get(EnergyAnalysis, result["id"]).result == result["result"]


def test_database_history_and_count_recheck_revoked_field_permissions(database, monkeypatch):
    service, _, device, query = database
    result = service.calculate(EnergyInput(device_id=device["id"], query_id=query["id"], min_samples=2))
    def revoked(*args, **kwargs):
        raise SqlFieldValidationError("能耗字段已撤权", field="energy")
    monkeypatch.setattr(DataService, "get_query", revoked)
    assert service.listing() == {"items": [], "total": 0}
    with pytest.raises(DomainError) as error:
        service.export(result["id"])
    assert error.value.status == 403
    with pytest.raises(DomainError) as error:
        EnergyService(service.session, Actor("other", "other-workspace", frozenset({"*"})), service.settings).energy(result["id"])
    assert error.value.status == 404


def test_database_missing_mapping_rejected_before_extra_query(database, monkeypatch):
    service, _, device, query = database
    def forbidden(*args, **kwargs):
        raise AssertionError("无效映射不能发起业务取数")
    monkeypatch.setattr(DataService, "execute_query", forbidden)
    with pytest.raises(DomainError) as error:
        service.calculate(EnergyInput(device_id=device["id"], query_id=query["id"], production_field="absent"))
    assert error.value.code == "energy_mapping_missing"
