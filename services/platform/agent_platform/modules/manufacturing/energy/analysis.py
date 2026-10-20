"""复用维护分析的分箱、冲突去重和覆盖判定；能耗按产量加权，金额仅由完整价格核算。"""
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from zoneinfo import ZoneInfo

from ..schemas import decimal_text
from .schemas import EnergyInput

D = Decimal
ZERO = D(0)
ENERGY_FACTORS = {"Wh": D("0.001"), "kWh": D(1), "MWh": D(1000), "W": D("0.001"), "kW": D(1)}


def text(value):
    """精确和量保留完整十进制，比例按本次240位上下文计算，不使用二进制浮点。"""
    return format(value, "f") if value is not None else None


def number(value):
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise ValueError("缺少数值")
    return D(decimal_text(str(value)))


@dataclass(frozen=True)
class Observation:
    stamp: datetime
    original_time: str
    period: str
    energy: Decimal
    production: Decimal
    load: Decimal
    conditions: tuple[str, ...]
    duration: Decimal | None
    price: Decimal | None
    interval_start: datetime | None = None
    original_interval_start: str | None = None


def _timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = datetime.fromtimestamp(value, timezone.utc)
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("观测时间缺少时区")
    if stamp.year > 9998:
        raise ValueError("观测时间超出可表示范围")
    return stamp.astimezone(timezone.utc)


def _optional(row, field):
    if not field:
        return None
    try:
        result = number(row.get(field))
        return result if result >= ZERO else None
    except (ValueError, TypeError):
        return None


def _prepare(rows, config):
    """同一设备同一时刻不同内容视为冲突；累计表只差分，不猜测重置前消耗。"""
    parsed, issues = [], Counter()
    scoped = 0
    for row in rows:
        if config.device_field and str(row.get(config.device_field, "")) != config.device_value:
            issues["other_device"] += 1
            continue
        scoped += 1
        reason = "invalid_time"
        try:
            stamp = _timestamp(row[config.time_field])
            reason = "unknown_period"
            period = {config.baseline_value: "baseline", config.recent_value: "recent"}.get(str(row[config.period_field]))
            if period is None:
                raise ValueError()
            reason = "invalid_energy"
            energy = number(row.get(config.energy_field))
            if energy < ZERO:
                raise ValueError()
            reason = "missing_or_invalid_production"
            production = number(row.get(config.production_field))
            if production < ZERO:
                raise ValueError()
            reason = "unit_mismatch"
            if config.energy_unit_field and str(row.get(config.energy_unit_field)) != config.energy_unit:
                raise ValueError()
            if config.production_unit_field and str(row.get(config.production_unit_field)) != config.production_unit:
                raise ValueError()
            reason = "missing_condition"
            load = number(row.get(config.load_field))
            conditions = tuple(str(row[field]).strip() for field in config.condition_fields if row.get(field) is not None and str(row[field]).strip())
            if len(conditions) != len(config.condition_fields):
                raise ValueError()
            duration = _optional(row, config.duration_field)
            if duration is not None:
                duration /= {"h": D(1), "min": D(60), "s": D(3600)}[config.duration_unit]
            if config.energy_mode == "power":
                reason = "missing_or_invalid_duration"
                if duration is None or duration <= ZERO:
                    raise ValueError()
                energy *= duration
            energy *= ENERGY_FACTORS[config.energy_unit]
            parsed.append(Observation(stamp, str(row[config.time_field]), period, energy, production, load, conditions, duration, _optional(row, config.price_field)))
        except (ValueError, TypeError, KeyError, OverflowError, ArithmeticError):
            issues[reason] += 1
    unique, conflicts = {}, set()
    for row in parsed:
        key = (row.period, row.stamp)
        if key in unique or key in conflicts:
            issues["duplicate_rows"] += 1
            previous = unique.get(key)
            if previous and replace(previous, original_time=row.original_time) != row:
                unique.pop(key)
                conflicts.add(key)
        else:
            unique[key] = row
    issues["conflicting_timestamps"] = len(conflicts)
    observations = sorted(unique.values(), key=lambda row: row.stamp)
    if config.energy_mode == "cumulative":
        previous, deltas = {}, []
        for row in observations:
            before = previous.get(row.period)
            previous[row.period] = row
            if before is None:
                issues["counter_anchors"] += 1
                continue
            delta = row.energy - before.energy
            crossed = row.conditions != before.conditions or (row.load / D(config.load_bin_width)).to_integral_value(rounding=ROUND_FLOOR) != (before.load / D(config.load_bin_width)).to_integral_value(rounding=ROUND_FLOOR)
            if crossed:
                issues["counter_condition_boundaries"] += 1
            if delta < ZERO:
                issues["counter_resets"] += 1
            if crossed or delta < ZERO:
                continue
            deltas.append(replace(row, energy=delta, interval_start=before.stamp, original_interval_start=before.original_time))
        observations = deltas
    return observations, issues, scoped


