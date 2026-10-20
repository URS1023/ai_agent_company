"""按相同负荷分箱比较历史与当前数据，不将统计偏移包装为故障概率。"""
from collections import defaultdict
from datetime import datetime
from math import floor, isfinite

from .schemas import AnalysisInput


def _time(value):
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def analyze(rows: list[dict], config: AnalysisInput) -> dict:
    """只比较两期都有足够样本的负荷箱，按当前样本占比加权。

    缺样本、无共同工况、采样时间不连续均降低覆盖率，低于门槛返回
    insufficient。结果保存原始样本数量、剔除数、每箱均值及时间范围供复算。
    """
    valid, rejected = [], 0
    for row in rows:
        try:
            value, load = float(row[config.value_field]), float(row[config.load_field])
            stamp = _time(row[config.time_field])
            if not all(isfinite(v) for v in (value, load, stamp)):
                raise ValueError()
            period = str(row.get(config.period_field, ""))
            conditions = tuple(str(row[field]) for field in config.condition_fields if row.get(field) is not None and str(row[field]).strip())
            if len(conditions) != len(config.condition_fields):
                raise ValueError()
            valid.append({"value": value, "load": load, "time": stamp, "period": period, "conditions": conditions})
        except (KeyError, ValueError, TypeError, OverflowError):
            rejected += 1
    valid.sort(key=lambda r: r["time"])
    if valid and not any(r["period"] in ("baseline", "recent") for r in valid):
        unique_times = sorted({row["time"] for row in valid})
        boundary = unique_times[len(unique_times) // 2]
        for row in valid:
            row["period"] = "baseline" if row["time"] < boundary else "recent"
    deduplicated, conflicts, duplicate_count = {}, set(), 0
    for row in valid:
        key = (row["time"], row["load"], row["period"], row["conditions"])
        if key in deduplicated or key in conflicts:
            duplicate_count += 1
            if key in deduplicated and deduplicated[key]["value"] != row["value"]:
                deduplicated.pop(key)
                conflicts.add(key)
        else:
            deduplicated[key] = row
    valid = sorted(deduplicated.values(), key=lambda row: row["time"])
    rejected += len(conflicts)
    baseline, recent = defaultdict(list), defaultdict(list)
    for row in valid:
        target = baseline if row["period"] == "baseline" else recent if row["period"] == "recent" else None
        if target is not None:
            target[(floor(row["load"] / config.bin_width), row["conditions"])].append(row["value"])
    bins, matched_count = [], 0
    recent_total = sum(map(len, recent.values()))
    for key in sorted(set(baseline) | set(recent)):
        before, after = baseline[key], recent[key]
        eligible = min(len(before), len(after)) >= config.min_samples
        old = sum(before) / len(before) if before else None
        new = sum(after) / len(after) if after else None
        if eligible:
            matched_count += len(after)
        bins.append({"load_min": key[0] * config.bin_width, "load_max": (key[0] + 1) * config.bin_width,
                     "conditions": dict(zip(config.condition_fields, key[1])),
                     "baseline_count": len(before), "recent_count": len(after),
                     "baseline_mean": old, "recent_mean": new, "eligible": eligible})
    condition_coverage = matched_count / recent_total if recent_total else 0.0
    sample_coverage = len(valid) / len(rows) if rows else 0.0
    period_coverage = sum(r["period"] in ("baseline", "recent") for r in valid) / len(valid) if valid else 0.0
    time_coverage = 1.0
    if config.expected_interval_seconds and len(valid) >= 2:
        for period in ("baseline", "recent"):
            times = sorted({r["time"] for r in valid if r["period"] == period})
            if not times:
                time_coverage = 0.0
                continue
            expected = int((times[-1] - times[0]) / config.expected_interval_seconds) + 1
            time_coverage = min(time_coverage, len(times) / max(expected, 1))
    coverage = min(condition_coverage, sample_coverage, period_coverage, time_coverage)
    enough = matched_count > 0 and coverage >= config.minimum_coverage
    comparable = [b for b in bins if b["eligible"]]
    before = sum(b["baseline_mean"] * b["recent_count"] for b in comparable) / matched_count if matched_count else None
    after = sum(b["recent_mean"] * b["recent_count"] for b in comparable) / matched_count if matched_count else None
    relative_change = (after - before) / abs(before) if before not in (None, 0) else 0.0 if before == 0 and after == 0 else None
    if enough and relative_change is None:
        enough = False
    triggered = relative_change is not None and ((abs(relative_change) if config.change_direction == "either" else relative_change if config.change_direction == "increase" else -relative_change) >= config.change_threshold)
    status = "insufficient_data" if not enough else "attention" if triggered else "normal"
    reasons = []
    if not matched_count:
        reasons.append("两期没有样本数足够的共同负荷区间")
    if coverage < config.minimum_coverage:
        reasons.append("有效数据或同工况覆盖率不足")
    if before == 0 and after != 0:
        reasons.append("基准均值为0，不能计算相对变化")
    if enough:
        reasons.append("同工况指标发生明显变化，建议现场检查" if status == "attention" else "当前比较范围内未见明显偏移")
    return {"status": status, "sample_count": len(rows), "valid_count": len(valid), "rejected_count": rejected,
            "duplicate_count": duplicate_count, "conflicting_observations": len(conflicts), "change_direction": config.change_direction,
            "baseline_count": sum(map(len, baseline.values())), "recent_count": recent_total,
            "matched_recent_count": matched_count, "coverage": coverage,
            "coverage_detail": {"condition": condition_coverage, "valid_samples": sample_coverage, "classified_period": period_coverage, "time": time_coverage},
            "baseline_mean": before, "recent_mean": after, "absolute_change": after - before if after is not None else None,
            "relative_change": relative_change, "bins": bins, "reasons": reasons,
            "time_range": [valid[0]["time"], valid[-1]["time"]] if valid else [],
            "method": "matched-load-bins-recent-weighted-v1"}
