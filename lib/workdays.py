# -*- coding: utf-8 -*-
"""工作日筛查：法定工作日判定，支持法定调休日（周末补班算工作日，法定假不算）。

数据来源（优先级从高到低）:
  1. config.json 的 workday_overrides（holidays / extra_workdays，用户手工兜底，最高优先）
  2. timor.tech 公开节假日 API（按年缓存 out/state/_holiday_cache.json，一次拉取长期复用）
  3. 回退规则: 周一~周五 为工作日（API 不可达且无 overrides 时）
"""
import datetime
import json
import urllib.request
from pathlib import Path

from lib.cdp_exec import SKILL_ROOT
from lib.paths import STATE_DIR

# 节假日安排是跨日期长期状态，放状态区（不随中间产物清理）
HOLIDAY_CACHE = STATE_DIR / "_holiday_cache.json"
API_TIMEOUT_S = 15


def _load_cache():
    if HOLIDAY_CACHE.exists():
        try:
            return json.loads(HOLIDAY_CACHE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def _fetch_year_holidays(year, cache):
    """拉取某年法定节假日安排: {date_iso: True(放假)/False(补班)}。失败返回 None。"""
    if str(year) in cache:
        return cache[str(year)]
    req = urllib.request.Request(
        "https://timor.tech/api/holiday/year/%d" % year,
        headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_S) as r:
            d = json.loads(r.read().decode("utf-8"))
        hol = d.get("holiday") or {}
        if not isinstance(hol, dict) or not hol:
            return None
        data = {}
        for v in hol.values():
            if v.get("date"):
                data[v["date"]] = bool(v.get("holiday"))
        cache[str(year)] = data
        HOLIDAY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        HOLIDAY_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        return data
    except Exception:
        return None


def build_workday_set(start, end, cfg=None):
    """返回 (工作日日期 set{ISO 字符串}, 来源说明 list[str])。

    法定调休日（周末补班）计入工作日；法定假（即使在工作日位置）不计入；
    周末不计入。config.workday_overrides 优先级最高，API 次之，周一~五兜底。
    """
    cfg = cfg or {}
    ov = cfg.get("workday_overrides") or {}
    extra_holidays = {str(x) for x in ov.get("holidays") or []}
    extra_workdays = {str(x) for x in ov.get("extra_workdays") or []}

    cache = _load_cache()
    years = set()
    d = datetime.date.fromisoformat(start)
    d_end = datetime.date.fromisoformat(end)
    while d <= d_end:
        years.add(d.year)
        d += datetime.timedelta(days=1)

    holiday_map, api_years, failed_years = {}, [], []
    for y in sorted(years):
        got = _fetch_year_holidays(y, cache)
        if got is None:
            failed_years.append(y)
        else:
            holiday_map.update(got)
            api_years.append(y)

    workdays, notes = set(), []
    d = datetime.date.fromisoformat(start)
    while d <= d_end:
        iso = d.isoformat()
        if iso in extra_workdays:
            workdays.add(iso)
        elif iso in extra_holidays:
            pass
        elif iso in holiday_map:
            if holiday_map[iso] is False:
                workdays.add(iso)
        elif d.weekday() < 5:
            workdays.add(iso)
        d += datetime.timedelta(days=1)

    if api_years:
        notes.append("法定工作日历: timor.tech API（%d 年）" % len(api_years))
    else:
        notes.append("法定工作日历: API 不可达，回退周一~周五规则")
    if failed_years:
        notes.append("%d 年节假日拉取失败: %s" % (len(failed_years), ", ".join(map(str, failed_years))))
    if extra_holidays:
        notes.append("overrides.holidays=%s" % sorted(extra_holidays))
    if extra_workdays:
        notes.append("overrides.extra_workdays=%s（调休补班计入工作日）" % sorted(extra_workdays))
    return workdays, notes


def previous_workday(ref=None, cfg=None, max_back=40):
    """ref 之前（不含 ref 当天）最近的一个工作日 ISO 日期。

    每日跑数默认取「前一个工作日」：今天往前回溯，跳过周末与法定节假日；
    今天本身是周末/法定假也照样回溯（例如周一跑取上周五，节后首日跑取节前最后一个工作日）。
    判定复用 build_workday_set（timor.tech 节假日日历 + config.workday_overrides 兜底），
    单日区间只要该日出现在工作日集合中即为工作日。回溯 max_back 天仍无结果时退回 ref-1。
    """
    day = ref or datetime.date.today()
    if isinstance(day, str):
        day = datetime.date.fromisoformat(day)
    first = (day - datetime.timedelta(days=max_back)).isoformat()
    last = (day - datetime.timedelta(days=1)).isoformat()
    # 一次区间调用取该窗口内全部工作日（避免逐日回溯重复读节假日缓存）
    wds, _notes = build_workday_set(first, last, cfg)
    picked = [x for x in wds if first <= x <= last]
    if picked:
        return max(picked)
    return last