def _period_bounds(rows, name, zone):
    selected = sorted((row for row in rows if row.period == name), key=lambda row: row.stamp)
    if not selected:
        return None
    first, last = selected[0], selected[-1]
    measured_start = min((row.interval_start or row.stamp for row in selected))
    start_day = datetime.combine(measured_start.astimezone(zone).date(), time.min, zone)
    end_day = datetime.combine(last.stamp.astimezone(zone).date() + timedelta(days=1), time.min, zone)
    return {"first_original": first.original_time, "last_original": last.original_time,
            "first_utc": first.stamp.isoformat(), "last_utc": last.stamp.isoformat(),
            "first_local": first.stamp.astimezone(zone).isoformat(), "last_local": last.stamp.astimezone(zone).isoformat(),
            "interval_start_original": first.original_interval_start,
            "interval_start_local": first.interval_start.astimezone(zone).isoformat() if first.interval_start else None,
            "containing_days_start_local": start_day.isoformat(), "containing_days_end_exclusive_local": end_day.isoformat()}


def _group(rows, config):
    baseline, recent = defaultdict(list), defaultdict(list)
    for row in rows:
        if row.production <= ZERO:
            continue
        index = (row.load / D(config.load_bin_width)).to_integral_value(rounding=ROUND_FLOOR)
        (baseline if row.period == "baseline" else recent)[(index, row.conditions)].append(row)
    bins, comparisons = [], []
    for index, conditions in sorted(set(baseline) | set(recent)):
        before, after = baseline[(index, conditions)], recent[(index, conditions)]
        before_energy, after_energy = sum((row.energy for row in before), ZERO), sum((row.energy for row in after), ZERO)
        before_production, after_production = sum((row.production for row in before), ZERO), sum((row.production for row in after), ZERO)
        baseline_sec = before_energy / before_production if before_production else None
        recent_sec = after_energy / after_production if after_production else None
        eligible = min(len(before), len(after)) >= config.min_samples and baseline_sec is not None and baseline_sec > ZERO
        expected_energy = baseline_sec * after_production if eligible else None
        gap = after_energy - expected_energy if expected_energy is not None else None
        group = {"id": f"bin-{len(bins) + 1}", "load_min": text(index * D(config.load_bin_width)), "load_max": text((index + 1) * D(config.load_bin_width)),
                 "conditions": dict(zip(config.condition_fields, conditions)), "baseline_count": len(before), "recent_count": len(after),
                 "baseline_energy_kwh": text(before_energy), "recent_energy_kwh": text(after_energy),
                 "baseline_production": text(before_production), "recent_production": text(after_production),
                 "baseline_sec": text(baseline_sec), "recent_sec": text(recent_sec), "eligible": eligible,
                 "expected_recent_energy_kwh": text(expected_energy), "energy_gap_kwh": text(gap),
                 "saving_space_kwh": text(max(gap, ZERO)) if gap is not None else None}
        bins.append(group)
        if eligible:
            comparisons.append((group, after, baseline_sec))
    return bins, comparisons


