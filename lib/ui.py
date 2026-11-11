# -*- coding: utf-8 -*-
"""展示层公共组件（唯一来源）：样式 + 公共日期筛选组件 + 页面骨架。

为什么必须"提成公共"：
  首页（index）与二级页（detail）的所有日期筛选，口径都是**执行结束时间**
  （pipelineRunEndtime / github run.updated_at），归档日 = 结束时间所在日期。
  两处若各写一套，很容易出现首页按结束时间、二级页按开始时间或采集时间的错位，
  同一个构建在两个页面被算进不同日期 —— 结论就没法核对。所以区间语义、组件 DOM、
  交互 JS 全部收在这里，两个页面都从这里取。

数据与展示完全隔离：
  这里只产出**静态壳**（样式 + 空容器 + 取数脚本），页面运行时用 fetch 读
  ../archive/... 下的 JSON。HTML 里不内嵌任何业务数据，归档目录才是唯一数据源。
"""
import json

from lib.paths import DATE_FIELD, DATE_RULE

# 日期口径徽标文案（首页/二级页都显示，避免"这个日期到底按什么算"的误解）
DATE_BADGE = "口径：执行结束时间"

# 系统级品牌行（首页/二级页同一份，放在公共日期筛选条上方）
BRAND_TITLE = "openLiBing指标分析"
BRAND_SUB = "夜间流水线工程指标 · 口径：执行结束时间 · 数据源 out/archive/"

