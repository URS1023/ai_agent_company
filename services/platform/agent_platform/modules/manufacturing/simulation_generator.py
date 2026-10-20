"""提取Test5纯模拟算法；只生成显式synthetic样本，不连接设备或数据库。"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone

from .simulation_fields import FIELDS

VERSION = "food-v1"
MANUALS = {
    "gearbox": "https://download.sew-eurodrive.com/download/pdf/31551629.pdf",
    "motor": "https://download.sew-eurodrive.com/download/pdf/33090874.pdf",
    "pump": "https://api.grundfos.com/literature/Grundfosliterature-6911153.pdf",
}


def iso(value: datetime) -> str:
    """统一使用可排序的 UTC 秒级时间。"""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str | datetime) -> datetime:
    """时间必须带时区，避免本机时区改变查询口径。"""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("时间必须带时区")
    return parsed.astimezone(timezone.utc)


def asset_definitions() -> list[dict]:
    """真实组件与模拟整机分开登记，不伪造完整订货型号。"""
    result = []
    for kind, code, count, names, family, power in [
        ("conveyor", "CV", 3, ["公共上料输送", "包装一线输送", "包装二线输送"], "SEW R37 + DRN（完整配置待锁定）", 1.5),
        ("mixer", "MX", 2, ["一号批次混料", "二号批次混料"], "SEW RM67候选 + DRN（完整配置待锁定）", 3.0),
        ("pump", "CW", 3, ["循环水一号泵", "循环水二号泵", "循环水三号泵"], "Grundfos CR10-5候选（完整订货号待锁定）", 2.5),
    ]:
        for index in range(count):
            result.append({"device_id": f"FOOD-{code}-{index + 1:02}", "name": names[index], "kind": kind, "manufacturer": "Grundfos" if kind == "pump" else "SEW-EURODRIVE（驱动组件）", "model_family": family,
                           "parameters": {"origin": "SIMULATION_DESIGN", "rated_input_kw": power, "minimum_run_seconds": 60, "minimum_stop_seconds": 60, "max_starts_per_hour": 6, "max_flow_m3_h": 10 if kind == "pump" else None, "minimum_flow_m3_h": 1.5 if kind == "pump" else None, "minimum_frequency_hz": 43 if kind == "pump" else 15, "maximum_frequency_hz": 50, "required_available_spares": 1 if kind == "pump" else 0, "food_contact": False, "gear_ratio": 30 if kind == "conveyor" else 50},
                           "manual_sources": [MANUALS["pump"]] if kind == "pump" else [MANUALS["gearbox"], MANUALS["motor"]], "virtual": 1})
    return result


def initial_state() -> dict:
    """仅保存模拟器私有状态；故障演进不在 AI 接口返回。"""
    return {"elapsed_seconds": 0, "tick": 0, "good_total": 0.0, "reject_total": 0.0, "capacity_shortage": False, "devices": {
        asset["device_id"]: {"run_hours": 0.0, "energy_kwh": 0.0, "running": False, "manual_running": None, "frequency_override": None, "temperature": 25.0, "oil_temperature": 25.0, "start_times": [], "start_count": 0, "jam_count": 0, "last_start_time": None, "last_stop_time": None, "state_version": 1, "degradation": 0.0, "maintenance_lock": False, "last_observable": "normal", "jam_active": False}
        for asset in asset_definitions()}}


def noise(device: str, tick: int, field: str) -> float:
    """无状态扰动保证事务回滚后重算一致，不使用全局随机源。"""
    digest = hashlib.sha256(f"{device}:{tick}:{field}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF * 2 - 1


def make_context(at: datetime, state: dict, seconds: int) -> dict:
    """以北京时间班次、换批和清洁阶段形成可解释工况。"""
    local = at.astimezone(timezone(timedelta(hours=8)))
    phase = "production" if 6 <= local.hour < 22 else "cleaning" if local.hour == 22 else "idle"
    target = 72.0 if phase == "production" else 0.0
    actual = target * (0.96 + .015 * math.sin(state["tick"] / 17))
    if state["devices"]["FOOD-CV-01"]["manual_running"] is False:
        actual = 0
    else:
        usable_branches = sum(state["devices"][device]["manual_running"] is not False for device in ("FOOD-CV-02", "FOOD-CV-03"))
        actual *= usable_branches / 2
    state["good_total"] += actual * seconds / 60 * .995
    state["reject_total"] += actual * seconds / 60 * .005
    return {"event_time": iso(at), "line_id": "FOOD-PACK-01", "batch_id": local.strftime("NUT-%Y%m%d-") + str(local.hour * 2 + local.minute // 30), "recipe_id": "nuts-light" if local.hour % 2 else "nuts-heavy", "production_phase": phase, "target_rate_per_min": target, "actual_rate_per_min": round(actual, 3), "good_total": round(state["good_total"], 3), "reject_total": round(state["reject_total"], 3), "cooling_demand_m3_h": 14.0 if phase == "production" else 7.5, "minimum_pressure_bar": 2.7, "cleaning_lock": int(phase == "cleaning"), "downstream_ready": int(phase != "cleaning")}


def pump_power(flow: float, frequency: float = 45) -> float:
    """演示输入功率曲线，不代表厂家曲线或实际节电保证。"""
    return .8 + .06 * max(flow, 0) ** 1.4 + .003 * (frequency - 45) ** 2


def pump_operating_point(frequency: float, demand: float, running_count: int) -> dict:
    """统一模拟泵曲线与平均分配口径，供生成、动作校验和节能计算共用。"""
    capacity = max(0.0, 10 * frequency / 45)
    assigned_flow = max(0.0, demand) / max(running_count, 1)
    differential = 3.0 * (frequency / 45) ** 2
    return {"capacity_m3_h": capacity, "flow_m3_h": min(capacity, assigned_flow), "differential_pressure_bar": differential, "outlet_pressure_bar": 1.1 + differential}


def generate(at: datetime, seconds: int, current: dict) -> tuple[list[dict], dict, list[dict]]:
    """原地推进待提交状态，由调用方负责提交或丢弃。"""
    context = make_context(at, current, seconds)
    current["elapsed_seconds"] += seconds
    current["tick"] += 1
    tick = current["tick"]
    rows, observable_events = [], []
    assets = asset_definitions()
    planned = {}
    for asset in assets:
        device = asset["device_id"]
        private = current["devices"][device]
        default = asset["kind"] == "pump" or context["production_phase"] == "production"
        if asset["kind"] == "mixer":
            default = default and at.minute % 30 < 25
        requested = default if private["manual_running"] is None else private["manual_running"]
        # 食品驱动的人工启动仍受批次与清洁阶段约束，不能永久覆盖生产排程。
        planned[device] = bool(requested) and (default if asset["kind"] != "pump" else True) and not private["maintenance_lock"]
    pump_count = sum(planned[a["device_id"]] for a in assets if a["kind"] == "pump")
    for asset in assets:
        device, kind = asset["device_id"], asset["kind"]
        private = current["devices"][device]
        running = planned[device]
        if running != private["running"]:
            if running:
                private["start_times"].append(iso(at))
                private["start_count"] += 1
                private["last_start_time"] = iso(at)
            else:
                private["last_stop_time"] = iso(at)
            private["state_version"] += 1
            observable_events.append({"device_id": device, "event_type": "started" if running else "stopped", "description": "模拟运行状态变更", "event_time": iso(at)})
        private["running"] = running
        private["start_times"] = [t for t in private["start_times"] if (at - parse_time(t)).total_seconds() < 3600]
        if running:
            private["run_hours"] += seconds / 3600
            if device in {"FOOD-CV-02", "FOOD-MX-02"} and current["elapsed_seconds"] > 3 * 86400:
                private["degradation"] = min(1.8, private["degradation"] + seconds / 300000)
        degradation = private["degradation"]
        progress = at.minute % 30 / 25 * 100
        recipe_load = .7 if context["recipe_id"] == "nuts-heavy" else .48
        load = (recipe_load if kind == "mixer" else .58) if running else 0
        jam = kind == "conveyor" and device.endswith("03") and running and at.hour % 4 == 0 and 10 <= at.minute < 13
        if jam and not private["jam_active"]:
            private["jam_count"] += 1
        private["jam_active"] = jam
        load = .95 if jam else load
        frequency = (private["frequency_override"] or (45 if kind == "pump" else 38 + load * 10)) if running else 0
        speed = frequency * (59 if kind == "pump" else 29.3)
        pump_point = pump_operating_point(frequency, context["cooling_demand_m3_h"], pump_count) if running and kind == "pump" else None
        flow = pump_point["flow_m3_h"] if pump_point else 0
        inlet_event = device == "FOOD-CW-02" and running and at.hour % 6 == 1 and 20 <= at.minute < 35
        if inlet_event:
            flow *= .72 + .08 * math.sin(tick)
        power = (pump_power(flow, frequency) if kind == "pump" else asset["parameters"]["rated_input_kw"] * (.16 + .72 * load) * (1 + .08 * degradation)) if running else .025
        load = min(1.3, power / asset["parameters"]["rated_input_kw"]) if kind == "pump" else load
        common_voltage = 345 if at.hour % 8 == 2 and 40 <= at.minute < 43 else 400
        voltage = common_voltage + 1.3 * noise(device, tick, "voltage")
        factor = .86 if running else .4
        current_a = power * 1000 / (math.sqrt(3) * voltage * factor)
        vibration = (.65 + .65 * load + degradation + (.7 if inlet_event else 0) + (.8 if jam else 0)) if running else .03
        vibration += .04 * noise(device, tick, "vibration")
        ambient = 25 + 2 * math.sin(at.hour / 24 * math.tau)
        target_temp = ambient + (24 * load + 11 * degradation if running else 0)
        private["temperature"] += (target_temp - private["temperature"]) * (1 - math.exp(-seconds / 600))
        oil_target = ambient + (20 * load + 13 * degradation if running else 0)
        private["oil_temperature"] += (oil_target - private["oil_temperature"]) * (1 - math.exp(-seconds / 900))
        private["energy_kwh"] += power * seconds / 3600
        row = dict.fromkeys(FIELDS)
        row.update(device_id=device, event_time=iso(at), ingested_at=iso(datetime.now(timezone.utc)), sample_interval_seconds=seconds, quality="SIMULATED_GOOD", production_phase=context["production_phase"], recipe_id=context["recipe_id"], load_bucket="low" if load < .4 else "medium" if load < .75 else "high", state_version=private["state_version"], simulation_version=VERSION,
                   voltage_ab_v=voltage, voltage_bc_v=voltage + .7, voltage_ca_v=voltage - .5, current_a_a=current_a, current_b_a=current_a * 1.01, current_c_a=current_a * .99, supply_frequency_hz=50 + .01 * noise(device, tick, "hz"), drive_frequency_hz=frequency, motor_voltage_v=frequency / 50 * 400, active_power_kw=power, reactive_power_kvar=power * math.tan(math.acos(factor)), power_factor=factor, energy_total_kwh=private["energy_kwh"], voltage_unbalance_pct=.7 / voltage * 100, current_unbalance_pct=1, voltage_thd_pct=2.3,
                   de_v_rms_x_mm_s=vibration, de_v_rms_y_mm_s=vibration * .92, de_v_rms_z_mm_s=vibration * .85, nde_v_rms_x_mm_s=vibration * .8, nde_v_rms_y_mm_s=vibration * .75, nde_v_rms_z_mm_s=vibration * .71, de_a_rms_m_s2=vibration * .8, nde_a_rms_m_s2=vibration * .65, de_a_peak_m_s2=vibration * 2.4, nde_a_peak_m_s2=vibration * 1.95, de_crest_factor=3, nde_crest_factor=3,
                   de_bearing_temp_c=private["temperature"], nde_bearing_temp_c=private["temperature"] - .6, motor_winding_temp_c=private["temperature"] + (12 if running else 0), motor_housing_temp_c=private["temperature"] - 1, ambient_temp_c=ambient, drive_heatsink_temp_c=ambient + 15 * load, cabinet_temp_c=ambient + 3, speed_rpm=speed,
                   running=int(running), operating_mode="running" if running else "standby", control_mode="remote_simulated", ready=int(not private["maintenance_lock"]), fault_code="SIM_OVERLOAD" if jam else "", run_hours=private["run_hours"], start_count=private["start_count"], starts_last_hour=len(private["start_times"]), last_start_time=private["last_start_time"], last_stop_time=private["last_stop_time"], drive_load_pct=load * 100, frequency_setpoint_hz=frequency, maintenance_lock=int(private["maintenance_lock"]))
        if kind == "pump":
            inlet = .08 if inlet_event else 1.1
            differential = pump_point["differential_pressure_bar"] if pump_point else 0
            row.update(flow_m3_h=flow, inlet_pressure_bar=inlet, outlet_pressure_bar=inlet + differential, differential_pressure_bar=differential, head_m=differential * 10.197, valve_opening_pct=75 if running else 0, inlet_filter_dp_bar=.32 if inlet_event else .04, tank_level_pct=68, leak_rate_ml_min=0, wire_to_water_efficiency_pct=(flow * differential / 36 / power * 100) if running else None, specific_energy_kwh_m3=power / flow if flow else None, fluid_temp_c=30 if running else ambient, pressure_setpoint_bar=3.0)
        else:
            shaft = speed / asset["parameters"]["gear_ratio"]
            row.update(gearbox_oil_temp_c=private["oil_temperature"], gearbox_oil_level_pct=65, output_shaft_rpm=shaft, output_torque_nm=power * .82 * 9550 / shaft if shaft else 0)
            if kind == "conveyor":
                row.update(conveyor_speed_m_min=shaft * .6 * (.55 if jam else 1), belt_slip_pct=45 if jam else .4, jam_count=private["jam_count"], upstream_buffer_pct=85 if jam else 25 if running else 0)
            else:
                row.update(mixer_batch_mass_kg=240 if context["recipe_id"] == "nuts-heavy" else 170, mixer_cycle_progress_pct=min(progress, 100), material_temp_c=ambient + 2, mixer_recipe_step="mix" if running else "completed")
        if device == "FOOD-CV-03" and at.hour % 6 == 3 and at.minute == 17:
            row["de_v_rms_x_mm_s"] = None
            row["quality"] = "SIMULATED_PARTIAL"
        observation = "overload" if jam else "inlet_fluctuation" if inlet_event else "elevated_vibration" if degradation > .5 and running else "normal"
        if observation != private["last_observable"]:
            observable_events.append({"device_id": device, "event_type": "observation", "description": {"overload": "电流和转矩升高，输送速度下降", "inlet_fluctuation": "入口压力下降，流量和振动波动", "elevated_vibration": "相近负荷下振动与温升持续增加", "normal": "观测指标恢复当前工况常态"}[observation], "event_time": iso(at)})
            private["last_observable"] = observation
        rows.append({key: round(value, 6) if isinstance(value, float) else value for key, value in row.items()})
    supplied_flow = sum(row["flow_m3_h"] or 0 for row in rows)
    shortage = supplied_flow < context["cooling_demand_m3_h"] * .95
    if shortage != current.get("capacity_shortage", False):
        observable_events.append({"device_id": "FOOD-CW-01", "event_type": "group_capacity", "description": f"循环水组供给 {supplied_flow:.2f} m³/h，需求 {context['cooling_demand_m3_h']:.2f} m³/h，" + ("供给不足" if shortage else "供给恢复"), "event_time": iso(at)})
    current["capacity_shortage"] = shortage
    return rows, context, observable_events