def _coverage(rows, scoped, comparisons, config):
    production = [row for row in rows if row.production > ZERO]
    recent = [row for row in production if row.period == "recent"]
    matched = [row for _, after, _ in comparisons for row in after]
    recent_production = sum((row.production for row in recent), ZERO)
    matched_production = sum((row.production for row in matched), ZERO)
    by_time = D(1)
    if config.expected_interval_seconds:
        for period in ("baseline", "recent"):
            times = sorted({row.stamp for row in rows if row.period == period})
            expected = int((times[-1] - times[0]).total_seconds() / config.expected_interval_seconds) + 1 if times else 1
            by_time = min(by_time, D(len(times)) / max(expected, 1))
    detail = {"valid_samples": D(len(rows)) / scoped if scoped else ZERO,
              "matched_recent_samples": D(len(matched)) / len(recent) if recent else ZERO,
              "matched_recent_production": matched_production / recent_production if recent_production else ZERO,
              "time": by_time}
    return min(detail.values()), detail, matched


def _economics(comparisons, matched, saving, enough, config):
    complete = bool(matched) and config.price_field is not None and all(row.price is not None for row in matched)
    amount = ZERO
    if complete and enough:
        # 按原时段价格比较反事实电量；不能用部分电价补平均价，也不把负差额变成节约。
        for group, after, baseline_sec in comparisons:
            if D(group["energy_gap_kwh"]) > ZERO:
                amount += sum(((row.energy - baseline_sec * row.production) * row.price for row in after), ZERO)
        amount = max(amount, ZERO)
    money = _money(amount) if complete and enough else None
    runtime_complete = bool(matched) and config.duration_field is not None and all(row.duration is not None and row.duration > ZERO for row in matched)
    runtime = sum((row.duration for row in matched), ZERO) if runtime_complete else None
    projection = None
    if enough and config.planned_hours and runtime:
        projected_energy = saving / runtime * D(config.planned_hours)
        projected_money = amount / runtime * D(config.planned_hours) if complete else None
        projection = {"planned_hours": config.planned_hours, "observed_runtime_hours": text(runtime), "saving_space_kwh": text(projected_energy),
                      "saving_space_cny": text(_money(projected_money)) if projected_money is not None else None,
                      "basis": "假设计划工时内维持本次匹配样本的产量节拍、工况组合和电价，仅按已观察单位运行时长差额估算"}
    return {"saving_space_cny": text(money), "price_complete": complete,
            "priced_samples": sum(row.price is not None for row in matched), "required_price_samples": len(matched),
            "runtime_hours": text(runtime), "projection": projection}