CSS = """
:root{
  --bg:#f6f7f9; --panel:#ffffff; --line:#e4e7ec; --line2:#eef0f3;
  --txt:#1f2328; --dim:#6b7280; --acc:#2563eb;
  --green:#16a34a; --yellow:#d97706; --red:#dc2626; --gray:#c9ced6;
  --radius:10px;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--txt);
  font:13px/1.55 "Noto Sans CJK SC","Microsoft YaHei",-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
h1{font-size:21px;margin:0 0 6px;font-weight:600;letter-spacing:.2px;color:#16233a}
h2{font-size:15px;margin:14px 0 10px;padding-left:10px;border-left:3px solid var(--acc);
  color:#1c2b45;font-weight:600}
h3{font-size:13.5px;margin:14px 0 8px;font-weight:600;color:#1c2b45}
.dim{color:var(--dim)}
.mono{font-family:Consolas,"Courier New",monospace}
.num{font-variant-numeric:tabular-nums}

/* ---------- 页面骨架：白卡 + 标题 + 公共日期筛选（两页共用，跳转时顶部不变） ---------- */
.wrap{width:min(1560px,94vw);margin:12px auto;background:var(--panel);border-radius:10px;
  box-shadow:0 1px 6px rgba(16,32,64,.06);padding:18px 30px 20px;
  min-height:calc(100vh - 24px);display:flex;flex-direction:column}
p.meta{font-size:12.5px;color:#7c8698;line-height:1.65;margin:0}
/* 品牌行：两页完全一致，因此它下面的公共筛选条在切页时位置不跳动 */
.hd{margin:0 0 12px}
.hd h1{font-size:21px;margin:0 0 5px}
.hd p.meta{font-size:12.5px}
/* 公共日期筛选条：系统级全局筛选，整条贴到卡片左右边缘并吸顶（top:0），
   首页与二级页共用同一份 DOM + 同一位置（品牌行之下、页面自身标题之上），
   切页、滚动都不动，语义始终是「整个系统的数据筛选」。 */
.toolbar{position:sticky;top:0;z-index:40;display:flex;flex-wrap:wrap;align-items:center;gap:10px;
  margin:0 -30px 14px;padding:8px 30px;background:#fff;
  border-top:1px solid var(--line2);border-bottom:1px solid var(--line);
  box-shadow:0 2px 8px rgba(16,32,64,.05)}
.toolbar .lb{font-size:12.5px;color:#4a5668;font-weight:600;white-space:nowrap}
.toolbar .vsep{width:1px;height:16px;background:#e2e8f0;margin:0 2px}
.toolbar .sp{flex:1 1 auto}
.seg{display:inline-flex;border:1px solid #d9e0ea;border-radius:8px;overflow:hidden;background:#fff}
.seg button{border:0;border-right:1px solid #e8edf4;background:#fff;padding:6px 14px;font:inherit;
  font-size:13px;color:#4a5668;cursor:pointer;line-height:1.3;transition:.15s}
.seg button:last-child{border-right:0}
.seg button:hover{background:#eef4ff;color:var(--acc)}
.seg button.on{background:var(--acc);color:#fff;font-weight:600}
.dates{display:inline-flex;align-items:center;gap:6px;background:#fff;border:1px solid #d9e0ea;
  border-radius:8px;padding:4px 10px}
.dates input[type=date]{border:0;outline:0;font:inherit;font-size:12.5px;color:#2b3547;
  background:transparent;padding:2px 0}
.dates .dash{color:#c2cad6;font-size:12px}
.hint{font-size:12px;color:var(--dim);white-space:nowrap}
.hint b{color:#374151;font-weight:600}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:#fff;
  border-radius:8px;padding:4px 9px;font:inherit;color:#374151;cursor:pointer}
.chip.on{border-color:#bfd3fe;background:#eff5ff;color:#1d4ed8}
input[type=text],select{border:1px solid var(--line);border-radius:8px;background:#fff;
  padding:5px 9px;font:inherit;color:var(--txt);outline:0}
input[type=text]:focus,select:focus{border-color:#b9cdfb;box-shadow:0 0 0 3px #eaf1ff}
label.sw{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#374151;cursor:pointer}

/* ---------- 卡片 ---------- */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px;margin-bottom:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:10px 12px}
.card .k{font-size:12px;color:var(--dim)}
.card .v{font-size:19px;font-weight:600;margin-top:2px;font-variant-numeric:tabular-nums}
.card .s{font-size:11.5px;color:var(--dim);margin-top:1px}

/* ---------- 表格 ---------- */
/* 表格容器自适应剩余高度：数据条数变化时高度不变，页面不出现外层滚动条 */
.tbl-wrap{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  overflow:auto;flex:1 1 auto;height:auto;min-height:320px}
.detail .tbl-wrap{height:auto;max-height:none}
/* 组件级概览表：版式对齐旧报告 table.ov（表头浅蓝底、单元格细右分隔线、行高 7px/14px） */
table.ov{margin:0;border-collapse:separate;border-spacing:0;width:100%;font-size:13px}
table.ov thead th{position:sticky;top:0;z-index:3;background:#f4f7fb;color:#3b4a60;font-weight:600;
  font-size:12.5px;text-align:center;padding:8px 14px;border:0;border-bottom:1px solid #e6ebf2;
  border-right:1px solid #eef2f7;white-space:nowrap}
table.ov thead th:last-child{border-right:0}
table.ov tbody td{padding:7px 14px;line-height:1.45;border:0;border-bottom:1px solid #eef2f7;
  border-right:1px solid #f5f7fa;background:#fff;vertical-align:middle}
table.ov tbody td:last-child{border-right:0}
table.ov tbody tr:hover td{background:#f6faff}
table.ov .c-name{font-weight:600;color:#1b2739;white-space:nowrap}
table.ov .c-rate{min-width:120px}
table.ov .c-num{text-align:center;white-space:nowrap;font-variant-numeric:tabular-nums;color:#2b3547}
/* 行高由页面脚本按「可用高度 / 行数」算出后写进 --rowh，让数据行刚好铺满表格区：
   条数少时自动拉高、条数多时回落到紧凑行高，且不会撑出外层滚动条。 */
table.ov tbody tr{height:var(--rowh,auto)}
/* 比率单元格：百分比 + 灯点（同旧版 .rate-line） */
.rate-line{display:inline-flex;align-items:center;gap:8px}
.rate-line .pct{min-width:52px;font-weight:600;font-variant-numeric:tabular-nums;color:#1b2739}
.rate-line .dot{margin:0}
/* 可点击数字（同旧版 .lnk）：只在有待分析流水线时出现，点进二级页看明细。
   颜色统一用页面链接色 --acc，和正文里的 a 标签、二级页的「查看记录 →」完全一致 */
.lnk{color:var(--acc);cursor:pointer}
.lnk:hover{text-decoration:underline}
td.r,th.r{text-align:right}
td.c,th.c{text-align:center}
.nowrap{white-space:nowrap}
.ell{max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--gray);
  vertical-align:1px;margin-right:5px}
.dot.green{background:var(--green)} .dot.yellow{background:var(--yellow)} .dot.red{background:var(--red)}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;background:#f3f4f6;
  color:#4b5563;border:1px solid var(--line);margin-right:4px;white-space:nowrap}
.tag.red{background:#fef2f2;border-color:#fecaca;color:#b91c1c}
.tag.yellow{background:#fffbeb;border-color:#fde68a;color:#92400e}
.tag.green{background:#f0fdf4;border-color:#bbf7d0;color:#15803d}
.tag.blue{background:#eff6ff;border-color:#bfdbfe;color:#1d4ed8}

/* ---------- 二级页 ---------- */
.rec{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);margin-bottom:12px}
.rec-hd{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 13px;border-bottom:1px solid var(--line2)}
.rec-hd .bn{font-weight:600;font-size:13.5px}
.rec-bd{padding:0 13px 12px}
.rec-sec{margin-top:11px}
.rec-sec .t{font-size:12px;color:var(--dim);margin-bottom:5px}
.rec-sec table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
.rec-sec th{text-align:center;color:#4b5563;font-weight:600;background:#fafbfc;padding:6px 9px;
  border-bottom:1px solid var(--line);white-space:nowrap;position:static}
.rec-sec td{padding:6px 9px;border-bottom:1px solid var(--line2);vertical-align:top}
.empty{color:var(--dim);padding:26px 0;text-align:center}
.ev-list{display:flex;flex-wrap:wrap;gap:6px}
.ev{font-size:11.5px;background:#f8fafc;border:1px solid var(--line);border-radius:6px;
  padding:2px 8px;color:#334155}
.err-line{background:#fbfbfc;border:1px solid var(--line2);border-radius:7px;padding:7px 9px;
  margin-bottom:6px;font-size:12px}
.err-line .src{color:var(--dim);font-size:11px;margin-bottom:2px}
.err-line pre{margin:0;white-space:pre-wrap;word-break:break-all;font-family:Consolas,"Courier New",monospace;
  font-size:11.5px;color:#334155}
.loading{padding:34px 0;text-align:center;color:var(--dim)}
.err{padding:14px;border:1px solid #fecaca;background:#fef2f2;color:#b91c1c;border-radius:var(--radius)}
"""