def _money(value):
    """金额保留两位时为整数部分留足位数，不因有效大数触发量化异常。"""
    with localcontext() as context:
        context.prec = max(context.prec, value.adjusted() + 4)
        return value.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def analyze_energy(rows: list[dict], config: EnergyInput) -> dict:
    """比较同工况两期单位能耗；所有空间只覆盖匹配生产样本，不外推到整厂或全年。"""
    with localcontext() as context:
        context.prec = 240
        observations, issues, scoped = _prepare(rows, config)
        bins, comparisons = _group(observations, config)
        candidate_intervals = max(scoped - issues["counter_anchors"], 0)
        coverage, coverage_detail, matched = _coverage(observations, candidate_intervals, comparisons, config)
        baseline_times = [row.stamp for row in observations if row.period == "baseline"]
        recent_times = [row.stamp for row in observations if row.period == "recent"]
        ordered = bool(baseline_times and recent_times) and max(baseline_times) < min(recent_times)
        coverage_detail["period_order"] = D(1) if ordered else ZERO
        coverage = min(coverage, coverage_detail["period_order"])
        enough = bool(matched) and coverage >= D(config.minimum_coverage)
        actual = sum((row.energy for row in matched), ZERO)
        production = sum((row.production for row in matched), ZERO)
        expected = sum((baseline_sec * sum((row.production for row in after), ZERO) for _, after, baseline_sec in comparisons), ZERO)
        saving = sum((max(D(group["energy_gap_kwh"]), ZERO) for group, _, _ in comparisons), ZERO)
        reasons = []
        if not matched:
            reasons.append("两期缺少样本足够且基准能耗大于零的共同工况")
        if coverage < D(config.minimum_coverage):
            reasons.append("有效数据、时间连续性或同工况覆盖不足")
        if baseline_times and recent_times and not ordered:
            reasons.append("两期时间重叠或顺序不符，不能作为历史基准比较")
        status = "insufficient_data" if not enough else "opportunity" if saving > ZERO else "no_observed_gap"
        economics = _economics(comparisons, matched, saving, enough, config)
        idle = [row for row in observations if row.period == "recent" and row.production == ZERO and row.energy > ZERO]
        suggestions = []
        if enough and saving > ZERO:
            suggestions.append({"kind": "unit_energy_gap", "title": "核查同工况单位能耗增加的原因", "evidence": {"bin_ids": [group["id"] for group, _, _ in comparisons if D(group["energy_gap_kwh"]) > ZERO], "saving_space_kwh": text(saving), "coverage": text(coverage)}, "boundary": "空间以历史基准在近期同工况下可再次达到为假设，尚未证明可实际节省；不含未匹配或零产出时段"})
        if idle:
            suggestions.append({"kind": "zero_output_energy", "title": "核对零产出时段的用电用途", "evidence": {"sample_count": len(idle), "energy_kwh": text(sum((row.energy for row in idle), ZERO))}, "boundary": "清洗、保温和待机用途未确认，这部分用电不计入可避免节能量"})
        if not enough:
            suggestions.append({"kind": "data_completion", "title": "补齐可比较的工况与产量记录", "evidence": {"issues": dict(issues), "coverage": text(coverage), "minimum_coverage": config.minimum_coverage}, "boundary": "当前不提供节能比例、金额或计划工时收益"})
        elif not economics["price_complete"]:
            suggestions.append({"kind": "price_completion", "title": "补齐匹配生产时段的电价记录", "evidence": {"priced_samples": economics["priced_samples"], "required_samples": len(matched)}, "boundary": "缺失电价不填零、不用部分平均值补齐，金额保持未估算"})
        zone = ZoneInfo(config.timezone)
        return {"status": status, "method": "matched-load-production-weighted-energy-v1", "energy_unit": "kWh", "production_unit": config.production_unit,
                "source_rows": len(rows), "scoped_rows": scoped, "candidate_intervals": candidate_intervals, "valid_count": len(observations), "issues": dict(issues),
                "baseline_count": sum(row.period == "baseline" for row in observations), "recent_count": sum(row.period == "recent" for row in observations),
                "matched_recent_count": len(matched), "coverage": text(coverage), "coverage_detail": {key: text(value) for key, value in coverage_detail.items()},
                "baseline_sec": text(expected / production) if enough and production else None,
                "recent_sec": text(actual / production) if enough and production else None,
                "relative_change": text((actual - expected) / expected) if enough and expected else None,
                "matched_recent_energy_kwh": text(actual), "matched_recent_production": text(production),
                "expected_recent_energy_kwh": text(expected) if enough else None, "saving_space_kwh": text(saving) if enough else None,
                "saving_space_percent": text(saving / actual * 100) if enough and actual else None,
                "economics": economics, "zero_output_recent_energy_kwh": text(sum((row.energy for row in idle), ZERO)),
                "periods": {name: _period_bounds(observations, name, zone) for name in ("baseline", "recent")}, "timezone": config.timezone,
                "time_basis": "按源记录明确的baseline/recent归属；自然日边界仅说明观测所处日期，不将跨日区间或缺测外推为完整日耗能",
                "bins": bins, "reasons": reasons, "suggestions": suggestions}