# 公共日期筛选组件（首页与二级页共用同一份 DOM 结构 + 同一份 JS 行为）
TOOLBAR_HTML = """<div class="toolbar" id="rf">
  <span class="lb">统计区间</span>
  <div class="seg" id="rf-seg">
    <button type="button" data-range="prev">昨天</button>
    <button type="button" data-range="7">近 7 天</button>
    <button type="button" data-range="30">近 30 天</button>
    <button type="button" data-range="90">近 90 天</button>
  </div>
  <span class="vsep"></span>
  <div class="dates">
    <input type="date" id="rf-from">
    <span class="dash">~</span>
    <input type="date" id="rf-to">
  </div>
  <span class="sp"></span>
  <span class="hint" id="rf-hint" title="__RULE__"></span>
  __EXTRA__
</div>""".replace("__RULE__", DATE_RULE)


def toolbar(extra=""):
    """取公共筛选组件 DOM（extra 为右端附加控件，如搜索框/开关）。"""
    return TOOLBAR_HTML.replace("__EXTRA__", extra or "")


HEADER_HTML = """<div class="hd">
  <h1>__BRAND__</h1>
  <p class="meta" id="hsub">__SUB__</p>
</div>"""


def header(sub=None):
    """系统级品牌行（首页/二级页同一份，位于公共筛选条正上方）。

    sub 传 None 时用默认副标题；传入内容后由页面脚本在运行时更新，
    保证两页这行的高度一致 —— 这样它下面的筛选条在切页时不会上下跳。
    """
    return (HEADER_HTML.replace("__BRAND__", BRAND_TITLE)
            .replace("__SUB__", BRAND_SUB if sub is None else sub))


# 公共区间逻辑：URL 参数 > localStorage > 默认（全部）
RANGE_JS = """
/* ==== 公共日期筛选组件（唯一来源 lib/ui.py，口径 = 执行结束时间）==== */
const RF_FIELD = "__FIELD__";
const rfState = {min:null, max:null, prev:null, seg:null, onChange:null};

function rfYmd(d){const p=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());}
function rfShift(ymd, days){const d=new Date(ymd+'T00:00:00');d.setDate(d.getDate()+days);return rfYmd(d);}
/* 区间内工作日数（周一~周五）。只是**兜底**：真正的法定工作日（含节假日/调休补班）
   由第 9 步把 lib/workdays.py 算出的日历注入成 WD_CAL，见下面的 wdInRange()。 */
function rfWorkdays(f, t){
  if (!f || !t) return 0;
  let d = new Date(f + 'T00:00:00'), end = new Date(t + 'T00:00:00'), n = 0;
  while (d <= end && n < 4000){
    const w = d.getDay();
    if (w !== 0 && w !== 6) n++;
    d.setDate(d.getDate() + 1);
  }
  return n;
}
/* 区间内「法定工作日数」= 版本可用度的分母（顶部「工作日 N 天」也用它）。
   WD_CAL 由第 9 步注入（lib/workdays.build_workday_set 的结果，含节假日与调休补班），
   与后端 step_2/step_7 的分母同源；只有区间超出日历覆盖范围
   （早于归档首日 / 晚于今天）时才退回周一~周五估算。 */
function wdInRange(f, t){
  if (!f || !t) return 0;
  const cal = (typeof WD_CAL !== 'undefined' && WD_CAL && WD_CAL.length) ? WD_CAL : null;
  if (cal && f >= cal[0] && t <= cal[cal.length - 1]){
    let n = 0;
    for (let i = 0; i < cal.length; i++){ const d = cal[i]; if (d >= f && d <= t) n++; }
    return n;
  }
  return rfWorkdays(f, t);
}
/* 「这条流水线在这里到底跑没跑过」的唯一判据：runsInPeriod 是第 7 步按区间运行数写死的，
   0 就是一次都没跑（RAGSDK / VisionSDK 这类只有看板配置、从没执行过的流水线）。
   为什么必须判：归档对「区间内无运行」的流水线会退回看板值（历史上是 0.0），
   那个 0 表示「压根没跑」而不是「跑了但不可用」；一旦被当成算出过的比率收进平均值，
   「无执行记录」就会被统计成 0.00%（用户反馈过两次）。老归档缺该字段时放行。 */
function ranInPeriod(pl){
  const v = pl ? pl.runsInPeriod : null;
  if (v === null || v === undefined || v === '') return true;
  return Number(v) > 0;
}
function rfClamp(v){return (v && rfState.min && v<rfState.min) ? rfState.min : ((v && rfState.max && v>rfState.max) ? rfState.max : v);}

/* 快捷段 → 实际区间：7/30/90 以归档最大日为右端，左端不早于归档最小日（数据不足时自动收缩）；
   「昨天」是唯一的例外 —— 它永远是「真实今天减一天」那一天，不吸附归档最大日、不跳过非工作日，
   那天没跑数（周末/节假日）就由页面把指标显示成 -- */
function rfSegRange(r){
  const max = rfState.max || rfYmd(new Date());
  if (r === 'prev'){ const p = rfState.prev || rfShift(rfYmd(new Date()), -1); return {f: p, t: p}; }
  const n = Number(r);
  if (!n) return null;
  let f = rfShift(max, -(n - 1));
  if (rfState.min && f < rfState.min) f = rfState.min;
  return {f: f, t: max};
}

function rfInit(opts){
  opts = opts || {};
  rfState.min = opts.min || null;
  rfState.max = opts.max || null;
  /* 「昨天」的锚点 = 真实今天减一天，不做任何夹取。
     以前这里会把昨天吸附到「归档最大日」，于是周末/节假日点「昨天」，
     看到的其实是上一个工作日的备份数据 —— 看起来就像页面自动跳过了工作日。
     现在昨天就是昨天：那天没有归档，指标就显示 --（不再拿别的日子顶替）。 */
  rfState.prev = rfShift(rfYmd(new Date()), -1);
  rfState.onChange = opts.onChange || null;   /* 不接上 onChange，页面就永远不会按区间取数 */
  const fromEl = document.getElementById('rf-from');
  const toEl   = document.getElementById('rf-to');
  if (rfState.min) { fromEl.min = rfState.min; toEl.min = rfState.min; }
  /* 日期框上界放到「今天」（而不是归档最大日）：归档还没落到昨天时，
     昨天也必须能在日历里选中，选中后由页面显示 -- */
  const cap = (rfState.max && rfState.max > rfYmd(new Date())) ? rfState.max : rfYmd(new Date());
  fromEl.max = cap; toEl.max = cap;

  const qs = new URLSearchParams(location.search);
  const storedSeg = localStorage.getItem('rf_seg') || null;
  /* 每日跑数只归档「前一日」一个目录，所以默认段取「昨天」，与采集口径一致；
     历史多日归档仍然可用「近7/近30/近90天」或自定义区间回看 */
  const def = rfSegRange('prev') || {f: rfState.min, t: rfState.max};
  let from, to;
  if (storedSeg === 'prev'){
    /* 「昨天」是相对日期，每次打开都按当天重算，不沿用上次存下来的旧日期 */
    from = def.f; to = def.t;
  } else {
    from = qs.get('from') || localStorage.getItem('rf_from') || def.f;
    to   = qs.get('to')   || localStorage.getItem('rf_to')   || def.t;
  }
  /* 只兜最小日：右端不再夹到归档最大日，否则「昨天」会被悄悄换成别的日子 */
  if (rfState.min && from < rfState.min) from = rfState.min;
  if (from > to) from = to;
  fromEl.value = from || ''; toEl.value = to || '';

  /* 选中态：seg 只在「当前区间仍是该段算出来的区间」时才算数。
     原实现的致命问题：paintSeg() 读的是 rfInit 的闭包变量 from/to，初始化后永不更新，
     所以点任何快捷段，高亮都停在初始那一段（表现为「点了没反应」）。 */
  let seg = storedSeg;
  function segHit(r, f, t){
    const rg = rfSegRange(r);
    return !!rg && rg.f === f && rg.t === t;
  }
  function deriveSeg(f, t){
    if (!f || !t) return null;
    let hit = null;
    document.querySelectorAll('#rf-seg button').forEach(function(b){
      if (hit === null && segHit(b.dataset.range, f, t)) hit = b.dataset.range;
    });
    return hit;
  }
  function paintSeg(){
    const f = fromEl.value, t = toEl.value;
    if (seg && !segHit(seg, f, t)) seg = null;      // 日期被手动改过 → 丢弃旧段
    const r = seg || deriveSeg(f, t);
    rfState.seg = r;
    document.querySelectorAll('#rf-seg button').forEach(function(b){
      b.classList.toggle('on', b.dataset.range === r);
    });
  }

  function commit(){
    const f = fromEl.value || rfState.min, t = toEl.value || rfState.max;
    fromEl.value = f || ''; toEl.value = t || '';
    localStorage.setItem('rf_from', f || ''); localStorage.setItem('rf_to', t || '');
    localStorage.setItem('rf_seg', seg || '');
    const u = new URL(location.href); u.searchParams.set('from', f || ''); u.searchParams.set('to', t || '');
    history.replaceState(null, '', u);
    paintSeg(); paintHint();
    if (rfState.onChange) rfState.onChange(f, t);
  }
  window.__rfCommit = commit;

  function paintHint(){
    const f = fromEl.value, t = toEl.value;
    const el = document.getElementById('rf-hint');
    if (!f || !t) { if (el) el.textContent = ''; return; }
    const wd = wdInRange(f, t);
    /* 单日且不是工作日：明确写清楚「不是跳过工作日，是这天本来就没跑数」 */
    const idle = (f === t && wd === 0);
    if (el) el.innerHTML = '当前区间 <b>' + f + '</b> ~ <b>' + t + '</b> ｜ 工作日 ' + wd + ' 天'
      + (idle ? ' ｜ <span class="dim">非工作日，当日无跑数，指标显示 --</span>' : '');
  }
  rfState.paintHint = paintHint;

  document.querySelectorAll('#rf-seg button').forEach(function(b){
    b.addEventListener('click', function(){
      const rg = rfSegRange(b.dataset.range);
      if (!rg) return;
      seg = b.dataset.range;                       // 先记下要选中的段，再由 commit 统一落盘
      fromEl.value = rg.f; toEl.value = rg.t;
      commit();
    });
  });
  fromEl.addEventListener('change', function(){ seg = null; if (fromEl.value > toEl.value) toEl.value = fromEl.value; commit(); });
  toEl.addEventListener('change',   function(){ seg = null; if (toEl.value < fromEl.value) fromEl.value = toEl.value; commit(); });
  paintSeg(); paintHint();
  if (rfState.onChange) rfState.onChange(fromEl.value, toEl.value);
}
function rfDays(a, b){ const d1=new Date(a+'T00:00:00'), d2=new Date(b+'T00:00:00'); return Math.round((d2-d1)/86400000)+1; }
function rfIn(ymd){ const f=document.getElementById('rf-from').value, t=document.getElementById('rf-to').value;
  return !!ymd && ymd >= f && ymd <= t; }
function rfEsc(s){ return String(s==null?'':s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
""".replace("__FIELD__", DATE_FIELD)


def page(title, body, script, extra_head="", workdays=None):
    """拼一个自包含页面：样式 + 公共组件 + 空容器 + 取数脚本（不内嵌业务数据）。

    workdays 是 lib/workdays.build_workday_set 算出的**法定工作日日历**（ISO 字符串升序），
    作为版本可用度的分母来源注入成 WD_CAL：页面按用户所选区间就地数工作日，
    与后端 step_2/step_7 的分母同源，跨长假（含调休补班）也不会算错。
    """
    cal = "const WD_CAL = " + json.dumps([str(x) for x in (workdays or [])]) + ";"
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>" + title + "</title>\n<style>" + CSS + extra_head + "</style>\n"
        "</head>\n<body>\n" + body + "\n<script>\n" + cal + "\n" + RANGE_JS + "\n" + script + "\n</script>\n"
        "</body>\n</html>\n"
    )
