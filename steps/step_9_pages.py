# -*- coding: utf-8 -*-
"""第 9 步 生成展示页（首页 + 二级页）—— 只写静态壳，不写任何业务数据。

为什么不内嵌数据：
  数据和 HTML 必须完全隔离。页面里只有样式、公共日期筛选组件和取数脚本，
  业务数字一律在浏览器运行时从 out/archive/ 下的 JSON 读，
  所以归档改了、补抓了、agent 补了根因，页面刷新即生效，不需要重新生成 HTML。

取数链（每一层都带口径与来源，便于核对）：
  首页    archive/_index.json                      → 归档日清单（days[]）
          archive/_products.json                   → 全部开源组件（含无待分析流水线的，行不能缺）
          archive/<年>/<年-月>/<日>/_day.json       → 当日产品点灯 + 流水线索引
  二级页  _day.json.pipelines[].path                → 该流水线当天的归档目录
          <目录>/_manifest.json                     → records[]（构建号/结束时间/定性）
          <目录>/records/run_<构建号>.json          → 单条执行记录全文
  说明：step_6_records.json 单份可达 80KB+，页面**不读它**，只走 _manifest.json 的
        records[] 再逐条读单条记录，既快又不会把整份中间产物塞进浏览器。

口径：首页和二级页都用 lib/ui.py 里的同一个筛选组件，日期一律是【执行结束时间】。
      二级页的区间与首页同源（URL from/to → localStorage），不会出现"首页算进去、
      二级页算不进去"的错位。

⚠ 直接用 file:// 双击打开时，浏览器会拦截 fetch 读取本地 JSON（CORS）。
   请用入口起本地服务：python run_pipeline.py --serve
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ui  # noqa: E402
from lib.paths import DATE_RULE, HTML_DIR, doc, iter_archive_days, rel  # noqa: E402

INDEX_NAME = "index.html"
DETAIL_NAME = "detail.html"
META_NAME = "_pages.json"

INDEX_TITLE = "openLiBing指标分析"
DETAIL_TITLE = "openLiBing指标分析 · 明细"

# ---------------------------------------------------------------- 首页
# 顶部顺序固定为：品牌行 → 全局日期筛选条 → 页面自身标题/表格。
# 二级页用同一份 品牌行 + 筛选条 且排在最前，所以切页时筛选条位置不动。
INDEX_BODY = (
    '<div class="wrap">\n'
    '  <span id="rule" hidden>' + DATE_RULE + '</span>\n'
    '  ' + ui.header() + '\n'
    '  ' + ui.toolbar() + '\n'
    '  <h2>开源组件级概览</h2>\n'
    '  <div class="tbl-wrap">\n'
    '    <table class="ov" id="tbl">\n'
    '      <thead><tr>\n'
    '        <th>开源组件</th>\n'
    '        <th>版本可用度</th>\n'
    '        <th>编译成功率</th>\n'
    '        <th>口径内流水线数</th>\n'
    '        <th>待分析流水线数（版本可用度）</th>\n'
    '        <th>待分析流水线数（编译成功率）</th>\n'
    '      </tr></thead>\n'
    '      <tbody id="tb"><tr><td colspan="6" class="loading">正在读取归档…</td></tr></tbody>\n'
    '    </table>\n'
    '  </div>\n'
    '  <p class="tnote" id="tnote"></p>\n'
    '  <footer id="foot"></footer>\n'
    '</div>'
)

INDEX_EXTRA_CSS = """
/* 首页整页固定成一屏：html/body 的滚动全部关掉，页面本身永远不出滚动条。
   之前只给 .wrap 定了高，卡片底部那 12px 外边距会溢出 body，页面就多出 12px 的滚动条
   （右侧那条细滚动条就是这么来的）；现在把滚动彻底关在首页之外。 */
html,body{height:100%;overflow:hidden}
/* 卡片高度 = 一屏减去上下外边距；只有主尺寸确定，表格区的 flex:1 才会真的分到
   「剩余高度」，fitRows() 也才能算出「13 条数据刚好铺满」的行高。
   数据变多时只在表格区内部滚动，滚动条留在表格里，不会跑到页面上。 */
.wrap{height:calc(100vh - 24px)}
.tbl-wrap{min-height:0}
/* 表下口径说明 + 页脚（对齐旧报告版式）：不参与收缩，高度不够时优先压表格区而不是裁文字 */
p.tnote{flex:0 0 auto;font-size:11.5px;color:#7c8698;line-height:1.75;margin:9px 0 0}
footer#foot{flex:0 0 auto;font-size:11.5px;color:#9aa3af;line-height:1.7;margin-top:7px;padding-top:8px;
  border-top:1px solid var(--line2)}
"""

INDEX_JS = r"""
/* ================= 首页：读归档 JSON，按执行结束时间区间聚合 ================= */
/* 数据根：一律相对「当前文档」算，全程不出现域名也不写死站点根。
   正常情况页面在 out/html/ 下，第一枪 '../' 就命中 out/archive/；
   万一整站被放到别的层级（比如直接以 out/ 为站点根），后面几枪能自己找到，
   不会因为层级差一层就整页空白。 */
let ARCH_ROOT = '../';
const ROOT_CANDIDATES = ['../', './', '../../', '../../../'];
function probeRoot(){
  let i = 0;
  function step(){
    if (i >= ROOT_CANDIDATES.length){
      return Promise.reject(new Error('archive/_index.json → HTTP 404（已依次试过：'
        + ROOT_CANDIDATES.join('  ') + '）'));
    }
    const cand = ROOT_CANDIDATES[i++];
    return jget(cand + 'archive/_index.json').then(function(x){
      ARCH_ROOT = cand;          /* 命中后就固定下来，后面所有数据都拼这个前缀 */
      return x;
    }).catch(function(e){
      if (isNotFound(e)) return step();
      throw e;
    });
  }
  return step();
}

const DETAIL_PAGE = '__DETAIL__';   /* 二级页文件名（构建时注入，避免 JS 里出现未定义变量） */
const LIGHT_RANK = {'红': 3, '黄': 2, '绿': 1};
let IDX = null;
let CATALOG = [];        /* archive/_products.json：全部开源组件（含无待分析流水线的） */

function jget(p){
  return fetch(p, {cache: 'no-store'}).then(function(r){
    if (!r.ok) throw new Error(p + ' → HTTP ' + r.status);
    return r.json();
  });
}
/* 文件不存在（归档区还没跑过采集）与「读取失败」要分开：前者提示去采集，后者才是环境问题 */
function isNotFound(e){
  const m = String((e && e.message) || e);
  return /HTTP 40[34]/.test(m) || /\b(404|ENOENT)\b/.test(m) || /not\s*found/i.test(m);
}
function nz(v){ return (v === null || v === undefined || v === '') ? '--' : String(v); }
function fmtPct(v){
  return (v === null || v === undefined || v === '' || isNaN(v)) ? '--' : (Number(v) * 100).toFixed(1) + '%';
}
function avgOf(a){ if (!a.length) return null; let s = 0; a.forEach(function(v){ s += Number(v); }); return s / a.length; }
/* 灯色一律就地按比率判档（归档只存数值，不存黄/绿/红）：
     版本可用度 >=90% 绿 / >=80% 黄 / 否则红；编译成功率 100% 绿 / >=95% 黄 / 否则红
   老归档里还留着 vaLight/bsLight 字段，仅作字段缺失时的兜底。 */
function lightByRate(v, kind){
  if (v === null || v === undefined || v === '' || isNaN(v)) return null;
  const r = Number(v);
  if (kind === 'bs') return r >= 1 ? '绿' : (r >= 0.95 ? '黄' : '红');
  return r >= 0.9 ? '绿' : (r >= 0.8 ? '黄' : '红');
}
function worstLight(a, b){
  if (!a || a === '无数据') return b || a;
  if (!b || b === '无数据') return a;
  return (LIGHT_RANK[b] || 0) > (LIGHT_RANK[a] || 0) ? b : a;
}
/* 归档里分子/分母的中文名（区间重算说明用） */
const DET_LABEL = {availDays: '可用日', daysInPeriod: '有运行日',
                   buildPassCount: '编译通过', buildCount: '编译次数'};

/* 区间重算：把逐日的分子/分母各自累加后相除（Σ可用日/Σ有运行日、Σ编译通过/Σ编译次数）。
   逐日比率的算术平均会被「分母不同的日期」带偏，统计区间一变必须按分子分母重算。
   老归档没有 *Detail 字段时退回逐日比率平均，并标记 mixed=true 说明口径。 */
function accRate(dayDocs, rateKey, detKey, numKey, denKey){
  let num = 0, den = 0, mixed = false;
  const vals = [];
  dayDocs.forEach(function(dd){
    const d = dd ? dd[detKey] : null;
    const raw = dd ? dd[rateKey] : null;
    const n = d ? Number(d[numKey]) : NaN;
    const m = d ? Number(d[denKey]) : NaN;
    if (!isNaN(n) && !isNaN(m) && m > 0) { num += n; den += m; }
    else if (raw !== null && raw !== undefined && raw !== '' && !isNaN(raw)) {
      vals.push(Number(raw)); mixed = true;
    }
  });
  if (den > 0) return {rate: num / den, num: num, den: den, numKey: numKey, denKey: denKey, mixed: mixed};
  if (vals.length) return {rate: avgOf(vals), num: null, den: null, numKey: numKey, denKey: denKey, mixed: true};
  return null;
}

/* 比率单元格的 title：先写区间重算的分子/分母（区间一改它就改），再列逐日原始值备查 */
function rateTitle(label, acc, dayLines){
  if (!acc) return '';
  const lines = [label + '（按统计区间重算）'];
  lines.push(acc.num !== null
    ? 'Σ' + (DET_LABEL[acc.numKey] || acc.numKey) + ' / Σ' + (DET_LABEL[acc.denKey] || acc.denKey)
      + ' = ' + acc.num + ' / ' + acc.den + ' → ' + fmtPct(acc.rate)
    : '归档没有分子/分母字段，按逐日比率取平均 → ' + fmtPct(acc.rate));
  if (acc.mixed) lines.push('注意：部分归档日缺分子/分母，只按有明细的日期累加');
  lines.push('逐日原始值（仅备查）：');
  return lines.concat(dayLines).join('\n');
}
function dotOf(l){ const m = {'绿': 'green', '黄': 'yellow', '红': 'red'}; return m[l] ? '<span class="dot ' + m[l] + '"></span>' : ''; }
function failBox(msg){
  const box = document.getElementById('tb');
  if (box) box.innerHTML = '<tr><td colspan="6"><div class="err">' + msg + '</div></td></tr>';
}

/* 静态托管（GitHub Pages 等）上归档取不到，九成是 Jekyll 把 _ 开头的文件整批过滤了：
   归档索引正好叫 archive/_index.json、archive/<日>/_day.json、<流水线>/_manifest.json。
   本地 http 服务上取不到才是「还没采集」。两种环境给两种提示，
   避免页面在线上让人去跑 run_pipeline（静态页本来就不该跑采集）。 */
function hostedSite(){
  const p = location.protocol, h = String(location.hostname || '');
  if (p !== 'http:' && p !== 'https:') return false;
  if (!h || h === 'localhost' || h === '127.0.0.1') return false;
  if (/^(10|192\.168)\./.test(h) || /^172\.(1[6-9]|2\d|3[01])\./.test(h)) return false;
  return true;
}
function emptyArchiveMsg(){
  if (!hostedSite()) {
    return '归档区暂无数据：请先执行 <span class="mono">python run_pipeline.py</span> 采集并归档。';
  }
  /* 线上还取不到，先别急着怪静态托管：把实际请求的地址打出来，
     一按 F12 就能看出是路径拼错还是托管层没发布这个文件。 */
  return '<b>归档索引没取到（HTTP 404）</b>'
    + '<div style="margin-top:8px">实际请求：<span class="mono">' + rfEsc(ARCH_ROOT + 'archive/_index.json') + '</span></div>'
    + '<div style="margin-top:8px">请按 F12 → Network 看这条请求的最终 URL 与状态码：</div>'
    + '<div style="margin-top:6px">· 若最终 URL 少了 <span class="mono">out/</span> 或多了一层目录 → 页面层级不对，'
    + '应该访问 <span class="mono">&lt;站点根&gt;/out/html/index.html</span>；</div>'
    + '<div style="margin-top:6px">· 若状态码就是 404 但这个文件仓库里确实有 → 是托管层没把它发布出去'
    + '（GitHub Pages 的 Jekyll 会跳过 <span class="mono">_</span> 开头的文件，'
    + '需要在仓库根加一份空的 <span class="mono">.nojekyll</span>）。</div>';
}

function boot(){
  /* 先用相对路径探测数据根，命中后再取组件清单：
       _index.json    → 归档日清单（决定日期控件上下界）
       _products.json → 全部开源组件清单（首页要「列出全部开源组件」，不能只列有待分析的） */
  const sub = document.getElementById('hsub');
  probeRoot().then(function(idx){
    IDX = idx;
    return jget(ARCH_ROOT + 'archive/_products.json').catch(function(){ return {products: []}; });
  }).then(function(prod){
    CATALOG = (prod && prod.products) || [];
    const days = (IDX.days || []).slice().sort(function(a, b){ return a.date < b.date ? -1 : 1; });
    if (!days.length){
      if (sub) sub.textContent = '归档区暂无数据';
      failBox(emptyArchiveMsg());
      return;
    }
    if (sub) sub.textContent = '归档 ' + days.length + ' 天（' + days[0].date + ' ~ '
      + days[days.length - 1].date + '）· 开源组件 ' + (CATALOG.length || '--')
      + ' 个 · 数据源 out/archive/';
    rfInit({
      min: days[0].date,
      max: days[days.length - 1].date,
      onChange: load
    });
  }).catch(function(e){
    /* 归档区为空时 _index.json 本来就不存在（HTTP 404 / ENOENT），这不叫「读取失败」，
       直接提示去采集；只有 file:// 直开被浏览器拦截（TypeError）才需要提示起本地服务 */
    if (isNotFound(e)){
      if (sub) sub.textContent = '归档区暂无数据';
      failBox(emptyArchiveMsg());
      return;
    }
    if (sub) sub.textContent = '读取归档索引失败';
    if (hostedSite()){
      /* 线上取索引被拦（403/5xx/跨域）——不是本地环境问题，别教人跑 python */
      failBox('<b>读取归档索引失败</b>'
        + '<div class="mono" style="margin-top:6px">' + rfEsc(String(e)) + '</div>'
        + '<div style="margin-top:8px">静态托管环境读不到 '
        + '<span class="mono">archive/_index.json</span>。请确认该文件已随仓库提交、'
        + '仓库根目录有 <span class="mono">.nojekyll</span>、Pages 已重新部署。</div>');
      return;
    }
    failBox('<b>读取归档索引失败</b>'
      + '<div class="mono" style="margin-top:6px">' + rfEsc(String(e)) + '</div>'
      + '<div style="margin-top:8px">直接双击打开（file://）时浏览器会拦截 fetch 读取本地 JSON。'
      + '请在 out 目录起本地服务后访问：<br><span class="mono">python run_pipeline.py --serve</span>'
      + '<br>或 <span class="mono">python -m http.server 8765 --directory out</span>'
      + '，再打开 <span class="mono">http://localhost:8765/html/index.html</span></div>');
  });
}

function load(from, to){
  const tb = document.getElementById('tb');
  /* 区间提示统一由公共筛选条右侧的 #rf-hint 负责（「当前区间 X ~ Y ｜ 工作日 N 天」），
     页面自身标题/副标题保持恒定，切页与改区间都不会让顶部内容跳动。 */
  const days = (IDX.days || []).filter(function(d){ return d.date >= from && d.date <= to; });
  if (!days.length){
    /* 区间内没有归档日（最常见的是「昨天」落在周末/节假日，那天本来就不跑数）：
       表格照常铺满全部开源组件，比率与待分析数一律显示 -- / 0，
       不再用一行提示把整张表替换掉 —— 「不是工作日」就该看到 --，而不是别的日子。 */
    render([], from, to);
    return;
  }
  tb.innerHTML = '<tr><td colspan="6" class="loading">正在读取 ' + days.length + ' 个归档日…</td></tr>';
  Promise.all(days.map(function(d){
    return jget(ARCH_ROOT + d.path + '/_day.json').then(function(x){ x.__path = d.path; return x; })
      .catch(function(e){ return {date: d.date, products: [], pipelines: [], __err: String(e), __path: d.path}; });
  })).then(function(docs){
    docs.sort(function(a, b){ return a.date < b.date ? -1 : 1; });
    render(docs, from, to);
  });
}

function render(docs, from, to){
  const prods = new Map();
  const pipes = new Map();
  const broken = docs.filter(function(d){ return d.__err; });

  /* 先铺全量组件清单：归档日只记录「当天下钻到的产品」，当天没有待分析流水线的产品
     不会出现在 _day.json 里；不全量铺底就会整行消失（用户要求：全部开源组件都列出，
     没有待分析流水线的显示 0）。 */
  CATALOG.forEach(function(p){
    prods.set(p.name, {name: p.name, projectId: p.projectId, days: {}, inCatalog: true});
  });

  docs.forEach(function(dd){
    (dd.products || []).forEach(function(p){
      let r = prods.get(p.product);
      if (!r){ r = {name: p.product, projectId: p.projectId, days: {}}; prods.set(p.product, r); }
      r.days[dd.date] = p;
    });
    (dd.pipelines || []).forEach(function(pl){
      let r = pipes.get(pl.key);
      if (!r){
        r = {key: pl.key, component: pl.component, pipeline: pl.pipeline, name: pl.pipelineName || pl.pipeline,
             platform: pl.platform || '', dims: {}, dates: [], path: pl.path,
             vaA: 0, vaN: 0, bsP: 0, bsC: 0, vaVals: [], bsVals: [],
             vaLightOld: null, bsLightOld: null};
        pipes.set(pl.key, r);
      }
      (pl.dims || []).forEach(function(d){ r.dims[d] = 1; });
      /* 区间重算：逐日累加分子/分母，区间一变就按新的 Σ分子/Σ分母 重算，
         不是把逐日比率做算术平均。老归档无 vaDetail/bsDetail 才退回逐日比率平均。 */
      const vd = pl.vaDetail, bd = pl.bsDetail;
      if (vd && Number(vd.daysInPeriod) > 0){
        r.vaA += Number(vd.availDays) || 0; r.vaN += Number(vd.daysInPeriod) || 0;
      } else if (pl.vaRate !== null && pl.vaRate !== undefined){
        r.vaVals.push(Number(pl.vaRate));
      }
      if (bd && Number(bd.buildCount) > 0){
        r.bsP += Number(bd.buildPassCount) || 0; r.bsC += Number(bd.buildCount) || 0;
      } else if (pl.bsRate !== null && pl.bsRate !== undefined){
        r.bsVals.push(Number(pl.bsRate));
      }
      r.vaLightOld = worstLight(r.vaLightOld, pl.vaLight || null);
      r.bsLightOld = worstLight(r.bsLightOld, pl.bsLight || null);
      if (r.dates.indexOf(dd.date) < 0) r.dates.push(dd.date);
      r.path = pl.path;
    });
  });

  const pipeList = Array.from(pipes.values());
  /* 流水线级区间重算：Σ可用日/Σ有运行日、Σ编译通过/Σ编译次数；灯色只按重算比率判档 */
  pipeList.forEach(function(p){
    p.vaRate = p.vaN > 0 ? p.vaA / p.vaN : avgOf(p.vaVals);
    p.bsRate = p.bsC > 0 ? p.bsP / p.bsC : avgOf(p.bsVals);
    p.vaLight = lightByRate(p.vaRate, 'va') || p.vaLightOld;
    p.bsLight = lightByRate(p.bsRate, 'bs') || p.bsLightOld;
  });

  const rows = Array.from(prods.values()).map(function(r){
    const ds = Object.keys(r.days).sort();
    const dayDocs = ds.map(function(d){ return r.days[d]; });
    const mine = pipeList.filter(function(p){ return p.component === r.name; });
    /* 组件级同样按区间重算：分子分母逐日累加后相除（归档里带 *Detail 字段） */
    const vaAcc = accRate(dayDocs, 'versionAvailabilityRate',
                          'versionAvailabilityDetail', 'availDays', 'daysInPeriod');
    const bsAcc = accRate(dayDocs, 'buildSuccessRate',
                          'buildSuccessDetail', 'buildPassCount', 'buildCount');
    const vaRate = vaAcc ? vaAcc.rate : null;
    const bsRate = bsAcc ? bsAcc.rate : null;
    return {
      name: r.name,
      nd: ds.length,
      days: ds,
      vaRate: vaRate,
      bsRate: bsRate,
      vaLight: lightByRate(vaRate, 'va') || ds.reduce(function(a, d){ return worstLight(a, r.days[d].vaLight); }, null),
      bsLight: lightByRate(bsRate, 'bs') || ds.reduce(function(a, d){ return worstLight(a, r.days[d].bsLight); }, null),
      vaTitle: rateTitle('版本可用度', vaAcc, ds.map(function(d, i){
        return d + '  ' + fmtPct(dayDocs[i].versionAvailabilityRate); })),
      bsTitle: rateTitle('编译成功率', bsAcc, ds.map(function(d, i){
        return d +  '  ' + fmtPct(dayDocs[i].buildSuccessRate); })),
      pipes: mine,
      pipeCount: mine.length,
      badVA: mine.filter(function(p){ return p.dims.va && p.vaLight && p.vaLight !== '绿'; }).length,
      badBS: mine.filter(function(p){ return p.dims.bs && p.bsLight && p.bsLight !== '绿'; }).length
    };
  });
  rows.sort(function(a, b){ return ((b.badVA + b.badBS) - (a.badVA + a.badBS)) || (a.name < b.name ? -1 : 1); });

  const tb = document.getElementById('tb');
  if (!rows.length && !pipeList.length){
    tb.innerHTML = '<tr><td colspan="6" class="empty">'
      + (docs.length ? '该区间内的归档日没有流水线进入待分析集合'
                     : '该区间内没有归档日（非工作日不跑数），指标显示 --')
      + '</td></tr>';
    paintNotes(docs, [], from, to);
    return;
  }
  const html = rows.map(function(r){ return prodRow(r, from, to); }).join('');
  tb.innerHTML = html + (broken.length
    ? '<tr><td colspan="6"><div class="err" style="margin:8px 0">'
      + broken.length + ' 个归档日的 _day.json 读取失败：'
      + broken.map(function(b){ return rfEsc(b.date); }).join('、') + '</div></td></tr>' : '');
  paintNotes(docs, rows, from, to);
  fitRows();
}

/* 首页只有这一张表，所以行高要跟着可视区走：整张表恰好铺满表格区（13 条数据刚好吃满），
   既不出现外层滚动条，也不在下面留一大片空白。
   做法：表格容器拿的是卡片内的剩余高度（.tbl-wrap 是 flex:1）→ 减掉表头后按行数均分，
   结果写进 --rowh（table.ov tbody tr 直接用它作行高）。
   行高有 30~70px 的下限/上限：条数多时保持紧凑、条数少时也不会被拉成一根细条。 */
function fitRows(){
  const wrap = document.querySelector('.tbl-wrap');
  const tbl = document.getElementById('tbl');
  if (!wrap || !tbl || !tbl.tBodies.length) return;
  const tb = tbl.tBodies[0];
  const n = tb.rows.length;
  if (!n || (n === 1 && tb.rows[0].cells.length === 1)){
    tbl.style.removeProperty('--rowh');      /* loading / 无数据 提示行不参与铺满 */
    return;
  }
  const th = tbl.tHead ? tbl.tHead.offsetHeight : 0;
  const avail = wrap.clientHeight - th - 2;  /* 减掉表格上下边框，避免差 1~2px 撑出滚动条 */
  if (avail <= 0) return;
  const h = Math.floor(avail / n);
  if (h < 34){                                /* 可视区太矮：回到自然行高，允许表格内滚动 */
    tbl.style.removeProperty('--rowh');
    return;
  }
  tbl.style.setProperty('--rowh', Math.min(h, 120) + 'px');
}
window.addEventListener('resize', function(){ if (IDX) fitRows(); });
window.addEventListener('load', function(){ if (IDX) fitRows(); });

/* 有数据时版本可用度/编译成功率单元格：百分比 + 灯点，title 里带逐日明细 */
function rateCell(light, title, rate){
  const t = title ? ' title="' + rfEsc(title) + '"' : '';
  if (!light || light === '无数据') return '<span class="rate-line"' + t + '><span class="pct">--</span></span>';
  return '<span class="rate-line"' + t + '><span class="pct">' + fmtPct(rate) + '</span>'
       + dotOf(light) + '</span>';
}

/* 待分析流水线数：0 就写 0；大于 0 才是可点数字（同旧版 .lnk），点进二级页看该组件的流水线清单 */
function numCell(n, comp, dim, q){
  if (!n) return '0';
  return '<a class="lnk" href="' + DETAIL_PAGE + '?comp=' + encodeURIComponent(comp)
    + '&dim=' + dim + q + '">' + n + '</a>';
}

/* 组件行：严格对齐旧版 6 列，不做任何展开（明细一律在二级页看） */
function prodRow(r, from, to){
  const q = '&from=' + encodeURIComponent(from) + '&to=' + encodeURIComponent(to);
  return '<tr>'
    + '<td class="c-name">' + rfEsc(r.name) + '</td>'
    + '<td class="c-rate">' + rateCell(r.vaLight, r.vaTitle, r.vaRate) + '</td>'
    + '<td class="c-rate">' + rateCell(r.bsLight, r.bsTitle, r.bsRate) + '</td>'
    + '<td class="c-num">' + r.pipeCount + '</td>'
    + '<td class="c-num">' + numCell(r.badVA, r.name, 'va', q) + '</td>'
    + '<td class="c-num">' + numCell(r.badBS, r.name, 'bs', q) + '</td>'
    + '</tr>';
}

/* 表下口径说明 + 页脚：说明写清楚，避免「结果被挑战」，也给二级页同一份口径 */
function paintNotes(docs, rows, from, to){
  let badVA = 0, badBS = 0, pipes = 0, comps = 0, red = 0;
  rows.forEach(function(r){
    badVA += r.badVA; badBS += r.badBS; pipes += r.pipeCount;
    if (r.pipeCount) comps += 1;
    if (r.vaLight === '红' || r.bsLight === '红') red += 1;
  });
  const note = document.getElementById('tnote');
  const rule = document.getElementById('rule');
  if (note) note.innerHTML =
    '口径：<b>' + (rule ? rule.textContent : '') + '</b>，即流水线执行结束时间（GitHub <span class="mono">run.updated_at</span>）'
    + '落在统计区间内才计入；版本可用度 = Σ可用日 / Σ有运行日，编译成功率 = Σ编译通过 / Σ编译次数，'
    + '<b>统计区间一变即按新区间的分子分母重算</b>（不是逐日比率取平均），'
    + '灯色不落库、按重算后的比率就地判档（可用度 &ge;90% 绿 / &ge;80% 黄 / 否则红；'
    + '编译成功率 100% 绿 / &ge;95% 黄 / 否则红），区间内只出这一档。'
    + '「待分析流水线数」= 该组件在区间内进入待分析集合、且对应维度重算后未达标的流水线条数，'
    + '与看板「待分析」判定一致，不是失败次数。';
  const foot = document.getElementById('foot');
  if (foot) foot.innerHTML =
    '生成时间 <span class="mono">' + rfEsc(nz(IDX && IDX.generatedAt)) + '</span> ｜ '
    + '统计区间 <span class="mono">' + rfEsc(from) + ' ~ ' + rfEsc(to) + '</span>（归档 '
    + docs.length + ' 天）｜ 开源组件 ' + rows.length + ' 个（有待分析流水线 ' + comps
    + ' 个，红灯 ' + red + ' 个）｜ 待分析流水线 ' + pipes + ' 条（可用度 ' + badVA
    + ' · 编译 ' + badBS + '）｜ 数据源 <span class="mono">out/archive/</span>'
    /* 区间内一天归档都没有：说明白是「这天没跑数」，指标才显示 -- */
    + (docs.length ? '' : ' ｜ <b>该区间内无归档日</b>（非工作日不跑数，指标显示 --）');
}

/* 说明：首页表格不做任何展开/下钻，只有「待分析流水线数」是可点数字，进二级页看该组件的流水线清单 */

boot();
""".replace("__DETAIL__", DETAIL_NAME)

# ---------------------------------------------------------------- 二级页
# 顶部顺序与首页**逐像素一致**：品牌行 → 公共日期筛选条 → 才是本页自己的东西
# （返回总览 / 流水线标题 / 面包屑 / 概要）。两页共用同一份 header + toolbar，
# 且都贴在最前，所以从首页点进来时「统计区间」整条不换位置、不跳动，
# 语义始终是「整个系统的数据筛选」，而不是某一页的局部筛选。
DETAIL_BODY = (
    '<div class="wrap">\n'
    '  ' + ui.header() + '\n'
    '  ' + ui.toolbar() + '\n'
    '  <a class="chip" href="' + INDEX_NAME + '" id="back">← 返回总览</a>\n'
    '  <h1 id="ttl">流水线根因明细</h1>\n'
    '  <div class="crumb" id="crumb"></div>\n'
    '  <p class="meta" id="meta">正在读取归档…</p>\n'
    '  <div class="dist" id="dist"></div>\n'
    '  <div id="recs"><div class="loading">正在读取归档记录…</div></div>\n'
    '</div>\n'
    '<div class="modal" id="mrec">\n'
    '  <div class="modal-box">\n'
    '    <div class="modal-hd"><span id="mttl"></span>'
    '<button type="button" id="mclose">关闭</button></div>\n'
    '    <div id="mbody"></div>\n'
    '  </div>\n'
    '</div>'
)

# 版式对齐旧报告：h3 区块头（浅蓝底）+ table.rca（全边框/表头浅灰/斑马纹/列宽固定）
DETAIL_EXTRA_CSS = """
#back{margin:2px 0 8px;align-self:flex-start}
/* 组件级流水线清单的比率：纯数字百分比（80.0%），不带圆点/灯色 */
table.ov td.c-rate .pct{font-weight:600;font-variant-numeric:tabular-nums;color:#1b2739}
/* 可点的比率（有运行历史、点了弹「构建记录」）必须显成链接色：
   选择器要比上一行更具体才压得住，颜色与正文 a 标签、「查看记录 →」同一个 --acc */
table.ov td.c-rate .pct.lnk{color:var(--acc);cursor:pointer}
table.ov td.c-rate .pct.lnk:hover{text-decoration:underline}
.crumb{margin:2px 0 4px;font-size:13px;color:#666}
.crumb b{color:#222}
/* 面包屑逐层可点：总览 › 组件（维度）› 流水线，点哪一层回哪一层 */
.crumb a{color:var(--acc)}
.crumb .sep{color:#c2cad6;margin:0 5px}
.grow{flex:1 1 auto}

/* ---------- 流水线区块头（同旧报告 h3） ---------- */
.subview h3{font-size:15px;font-weight:600;color:#16233a;line-height:1.6;
  margin:22px 0 8px;padding:8px 12px;background:#f0f5ff;border-radius:6px}
.subview h3:first-of-type{margin-top:8px}
.subview h3 .hist{font-size:12px;font-weight:400;margin-left:8px}
.subview h3 .rate-line{margin-left:8px;font-weight:400;font-size:13px}

/* ---------- 根因明细表（同旧报告 table.rca）：7 列固定列宽，行宽不随内容变化 ---------- */
table.rca{table-layout:fixed;width:100%;border-collapse:collapse;font-size:13px;margin:8px 0 4px}
table.rca th,table.rca td{border:1px solid #e3e6ea;padding:7px 9px;text-align:left;
  vertical-align:top;line-height:1.55;word-break:break-word;overflow-wrap:anywhere}
table.rca th{background:#f7f9fc;font-weight:600;white-space:nowrap;color:#3b4a60}
table.rca tbody tr:nth-child(even) td{background:#fafbfc}
table.rca td.err-cell{font-size:12.5px}
/* 错误信息列：日志原文必须在列宽内换行（长路径 testcase/cann/... 没有空格，
   只靠 word-break 会撑破列），并按行数截断，超出部分留省略号而不是溢出到下一列 */
table.rca td.err-cell .err-text{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:4;
  overflow:hidden;white-space:normal;word-break:break-all;overflow-wrap:anywhere}
/* 「证据」链接单独一行、不参与上面的行数截断，保证永远可点 */
table.rca td.err-cell .ev-lnk{display:inline-block;margin-top:3px}
/* 弹窗里的长文本（用例失败详情 / 日志证据原文）：整行完整输出，不截断、不溢出 */
table.rca td.expr-txt{white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;
  font-family:Consolas,Menlo,monospace;font-size:12px;line-height:1.5}
/* 第 1 列「构建编号」里的构建号链接不折行 */
table.rca td:nth-child(1) a{white-space:nowrap}
.exec-sub{display:block;color:#999;font-size:11px;margin-top:2px}
.badge{display:inline-block;padding:1px 9px;border-radius:10px;color:#fff;font-size:12px;vertical-align:1px}
.badge.green{background:#52c41a}.badge.yellow{background:#faad14}
.badge.red{background:#f5222d}.badge.gray{background:#999}

/* ---------- 弹窗骨架：三个入口共用（失败用例 / 日志证据 / 构建记录） ----------
   版式固定成「标题 → 表格区（唯一滚动区）→ 脚注」三段：
   外框只做定高、overflow:hidden，所以弹窗自己永不出滚动条；
   纵向滚动条贴着表格右边缘、横向滚动条贴着表格底边，滚动的始终是表格本身。 */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:50;
  align-items:center;justify-content:center;padding:24px 16px;overflow:hidden}
.modal.on{display:flex}
.modal-box{background:#fff;border-radius:10px;width:min(1280px,96vw);
  max-height:calc(100vh - 48px);overflow:hidden;
  padding:16px 20px;box-shadow:0 8px 30px rgba(16,32,64,.2);
  display:flex;flex-direction:column}
.modal-hd{display:flex;justify-content:space-between;align-items:center;gap:10px;flex:0 0 auto;
  font-weight:600;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid var(--line2)}
.modal-hd button{background:#fff;border:1px solid #ccc;border-radius:6px;padding:3px 12px;cursor:pointer;font:inherit}
/* 弹窗正文 = 表格区（可滚，吃掉全部剩余高度）+ 脚注（固定不动） */
#mbody{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
/* 表格独占的滚动容器：纵向滚动条在表格右侧、横向滚动条在表格下方，弹窗本身不滚 */
.mtbl{flex:1 1 auto;min-height:0;overflow:auto}
.mtbl table.rca{margin:0}
/* 表头吸顶：表格内滚动时列名始终可见 */
.mtbl table.rca thead th{position:sticky;top:0;z-index:2;background:#f7f9fc;
  box-shadow:inset 0 -1px 0 #e3e6ea,inset 0 1px 0 #e3e6ea}
/* 弹窗表格里的长内容列：一律在列宽内换行（长路径/用例名没有空格也能断），
   绝不横向溢出到相邻列 —— 列宽由 colgroup 固定，行高随内容自然增长 */
table.rca td.cw{white-space:normal;word-break:break-all;overflow-wrap:anywhere}
/* 时间列：优先在「日期」和「时刻」之间的空格处折行，不要从毫秒中间断开 */
table.rca td.cw1{white-space:normal;word-break:normal;overflow-wrap:anywhere}
/* 定性分布：二级页字段，按执行记录计数 */
.dist{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin:8px 0 10px;
  padding:8px 12px;background:#fff;border:1px solid var(--line);border-radius:8px;font-size:12px}
.dist:empty{display:none}
.dist .dt{color:#374151;font-weight:600;margin-right:2px}
.dist .sp{flex:1 1 auto}

.mrec-note{margin-top:6px;color:#999;font-size:12px;flex:0 0 auto}
"""

DETAIL_JS = r"""
/* ================= 二级页：同口径区间 → 该流水线每天的归档目录 → 逐条执行记录 ================= */
/* 数据根：同首页，一律相对「当前文档」逐级探测 archive/_index.json，不写死域名和站点根 */
let BACK_TO = '../';
const ROOT_CANDIDATES = ['../', './', '../../', '../../../'];
function probeRoot(){
  let i = 0;
  function step(){
    if (i >= ROOT_CANDIDATES.length){
      return Promise.reject(new Error('archive/_index.json → HTTP 404（已依次试过：'
        + ROOT_CANDIDATES.join('  ') + '）'));
    }
    const cand = ROOT_CANDIDATES[i++];
    return jget(cand + 'archive/_index.json').then(function(x){
      BACK_TO = cand;            /* 命中后固定，后面所有归档请求都拼这个前缀 */
      return x;
    }).catch(function(e){
      if (isNotFound(e)) return step();
      throw e;
    });
  }
  return step();
}
const CATS = {
  compile_failure: ['编译失败', 'red'],
  test_failure:    ['用例失败', 'red'],
  env_not_ready:   ['用例未拉起', 'yellow'],
  job_failure:     ['任务失败', 'yellow'],
  cancelled:       ['运行被取消', ''],
  evidence_missing:['证据缺失', 'blue'],
  unknown:         ['证据不足待确认', '']
};
const QS = new URLSearchParams(location.search);
const KEY = QS.get('key') || '';        /* 流水线级：组件|流水线 */
const COMP = QS.get('comp') || '';      /* 组件级：从首页「待分析流水线数」点进来的 */
const DIM = QS.get('dim') || '';        /* 组件级下的维度过滤：va / bs */
const DIMNAME = {va: '版本可用度', bs: '编译成功率'};
/* 流水线级页面上「上一级」是哪个组件：优先用链接带过来的 comp，退化到 key 的组件前缀 */
const COMP_OF_KEY = COMP || (KEY ? String(KEY).split('|')[0] : '');
const INDEX_PAGE = '__INDEX__';          /* 首页文件名（构建时注入） */
let DAYS = [];

function jget(p){
  return fetch(p, {cache: 'no-store'}).then(function(r){
    if (!r.ok) throw new Error(p + ' → HTTP ' + r.status);
    return r.json();
  });
}
/* 文件不存在（归档区还没跑过采集）与「读取失败」要分开：前者提示去采集，后者才是环境问题 */
function isNotFound(e){
  const m = String((e && e.message) || e);
  return /HTTP 40[34]/.test(m) || /\b(404|ENOENT)\b/.test(m) || /not\s*found/i.test(m);
}
function nz(v){ return (v === null || v === undefined || v === '') ? '--' : String(v); }
function dotOf(l){ const m = {'绿': 'green', '黄': 'yellow', '红': 'red'}; return m[l] ? '<span class="dot ' + m[l] + '"></span>' : ''; }
/* 灯色不落库：归档只存数值，这里按比率就地判档（老归档的 vaLight/bsLight 只作兜底）。
   版本可用度 >=90% 绿 / >=80% 黄 / 否则红；编译成功率 100% 绿 / >=95% 黄 / 否则红 */
function lightByRate(v, kind){
  if (v === null || v === undefined || v === '' || isNaN(v)) return null;
  const r = Number(v);
  if (kind === 'bs') return r >= 1 ? '绿' : (r >= 0.95 ? '黄' : '红');
  return r >= 0.9 ? '绿' : (r >= 0.8 ? '黄' : '红');
}

/* ===== 逐层下钻与逐层返回 =====
   首页（组件级概览）→ 组件级流水线清单（?comp=&dim=）→ 流水线根因（?key=）。
   每一层的链接都显式带上 comp / dim / from / to，所以：
   1) 返回按钮只回上一层，不会一步跳回首页；
   2) 回到上一层后，维度过滤和统计区间都保持不变（不用重新选日期）。 */
function rangeQS(from, to){
  return (from && to) ? 'from=' + encodeURIComponent(from) + '&to=' + encodeURIComponent(to) : '';
}
function levelQS(extra, from, to){
  const parts = [];
  Object.keys(extra || {}).forEach(function(k){
    if (extra[k] !== null && extra[k] !== undefined && extra[k] !== '') parts.push(k + '=' + encodeURIComponent(extra[k]));
  });
  const r = rangeQS(from, to);
  if (r) parts.push(r);
  return parts.length ? '?' + parts.join('&') : '';
}
/* 组件级清单地址（流水线级页面的上一层；首页返回按钮也用它判断层级） */
function compHref(from, to){
  return levelQS({comp: COMP_OF_KEY || COMP, dim: DIM}, from, to);
}
function indexHref(from, to){
  return INDEX_PAGE + levelQS({}, from, to);
}
function setBack(from, to){
  const el = document.getElementById('back');
  if (!el) return;
  if (KEY && (COMP_OF_KEY || COMP)){
    el.href = compHref(from, to);
    el.textContent = '← 返回 ' + (COMP_OF_KEY || COMP) + ' 流水线清单';
    el.title = '返回组件级流水线清单（' + (DIMNAME[DIM] || '全部维度') + '，统计区间不变）';
  } else if (COMP){
    el.href = indexHref(from, to);
    el.textContent = '← 返回总览';
    el.title = '返回首页组件级概览（统计区间不变）';
  }
}
/* 面包屑：总览 › 组件（维度）› 流水线，每层都可点回去 */
function crumbHead(from, to){
  return '<a href="' + indexHref(from, to) + '">总览</a><span class="sep">›</span>';
}
function statusTag(s){
  const v = String(s || '').toUpperCase();
  if (v === 'FAILED' || v === 'FAILURE') return '<span class="tag red">' + rfEsc(s) + '</span>';
  if (v === 'SUCCESS' || v === 'SUCCEEDED') return '<span class="tag green">' + rfEsc(s) + '</span>';
  return '<span class="tag">' + rfEsc(s || '--') + '</span>';
}
function failBox(msg){
  const box = document.getElementById('recs');
  if (box) box.innerHTML = '<div class="err">' + msg + '</div>';
}

function boot(){
  if (!KEY && !COMP){
    failBox('缺少参数。请从首页表格点击「待分析流水线数」进入（组件级），或点「查看记录 →」进入（流水线级）。');
    return;
  }
  /* 先把返回按钮按层级摆好（数据回来之前也不要显示错层级） */
  setBack(QS.get('from') || '', QS.get('to') || '');
  probeRoot().then(function(idx){
    DAYS = (idx.days || []).slice().sort(function(a, b){ return a.date < b.date ? -1 : 1; });
    if (!DAYS.length){
      failBox(emptyArchiveMsg());
      return;
    }
    rfInit({min: DAYS[0].date, max: DAYS[DAYS.length - 1].date, onChange: load});
  }).catch(function(e){
    /* 归档区为空时 _index.json 本来就不存在（HTTP 404 / ENOENT）→ 提示去采集；
       file:// 直开被浏览器拦截（TypeError）才提示起本地服务 */
    if (isNotFound(e)){
      failBox(emptyArchiveMsg());
      return;
    }
    if (hostedSite()){
      failBox('<b>读取归档索引失败</b><div class="mono" style="margin-top:6px">' + rfEsc(String(e)) + '</div>'
        + '<div style="margin-top:8px">静态托管环境读不到 <span class="mono">archive/_index.json</span>，'
        + '请确认文件已提交、仓库根目录有 <span class="mono">.nojekyll</span>、Pages 已重新部署。</div>');
      return;
    }
    failBox('<b>读取归档索引失败</b><div class="mono" style="margin-top:6px">' + rfEsc(String(e)) + '</div>'
      + '<div style="margin-top:8px">file:// 直开会被浏览器拦截 fetch，请用 '
      + '<span class="mono">python run_pipeline.py --serve</span> 起本地服务后访问。</div>');
  });
}

function load(from, to){
  setBack(from, to);                     /* 返回按钮跟着当前层级 + 当前区间走 */
  if (!KEY) return loadComp(from, to);   /* 没给 key 就是组件级：先列该组件的流水线 */
  const box = document.getElementById('recs');
  box.innerHTML = '<div class="loading">正在读取归档记录…</div>';
  document.getElementById('ttl').textContent = KEY.replace('|', ' / ');
  const days = DAYS.filter(function(d){ return d.date >= from && d.date <= to; });
  if (!days.length){
    /* 这天没有归档（非工作日不跑数）：照样出区块头，比率 / 达标工作日一律 --，
       不把「昨天」悄悄换成别的日子，也不留一张空表让人以为数据丢了 */
    RUNS = [];
    CTX = {dirs: [], days: days, from: from, to: to, runs: []};
    renderHead(null, [], CTX);
    renderRecs([], CTX);
    return;
  }
  /* 1) 逐日找该流水线的归档目录（同一条流水线在区间内可能每天一个目录） */
  const dirs = [];
  let meta = null;
  let chain = Promise.resolve();
  days.forEach(function(d){
    chain = chain.then(function(){
      return jget(BACK_TO + d.path + '/_day.json').then(function(day){
        (day.pipelines || []).forEach(function(pl){
          if (pl.key === KEY){
            dirs.push({date: d.date, path: pl.path, pl: pl});
            if (!meta) meta = {pl: pl, day: day};
          }
        });
      }).catch(function(){ /* 该日读取失败则跳过，不影响其它日 */ });
    });
  });
  chain.then(function(){
    if (!dirs.length){
      const withAny = (DAYS.length && days.length) ? '' : '';
      box.innerHTML = '<div class="empty">该区间内没有这条流水线的归档记录（' + rfEsc(from) + ' ~ ' + rfEsc(to) + '）'
        + withAny + '<div class="dim" style="margin-top:6px">流水线只在被点灯的日期才会进入待分析集合。</div></div>';
      RUNS = [];
      CTX = {dirs: [], days: days, from: from, to: to, runs: []};
      renderHead(null, [], CTX);
      return;
    }
    /* 2) 逐目录读 step_2_runs.json（区间内全部运行 → 按天构建记录 / 区间重算）
          + _manifest.json → records[] → 单条记录（不读 80KB+ 的 step_6_records.json） */
    const recs = [];
    const runs = [];
    const runSeen = {};
    let chain2 = Promise.resolve();
    dirs.forEach(function(d){
      /* 2a) 运行历史：同一条流水线的每个归档日都带着同一份「整区间」runHistory，按构建号去重 */
      chain2 = chain2.then(function(){
        return jget(BACK_TO + d.path + '/step_2_runs.json').then(function(s2){
          ((s2 || {}).runHistory || []).forEach(function(h){
            const rn = String(h.rn);
            if (!rn || rn === 'undefined' || runSeen[rn]) return;
            runSeen[rn] = 1;
            const end = String(h.end || '');
            runs.push({rn: h.rn, start: h.start, end: end, day: end.slice(0, 10),
                       status: h.status, url: h.url, iva: h.iva,
                       bc: h.bc, bpc: h.bpc, cpr: h.cpr, ftt: h.ftt});
          });
        }).catch(function(){ /* 老归档没有 step_2_runs.json：退回「逐日灯态」口径，不报错 */ });
      });
      /* 2b) 归档清单 → records[] → 单条记录 */
      chain2 = chain2.then(function(){
        return jget(BACK_TO + d.path + '/_manifest.json').then(function(mf){
          const missing = new Set(((mf.evidence || {}).missing) || []);
          const list = (mf.records || []).slice();
          let chain3 = Promise.resolve();
          list.forEach(function(r){
            chain3 = chain3.then(function(){
              return jget(BACK_TO + d.path + '/' + r.file).then(function(rec){
                rec.__base = BACK_TO + d.path + '/';
                rec.__dir = d.path;
                rec.__missing = missing;
                recs.push(rec);
              }).catch(function(e){
                recs.push({__err: String(e), __base: BACK_TO + d.path + '/', __dir: d.path, __missing: missing,
                           recordId: r.recordId, buildNumber: r.buildNumber, endedAt: r.endedAt,
                           category: r.category, tasks: [], cases: [], errorMessages: [], evidence: {files: [], missing: []}});
              });
            });
          });
          return chain3;
        }).catch(function(){});
      });
    });
    chain2.then(function(){
      recs.sort(function(a, b){ return String(a.endedAt || '') < String(b.endedAt || '') ? 1 : -1; });
      runs.sort(function(a, b){ return String(a.end || '') < String(b.end || '') ? 1 : -1; });
      RUNS = runs;
      CTX = {dirs: dirs, days: days, from: from, to: to, runs: runs};
      renderHead(meta, recs, CTX);
      renderRecs(recs, CTX);
    });
  });
}

/* ---------- 流水线级页面的维度口径 ----------
   这一页是**从哪个维度点进来的，就只讲哪个维度**：dim=va 只讲版本可用度，dim=bs 只讲编译成功率。
   比率、工作日达标数、构建记录都按同一个维度取；另一个维度在这一页上完全不出现。 */
function dimOf(){
  if (DIM === 'bs') return 'bs';
  if (DIM === 'va') return 'va';
  /* 没带 dim（直接开链接）时：该流水线只有 bs 维度的就按 bs，否则按 va */
  const pl = (RECS[0] || {}).light || {};
  const dims = pl.dims || [];
  return (dims.indexOf('bs') >= 0 && dims.indexOf('va') < 0) ? 'bs' : 'va';
}

/* 该流水线在区间内的逐日灯态：用于区块头的「可用 N/M 工作日」（按当前维度取灯）
   灯态由当天比率就地判档，归档里不再存灯色 */
function dayLights(ctx){
  const dim = DIM || '';
  const out = [];
  (ctx.dirs || []).forEach(function(d){
    const pl = d.pl || {};
    const ds = pl.dims || [];
    const useBs = dim ? dim === 'bs' : (ds.indexOf('bs') >= 0 && ds.indexOf('va') < 0);
    const light = useBs
      ? (lightByRate(pl.bsRate, 'bs') || pl.bsLight)
      : (lightByRate(pl.vaRate, 'va') || pl.vaLight);
    out.push({date: d.date, light: light || null});
  });
  return out;
}
/* 区间重算：只统计「执行结束时间落在当前统计区间内」的运行，逐日判定是否达标。
   比率 = 达标日 / 有运行的工作日（与第 2 步 recompute 口径一致：可用日 / 有记录日）；
   区间一变，分子分母同时按新区间重算，不再沿用归档当日写死的比率。
   dim=va：当天任一次运行 iva=true 才算可用；dim=bs：当天全部运行的编译成功次数 = 编译次数。 */
function rangeStats(ctx, dim){
  ctx = ctx || {};
  const from = ctx.from || '', to = ctx.to || '';
  const inRange = (ctx.runs || []).filter(function(r){
    return r.day && r.day >= from && r.day <= to;
  });
  if (inRange.length){
    const byDay = {}, dayRuns = {};
    inRange.forEach(function(r){
      const d = byDay[r.day] || (byDay[r.day] = {runs: 0, ok: false, bc: 0, bpc: 0});
      d.runs += 1;
      if (r.iva === true) d.ok = true;
      if (r.bc !== null && r.bc !== undefined && r.bc !== ''){
        d.bc += Number(r.bc) || 0;
        d.bpc += Number(r.bpc) || 0;
      }
      (dayRuns[r.day] || (dayRuns[r.day] = [])).push(r);
    });
    const dayList = Object.keys(byDay).sort();
    let okDays = 0, bc = 0, bpc = 0;
    const dayOk = {};
    dayList.forEach(function(k){
      const d = byDay[k];
      const ok = (dim === 'bs') ? (d.bc > 0 && d.bpc >= d.bc) : !!d.ok;
      dayOk[k] = ok;
      if (ok) okDays += 1;
      bc += d.bc; bpc += d.bpc;
    });
    Object.keys(dayRuns).forEach(function(k){
      dayRuns[k].sort(function(a, b){ return String(a.end || '') < String(b.end || '') ? 1 : -1; });
    });
    return {source: 'runs', runs: inRange, byDay: byDay, dayRuns: dayRuns, dayList: dayList,
            dayOk: dayOk, total: dayList.length, availDays: okDays,
            bc: bc, bpc: bpc,
            rate: (dim === 'bs') ? (bc > 0 ? bpc / bc : null)
                                 : (dayList.length ? okDays / dayList.length : null)};
  }
  /* 兜底：老归档没有 step_2_runs.json 的运行历史时，退回「归档日逐日灯态」口径（灯态由比率就地算）。
     只统计有灯态的日子：既没有比率也没有灯色字段的日子不计入分母，避免被当成达标日。 */
  const dls = dayLights(ctx).filter(function(d){ return !!d.light; });
  const bad = {};
  dls.forEach(function(d){ if (d.light === '黄' || d.light === '红') bad[d.date] = 1; });
  const total = dls.length;
  const okDays = Math.max(0, total - Object.keys(bad).length);
  return {source: 'lights', runs: [], byDay: {}, dayRuns: {}, dayList: [], dayOk: {},
          total: total, availDays: okDays, bc: 0, bpc: 0,
          rate: total ? okDays / total : null};
}
/* 根因：只取本条记录的 AI 分析结论（不做任何历史结论复用），没有就留空走兜底文案 */
function rootCauseOf(r){
  const an = r.analysis || {};
  return an.rootCause || '';
}
function cut(s, n){
  const t = String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  if (!t) return '--';
  return t.length > n ? t.slice(0, n) + '…' : t;
}
/* 2026-10-28T21:10:17 → 2026-10-28 21:10:17 */
function tm(s){
  const v = String(s == null ? '' : s).replace('T', ' ');
  return v ? v.slice(0, 19) : '--';
}
/* 错误信息里常见 {"errorMessage":..,"errorCode":..} 的 JSON 原文，表格单元格里压成一句话 */
function errText(t){
  const s = String(t == null ? '' : t).trim();
  if (s.charAt(0) === '{'){
    try {
      const o = JSON.parse(s);
      if (o && (o.errorMessage || o.errorCode)){
        const msg = String(o.errorMessage || '').trim();
        return msg + (o.errorCode ? '（' + o.errorCode + '）' : '');
      }
    } catch (e) { /* 不是合法 JSON 就按原文展示 */ }
  }
  return s;
}

/* job 日志的每一行本来就带时间戳（[2026/11/06 01:11:47.479 GMT+08:00] ...），
   而 errorMessages 这一层只有行号没有时间字段 → 从原文行首把时间抽出来，供「证据」弹窗的时间列用 */
function timeFromLine(s){
  const m = String(s == null ? '' : s)
    .match(/(\d{4})[-\/](\d{1,2})[-\/](\d{1,2})[ T](\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)/);
  if (!m) return '';
  const p2 = function(x){ return ('0' + x).slice(-2); };
  return m[1] + '-' + p2(m[2]) + '-' + p2(m[3]) + ' ' + m[4];
}
/* 一条记录的「证据」行 = 用例级 errorMessages（带 time/loc，测试报告路线）
   + 顶层 errorMessages（job 日志扫描出的错误行，时间在行首），逐行去重、逐行完整保留。
   用例级证据排前面并按时间升序（同一次执行的日志时间线），
   没有时间的「测试报告断言」保持原序排在后面，不丢任何一行。 */
function evidenceRows(r){
  if (!r) return [];
  if (r.__ev) return r.__ev;
  const out = [], seen = new Set();
  const push = function(o){
    const k = [o.time || '', o.level || '', o.loc || '',
               (o.line == null ? '' : o.line), o.text || ''].join('|');
    if (seen.has(k)) return;
    seen.add(k);
    out.push(o);
  };
  (r.cases || []).forEach(function(c){
    (c.errorMessages || []).forEach(function(m){
      push({time: m.time || timeFromLine(m.msg), level: nz(m.level), loc: m.loc || '',
            line: m.line, text: m.msg || '', task: c.task || '', case: c.name || ''});
    });
  });
  (r.errorMessages || []).forEach(function(e){
    if (e.isSummary) return;                 /* pytest 汇总行不是一条证据 */
    push({time: e.time || timeFromLine(e.text), level: nz(e.level), loc: '',
          line: e.line, text: e.text || '', task: e.task || '', case: e.case || ''});
  });
  const withT = out.filter(function(x){ return x.time; });
  const noT = out.filter(function(x){ return !x.time; });
  withT.sort(function(a, b){ return String(a.time).localeCompare(String(b.time)); });
  r.__ev = withT.concat(noT);
  return r.__ev;
}

/* ---------- 组件级：首页「待分析流水线数」点进来的流水线清单（不展开、单独一页） ----------
   这一页是**按维度下钻**来的：dim=va 就只列版本可用度，dim=bs 就只列编译成功率，
   另一个维度根本不渲染（不显示、不出灯、不出归档日），避免在一张表里塞两套口径。
   比率只给纯数字（80.0%），不带黄/绿/红圆点或灯色文字：
   达标与否已在首页「待分析流水线数」表达，这里是查数用的清单。 */
function loadComp(from, to){
  const box = document.getElementById('recs');
  const ttl = document.getElementById('ttl');
  if (ttl) ttl.textContent = COMP;
  const dimTxt = DIMNAME[DIM] || '';
  const days = DAYS.filter(function(d){ return d.date >= from && d.date <= to; });
  if (!days.length){
    /* 没有归档日就列不出流水线清单（清单是当天归档带出来的），
       但仍要把口径写清楚：这两列的比值这天就是 -- */
    box.innerHTML = '<div class="empty">'
      + rfEsc(from === to ? from : (from + ' ~ ' + to)) + ' 没有归档日（非工作日不跑数）'
      + '<div class="dim" style="margin-top:6px">'
      + rfEsc(dimTxt || '版本可用度') + ' 显示 <b>--</b>，换成有跑数的工作日即可看明细</div></div>';
    const meta0 = document.getElementById('meta');
    if (meta0) meta0.textContent = '区间 ' + from + ' ~ ' + to + ' · 无归档日 · 指标显示 --';
    return;
  }
  box.innerHTML = '<div class="loading">正在读取 ' + days.length + ' 个归档日…</div>';
  const map = new Map();
  let chain = Promise.resolve();
  days.forEach(function(d){
    chain = chain.then(function(){
      return jget(BACK_TO + d.path + '/_day.json').then(function(day){
        (day.pipelines || []).forEach(function(pl){
          if (pl.component !== COMP) return;
          const dims = pl.dims || [];
          if (DIM && dims.indexOf(DIM) < 0) return;
          let r = map.get(pl.key);
          if (!r){
            r = {key: pl.key, pipeline: pl.pipeline, name: pl.pipelineName || pl.pipeline,
                 platform: pl.platform || '', dims: {},
                 vaVals: [], bsVals: [], vaA: 0, vaN: 0, bsP: 0, bsC: 0,
                 runs: [], totalRuns: 0};
            map.set(pl.key, r);
          }
          dims.forEach(function(x){ r.dims[x] = 1; });
          /* 区间重算：逐日累加分子/分母（可用日/有运行日、编译通过/编译次数），
             统计区间一变就按新的 Σ分子/Σ分母 重算；老归档无 *Detail 才退回逐日比率平均 */
          const vd = pl.vaDetail, bd = pl.bsDetail;
          if (vd && Number(vd.daysInPeriod) > 0){
            r.vaA += Number(vd.availDays) || 0; r.vaN += Number(vd.daysInPeriod) || 0;
          } else { r.vaVals.push(pl.vaRate); }
          if (bd && Number(bd.buildCount) > 0){
            r.bsP += Number(bd.buildPassCount) || 0; r.bsC += Number(bd.buildCount) || 0;
          } else { r.bsVals.push(pl.bsRate); }
          /* 只记当日运行数：比率按统计区间重算，逐日再贴一遍同一个比率只会误导 */
          r.runs.push({date: d.date, n: pl.runsToday || 0, fail: pl.failedRunsToday || 0});
          r.totalRuns = Math.max(r.totalRuns || 0, pl.runsInPeriod || 0);
        });
      }).catch(function(){ /* 单日读取失败跳过，不影响其它日 */ });
    });
  });
  chain.then(function(){
    const list = Array.from(map.values()).sort(function(a, b){ return a.pipeline < b.pipeline ? -1 : 1; });
    /* 流水线级比率同样按统计区间重算：Σ可用日/Σ有运行日、Σ编译通过/Σ编译次数 */
    list.forEach(function(p){
      p.vaRate = p.vaN > 0 ? p.vaA / p.vaN : avgNum(p.vaVals);
      p.bsRate = p.bsC > 0 ? p.bsP / p.bsC : avgNum(p.bsVals);
      p.vaAcc = p.vaN > 0 ? ('Σ可用日 / Σ有运行日 = ' + p.vaA + ' / ' + p.vaN) : '归档无分子/分母，退回归档比率';
      p.bsAcc = p.bsC > 0 ? ('Σ编译通过 / Σ编译次数 = ' + p.bsP + ' / ' + p.bsC) : '归档无分子/分母，退回归档比率';
      const head = '区间内共 ' + (p.totalRuns || 0) + ' 次运行（成功与失败都采集）'
        + '，比率按本统计区间重算（区间一变即重算，不是逐日比率取平均）';
      const dd = (p.runs || []).map(function(x){
        return x.date + '  当日 ' + x.n + ' 次运行'
          + (x.fail ? '（失败/取消 ' + x.fail + '）' : '');
      });
      p.vaTitle = '版本可用度（流水线级）\n' + head + '\n' + p.vaAcc + '\n' + dd.join('\n');
      p.bsTitle = '编译成功率（流水线级）\n' + head + '\n' + p.bsAcc + '\n' + dd.join('\n');
    });
    const cols = DIM ? [DIM] : ['va', 'bs'];
    const meta = document.getElementById('meta');
    if (meta) meta.textContent = '区间 ' + from + ' ~ ' + to
      + (dimTxt ? ' · 维度仅 ' + dimTxt : '')
      + ' · 待分析流水线 ' + list.length + ' 条 · 数据源 out/archive/';
    const crumb = document.getElementById('crumb');
    if (crumb) crumb.innerHTML = crumbHead(from, to) + '<b>' + rfEsc(COMP) + '</b>（' + rfEsc(dimTxt || '全部维度')
      + ' · ' + list.length + ' 条待分析流水线'
      + (list.length ? '：' + rfEsc(list.map(function(p){ return p.name; }).join('、')) : '')
      + '）';
    const dist = document.getElementById('dist');
    if (dist) dist.innerHTML = '';
    if (!list.length){
      box.innerHTML = '<div class="empty">该区间内没有进入待分析集合的流水线</div>';
      return;
    }
    const head = '<th style="width:38%">流水线</th><th style="width:16%">平台</th>'
      + cols.map(function(d){ return '<th style="width:18%">' + DIMNAME[d] + '</th>'; }).join('')
      + '<th style="width:10%"></th>';
    box.innerHTML = '<div class="tbl-wrap" style="border:0;border-radius:0;min-height:0">'
      + '<table class="ov" id="tbl"><thead><tr>' + head + '</tr></thead><tbody>'
      + list.map(function(p){ return compRow(p, from, to, cols); }).join('')
      + '</tbody></table></div>';
  });
}

function compRow(p, from, to, cols){
  /* 下钻链接带上 comp + dim：流水线页才知道上一层是哪个组件的哪个维度，能逐层退回来 */
  const q = levelQS({key: p.key, comp: COMP, dim: DIM}, from, to);
  let tds = '<td class="c-name">' + rfEsc(p.pipeline)
    + '<div class="dim" style="font-size:11px;font-weight:400">' + rfEsc(p.name) + '</div></td>'
    + '<td>' + rfEsc(p.platform || '--') + '</td>';
  cols.forEach(function(d){
    tds += '<td class="c-rate">'
      + rateNum(d === 'va' ? p.vaRate : p.bsRate, d === 'va' ? p.vaTitle : p.bsTitle,
                p.key, d, from, to)
      + '</td>';
  });
  /* 本页展示的维度比率全部 100% = 这区间内没有需要下钻的失败记录：
     「查看记录」直接显示 --（不给链接、不响应点击），避免点进去看到一张空表 */
  let full = cols.length > 0;
  cols.forEach(function(d){
    const v = d === 'va' ? p.vaRate : p.bsRate;
    if (v === null || v === undefined || v === '' || isNaN(v) || Number(v) < 1) full = false;
  });
  tds += '<td class="nowrap">'
    + (full ? '<span class="dim" title="该维度比率 100%，区间内没有失败记录可下钻">--</span>'
            : '<a class="lnk" href="' + q + '">查看记录 →</a>')
    + '</td>';
  return '<tr>' + tds + '</tr>';
}

/* 纯数字百分比（80.0%）：不输出圆点、不输出黄/绿/红文字，title 里给逐日明细；
   数字本身可点 → 弹「构建记录」（数据源 = 该流水线归档里的 step_2_runs.json，
   点的时候才按需去读，组件级清单首屏不额外拉数据） */
function rateNum(v, title, key, dim, from, to){
  const txt = pctText(v);
  const has = !(v === null || v === undefined || v === '' || isNaN(v));
  if (!has) return '<span class="pct">' + txt + '</span>';
  const t = (title ? title + '\n' : '') + '点击查看构建记录';
  return '<span class="pct lnk" title="' + rfEsc(t) + '"'
    + ' data-builds-of="' + rfEsc(key) + '" data-dim="' + rfEsc(dim) + '"'
    + ' data-from="' + rfEsc(from) + '" data-to="' + rfEsc(to) + '">' + txt + '</span>';
}
function pctText(v){
  return (v === null || v === undefined || v === '' || isNaN(v)) ? '--'
    : (Number(v) * 100).toFixed(1) + '%';
}
function avgNum(a){
  const xs = (a || []).filter(function(v){ return v !== null && v !== undefined && v !== '' && !isNaN(v); });
  if (!xs.length) return null;
  let s = 0; xs.forEach(function(v){ s += Number(v); });
  return s / xs.length;
}

function renderHead(meta, recs, ctx){
  const el = document.getElementById('meta');
  let tasks = 0, cases = 0, errs = 0, evs = 0;
  recs.forEach(function(r){
    const c = r.counts || {};
    tasks += Number(c.tasks || 0); cases += Number(c.cases || 0);
    errs += Number(c.errorMessages || 0); evs += Number(c.evidenceFiles || 0);
  });
  if (meta && meta.pl){
    const pl = meta.pl;
    document.getElementById('ttl').innerHTML = '<b>' + rfEsc(pl.component) + '</b> / ' + rfEsc(pl.pipeline)
      + ' <span class="dim" style="font-weight:400;font-size:12px">' + rfEsc(pl.pipelineName || '') + '</span>';
    const crumb = document.getElementById('crumb');
    if (crumb) crumb.innerHTML = crumbHead(ctx.from, ctx.to)
      + '<a href="' + levelQS({comp: pl.component, dim: DIM}, ctx.from, ctx.to) + '">'
      + rfEsc(pl.component) + '</a>'
      + (DIM ? '<span class="dim">（' + rfEsc(DIMNAME[DIM]) + '）</span>' : '')
      + '<span class="sep">›</span><b>' + rfEsc(pl.pipeline) + '</b>'
      + '<span class="dim">（流水线级 · 区间内 ' + ((ctx.dirs || []).length) + ' 天有归档记录 · 每条执行记录一行根因）</span>';
  }
  if (el) el.textContent = '区间 ' + ctx.from + ' ~ ' + ctx.to
    + (meta && meta.pl ? ' · 平台 ' + (meta.pl.platform || '--') : '')
    + ' · 执行记录 ' + recs.length + ' · 任务 ' + tasks + ' · 用例 ' + cases
    + ' · 错误信息 ' + errs + ' · 证据 ' + evs;
  renderDist(recs);
}

/* 定性分布：二级页字段，按执行记录计数（含占比） */
function renderDist(recs){
  const el = document.getElementById('dist');
  if (!el) return;
  const byCat = {};
  recs.forEach(function(r){
    const k = (r.verdict || {}).category || r.category || 'unknown';
    byCat[k] = (byCat[k] || 0) + 1;
  });
  const ks = Object.keys(byCat).sort(function(a, b){ return byCat[b] - byCat[a]; });
  const total = recs.length || 1;
  const tags = ks.map(function(k){
    const c = CATS[k] || [k, ''];
    const p = (byCat[k] / total * 100).toFixed(byCat[k] === total ? 0 : 1);
    return '<span class="tag ' + c[1] + '">' + c[0] + ' ' + byCat[k]
      + ' <span class="dim">' + p + '%</span></span>';
  }).join('');
  el.innerHTML = '<span class="dt">定性分布</span>'
    + (ks.length ? tags : '<span class="dim">--</span>')
    + '<span class="sp"></span>'
    + '<span class="dim">按执行记录计数，共 ' + recs.length + ' 条</span>';
}

let RECS = [];
let RUNS = [];      /* 区间内该流水线的全部运行（每日 step_2_runs.json 的 runHistory，按构建号去重） */
let CTX = null;     /* 当前统计区间上下文：dirs / days / from / to / runs */

/* ===== 流水线级：一条流水线一个区块（h3 浅蓝区块头）+ 一张 10 列固定列宽的根因明细表 ===== */
function renderRecs(recs, ctx){
  const box = document.getElementById('recs');
  RECS = recs;
  const dim = dimOf();
  const st = rangeStats(ctx, dim);
  const total = st.total;
  const okDays = st.availDays;
  const rate = st.rate;
  const pl = recs[0] || ((ctx.dirs || [])[0] || {}).pl || {};
  /* 流水线级只看数字（85.71%），不带黄/绿/红标记：
     灯色只在首页组件级出现，往下钻一律看数字 + 达标工作日数。 */
  const okWord = dim === 'bs' ? '达标' : '可用';
  const h3 = '<h3 title="' + DIMNAME[dim] + '：' + (st.source === 'runs'
      ? '区间内有运行的工作日 ' + total + ' 天，其中 ' + okDays + ' 天' + okWord
        + '（逐日按执行结果重算，统计区间一变即重算）'
      : '区间内归档 ' + total + ' 天，其中 ' + okDays + ' 天该流水线在此维度上没有黄/红')
    + '">' + rfEsc(pl.pipelineName || pl.pipeline || KEY)
    + ' ｜ ' + DIMNAME[dim] + ' ' + (rate == null ? '--' : (rate * 100).toFixed(2) + '%')
    + ' ｜ ' + okWord + ' ' + okDays + '/' + total + ' 工作日 '
    + '<span class="hist lnk" data-builds="1">查看构建记录</span></h3>';
  if (!recs.length){
    /* 区间内没有失败记录也要出区块头：比率/工作日是区间重算的结果，不是记录数的函数 */
    box.innerHTML = '<div class="subview">' + h3
      + '<div class="empty">该区间内没有需要根因分析的失败执行记录'
      + (st.runs.length ? '（区间内共 ' + st.runs.length + ' 次运行，见「查看构建记录」）' : '')
      + '</div></div>';
    return;
  }
  /* 7 列：构建编号 → 任务 → 用例 → 错误信息 → 定性 → 根因 → 建议
     （「根因编号」列已删除，构建号本身就跳 openLiBing，编号没有额外信息量；
       原 8/6/9/9/20/7/24/17 里腾出的 6% 并给用例、错误信息、根因，合计仍是 100%） */
  const cols = ['8%', '11%', '11%', '22%', '7%', '24%', '17%'];
  const ths = ['构建编号', '任务', '用例', '错误信息', '定性', '根因', '建议'];
  const table = '<table class="rca"><colgroup>'
    + cols.map(function(w){ return '<col style="width:' + w + '">'; }).join('')
    + '</colgroup><thead><tr>' + ths.map(function(t){ return '<th>' + t + '</th>'; }).join('') + '</tr></thead>'
    + '<tbody>' + recs.map(function(r, i){ return recRow(r, i); }).join('') + '</tbody></table>';
  box.innerHTML = '<div class="subview">' + h3 + table + '</div>';
}

/* 一行 = 一条执行记录（内容口径与改版前完全一致，只是换成表格一行） */
function recRow(r, i){
  const v = r.verdict || {};
  const cov = r.counts || {};
  const an = r.analysis || {};
  const links = r.links || {};

  const tasks = r.tasks || [];
  const taskCell = tasks.length
    ? '<b>' + rfEsc(nz(tasks[0].jobName)) + '</b>'
      + (tasks.length > 1 ? '<span class="exec-sub">共 ' + tasks.length + ' 个任务</span>' : '')
    : '<span class="dim">--</span>';

  /* 成功运行也落记录（全量采集），但它没有失败用例，不能套用「setup 阶段批量失败」的兜底文案 */
  const isOk = (v.category === 'success');
  const cases = r.cases || [];
  let caseCell;
  if (cases.length){
    /* 用例列：第一条用例名打头，「共 N 条失败用例」是可点链接 → 弹窗里逐条给用例详情
       （用例名 / 状态 / 起止时间 / 报告里的断言原文 / 该用例日志） */
    caseCell = rfEsc(cut(cases[0].name, 70))
      + '<span class="lnk exec-sub" data-cases="' + i + '"'
      + ' title="点击查看 ' + cases.length + ' 条失败用例的详情">共 '
      + cases.length + ' 条失败用例</span>';
  } else if (isOk){
    caseCell = '<span class="dim">--（执行成功，无失败用例）</span>';
  } else {
    caseCell = '<span class="dim">—（setup 阶段批量失败，未产出有效用例记录）</span>';
  }

  const seen = new Set();
  const uniq = [];
  (r.errorMessages || []).forEach(function(e){
    const k = (e.level || '') + '|' + (e.text || '');
    if (seen.has(k)) return;
    seen.add(k);
    uniq.push(e);
  });
  /* 证据行：用例级 errorMessages（带时间/位置）+ job 日志扫描出的错误行，逐行完整保留 */
  const evs = evidenceRows(r);
  let errCell = '<span class="dim">--</span>';
  if (uniq.length){
    /* 优先展示真因：跳过平台封装文案（STEP/JOB，如「构建任务执行失败!(DEV-CODECI-35002)」），
       直接给行级/用例级错误；全都只有封装码时才回退首条。
       出处标签（Test_ops-math · 测试报告 · CASE）不再堆在单元格里，改成「证据」链接看原始行 */
    const cand = uniq.filter(function(e){
      return !e.isWrapper && e.level !== 'STEP' && e.level !== 'JOB';
    });
    const e0 = cand[0] || uniq[0];
    errCell = '<div class="err-text">' + rfEsc(cut(errText(e0.text), 220)) + '</div>'
      + (evs.length
        ? '<div><span class="lnk ev-lnk" data-evidence="' + i + '" title="点击查看日志证据：'
          + evs.length + ' 行（时间 / 级别 / 位置 / 原文）">证据</span></div>'
        : '<div><span class="dim ev-lnk">证据 --</span></div>');
  }

  const buildCell = links.openlibing
    ? '<a href="' + rfEsc(links.openlibing) + '" target="_blank" rel="noopener">#' + rfEsc(nz(r.buildNumber)) + '</a>'
    : '#' + rfEsc(nz(r.buildNumber));

  /* 根因：插在「建议」前一列，方便一眼看出这条记录为什么失败；
     不做 AI 分析的记录（成功运行 / 指标达标的失败运行，analysis.status=na）显示 -- */
  const noAI = isOk || an.status === 'na';
  const rc = rootCauseOf(r);
  const rcCell = rc ? rfEsc(cut(rc, 120))
    : (noAI ? '<span class="dim">--</span>' : '<span class="dim">待 AI 根因分析</span>');

  const adv = an.advice || ((r.suggestions || [])[0] || '');
  const advCell = adv ? rfEsc(cut(adv, 160))
    : (noAI ? '<span class="dim">--</span>' : '<span class="dim">待 AI 根因分析</span>');

  /* 根因编号列已删除：构建号本身就在第一列且直接跳 openLiBing，编号（AC-BS-01）没有额外信息量 */
  return '<tr>'
    + '<td class="nowrap">' + buildCell
      + (r.__err ? '<span class="exec-sub" style="color:#f5222d">记录读取失败</span>' : '') + '</td>'
    + '<td>' + taskCell + '</td>'
    + '<td>' + caseCell + '</td>'
    + '<td class="err-cell">' + errCell + '</td>'
    + '<td class="nowrap">' + rfEsc(((CATS[v.category] || [v.text || v.category || '未定性']))[0]) + '</td>'
    + '<td>' + rcCell + '</td>'
    + '<td>' + advCell + '</td>'
    + '</tr>';
}

/* ===== 弹窗骨架：三个入口共用（失败用例 / 日志证据 / 构建记录） ===== */
function openModal(title, html){
  const m = document.getElementById('mrec');
  const t = document.getElementById('mttl');
  const b = document.getElementById('mbody');
  if (!m || !b) return;
  if (t) t.innerHTML = title;
  b.innerHTML = html;
  m.classList.add('on');
  m.scrollTop = 0;
}
function closeModal(){
  const m = document.getElementById('mrec');
  if (m) m.classList.remove('on');
}

/* ===== 用例列「共 N 条失败用例」→ 弹窗逐条给出用例详情 =====
   数据源 = 该执行记录 records/run_<构建号>.json 的 cases[]（测试报告 failedCases）：
   name / state / beginTime / endTime / description（报告里的断言原文）/ localLog（归档的用例 HTML）。
   这里只做「有哪些用例失败、失败在哪一步」，日志原文行在「证据」弹窗里看。 */
function openCases(i){
  const r = RECS[i] || {};
  const cases = r.cases || [];
  if (!cases.length) return;
  const base = r.__base || '';
  const rows = cases.map(function(c, k){
    /* 用例日志：优先归档里的本地 HTML（localLog），没有再给 openLiBing 的远程报告地址 */
    const lg = c.localLog ? (base + c.localLog) : (c.logUrl || '');
    const lgCell = lg
      ? '<a href="' + rfEsc(lg) + '" target="_blank" rel="noopener">用例日志</a>'
      : '<span class="dim">--</span>';
    return '<tr>'
      + '<td class="expr-txt"><span class="dim">' + (k + 1) + '</span> ' + rfEsc(nz(c.name)) + '</td>'
      + '<td class="nowrap">' + statusTag(c.state) + '</td>'
      + '<td class="cw1 dim">' + rfEsc(nz(c.beginTime)) + '</td>'
      + '<td class="cw1 dim">' + rfEsc(nz(c.endTime)) + '</td>'
      + '<td class="expr-txt">' + rfEsc(nz(c.description)) + '</td>'
      + '<td class="nowrap">' + lgCell + '</td>'
      + '</tr>';
  }).join('');
  openModal('失败用例（' + cases.length + ' 条）｜#' + rfEsc(nz(r.buildNumber))
      + ' · ' + rfEsc(nz(r.pipeline)),
    '<div class="mtbl"><table class="rca"><colgroup><col style="width:21%"><col style="width:7%">'
    + '<col style="width:13%"><col style="width:13%"><col style="width:39%"><col style="width:7%"></colgroup>'
    + '<thead><tr><th>用例</th><th>状态</th><th>开始时间</th><th>结束时间</th>'
    + '<th>失败详情（测试报告断言原文）</th><th>日志</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div>'
    + '<div class="mrec-note">用例清单取自该次执行的测试报告 failedCases（共 ' + cases.length
    + ' 条全部失败用例），失败详情按原文完整输出、不做截断；'
    + '「用例日志」打开归档里的该用例 HTML 报告。日志原始错误行见「错误信息」列的「证据」。</div>');
}

/* ===== 错误信息列「证据」→ 弹窗逐行输出日志证据（时间 / 级别 / 位置 / 整行原文） =====
   行级信息（time / level / loc / line / 原文）直接从归档记录里取，不再堆在单元格里，
   单元格只留「证据」两个字。 */
function openEvidence(i){
  const r = RECS[i] || {};
  const evs = evidenceRows(r);
  if (!evs.length) return;
  const rows = evs.map(function(x){
    /* 位置：log 行给 line（L3340），用例内错误给 loc:line（plugin.py:999:3340） */
    const loc = x.loc
      ? rfEsc(x.loc) + (x.line == null ? '' : ':' + rfEsc(x.line))
      : (x.line == null ? '<span class="dim">--</span>' : 'L' + rfEsc(x.line));
    const who = [nz(x.task), x.case || ''].filter(function(s){ return s && s !== '--'; }).join(' / ');
    return '<tr>'
      + '<td class="cw1">' + (x.time ? rfEsc(x.time) : '<span class="dim">--</span>') + '</td>'
      + '<td class="cw">' + (x.level && x.level !== '--'
          ? rfEsc(x.level) : '<span class="dim">--</span>') + '</td>'
      + '<td class="cw dim">' + rfEsc(who || '--') + '</td>'
      + '<td class="cw dim">' + loc + '</td>'
      + '<td class="expr-txt">' + rfEsc(nz(x.text)) + '</td>'
      + '</tr>';
  }).join('');
  openModal('日志证据（' + evs.length + ' 行）｜#' + rfEsc(nz(r.buildNumber))
      + ' · ' + rfEsc(nz(r.pipeline)),
    '<div class="mtbl"><table class="rca"><colgroup><col style="width:14%"><col style="width:5%">'
    + '<col style="width:23%"><col style="width:14%"><col style="width:44%"></colgroup>'
    + '<thead><tr><th>时间</th><th>级别</th><th>任务 / 用例</th><th>位置</th>'
    + '<th>证据原文（整行，不截断）</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div>'
    + '<div class="mrec-note">证据 = 采集到的日志/报告原始行，逐行完整输出。'
    + '用例级证据取自测试报告 errorMessages（自带时间与位置）；'
    + 'job 日志证据的源行本就带时间戳，已抽到「时间」列；'
    + '没有时间戳的「测试报告」断言显示 --。pytest 汇总行不计入证据。</div>');
}
/* ===== 构建记录：区间内该流水线的**每一次运行** =====
   数据源 = 归档里每日 step_2_runs.json 的 runHistory（整区间全部运行，按结束时间归属日期），
   所以区间一变就按新区间重新筛、重新统计；不是只看被点灯/待查的那几条失败记录。
   dim=va 看「当天是否有可用运行」，dim=bs 看「当天编译是否全成功」。
   同一张表服务两个入口：流水线页区块头的「查看构建记录」、组件级清单里可点的比率数字。 */
function buildsTable(ctx, dim, recs, who){
  const st = rangeStats(ctx, dim);
  if (st.source !== 'runs' || !st.runs.length) return false;
  /* 构建号跳转：优先用 runHistory 自带的 openLiBing 链接（pipelineId/projectId/pipelineRunId），
     没有再回落到同构建号的归档记录里的 links.openlibing；都没有就只显示构建号（不做明细弹窗） */
  const lbByBuild = {};
  (recs || []).forEach(function(r){ lbByBuild[String(r.buildNumber)] = ((r.links || {}).openlibing) || ''; });
  /* 不做按天汇总（不再出现「N 次运行 · 当日达标」这类聚合行），区间内每一次运行直接平铺成一行，
     时间列本身带年月日时分秒，按结束时间倒序即为最新在前 */
  const rows = [];
  const flat = [];
  st.dayList.forEach(function(day){
    (st.dayRuns[day] || []).forEach(function(x){ flat.push(x); });
  });
  flat.sort(function(a, b){ return String(b.end || '').localeCompare(String(a.end || '')); });
  flat.forEach(function(x){
      const lb = x.url || lbByBuild[String(x.rn)] || '';
      const bn = lb
        ? '<a href="' + rfEsc(lb) + '" target="_blank" rel="noopener" title="在 openLiBing 查看构建 #'
          + rfEsc(nz(x.rn)) + '">#' + rfEsc(nz(x.rn)) + '</a>'
        : '<span class="mono">#' + rfEsc(nz(x.rn)) + '</span>';
      const va = (x.iva === true) ? '可用' : ((x.iva === false) ? '<span class="dim">不可用</span>'
        : '<span class="dim">--</span>');
      const bs = (x.bc === null || x.bc === undefined || x.bc === '')
        ? '<span class="dim">--</span>'
        : (Number(x.bpc) || 0) + '/' + Number(x.bc);
      const cpr = (x.cpr === null || x.cpr === undefined || x.cpr === '')
        ? '<span class="dim">--</span>' : Number(x.cpr).toFixed(1) + '%';
      /* 失败类型：服务端 failedTaskType（如「用例失败」「编译失败」）；执行成功时为 null */
      const ftt = (x.ftt === null || x.ftt === undefined || x.ftt === '' || x.ftt === '流水线未失败')
        ? '<span class="dim">--</span>' : rfEsc(String(x.ftt));
      rows.push('<tr>'
        + '<td class="nowrap">' + bn + '</td>'
        + '<td class="nowrap">' + statusTag(x.status) + '</td>'
        + '<td class="nowrap dim">' + ftt + '</td>'
        + '<td class="nowrap">' + va + '</td>'
        + '<td class="nowrap">' + bs + '</td>'
        + '<td class="nowrap dim">' + cpr + '</td>'
        + '<td class="nowrap dim">' + rfEsc(String(x.start || '') || '--') + '</td>'
        + '<td class="nowrap dim">' + rfEsc(String(x.end || '') || '--') + '</td>'
        + '</tr>');
  });
  openModal('构建记录（' + st.runs.length + ' 次运行）｜'
      + rfEsc(who ? who + ' · ' : '') + rfEsc(DIMNAME[dim] || '全部维度'),
    '<div class="mtbl"><table class="rca"><colgroup><col style="width:8%"><col style="width:9%">'
    + '<col style="width:12%"><col style="width:8%"><col style="width:11%">'
    + '<col style="width:9%"><col style="width:21.5%"><col style="width:21.5%"></colgroup>'
    + '<thead><tr><th>构建</th><th>状态</th><th>失败类型</th><th>版本可用</th><th>编译成功/次数</th>'
    + '<th>用例通过率</th><th>开始时间</th><th>结束时间</th></tr></thead>'
    + '<tbody>' + rows.join('') + '</tbody></table></div>'
    + '<div class="mrec-note">区间 ' + rfEsc(ctx.from) + ' ~ ' + rfEsc(ctx.to)
    + ' · 区间内每一次运行逐条平铺（不按天汇总），运行来自归档 step_2_runs.json 的 runHistory'
    + '（按执行结束时间归属日期）· 统计区间一变即按新区间重新筛选、重新计算 · 执行成功也逐条列出'
    + ' · 点构建号直接在 openLiBing 打开该次执行。</div>');
  return true;
}
/* 流水线页区块头入口：CTX / RECS 已在页面加载时备好，直接渲染 */
function openBuilds(){
  const dim = dimOf();
  const ctx = CTX || {dirs: [], days: [], from: '', to: '', runs: RUNS};
  const r0 = RECS[0] || {};
  if (buildsTable(ctx, dim, RECS, r0.pipelineName || r0.pipeline || KEY)) return;
  openBuildsLegacy(dim);
}
/* 组件级清单入口：点「版本可用度 / 编译成功率」数字 → 按 key 找到该流水线的归档目录，
   再按需读 step_2_runs.json 取运行历史（组件级首屏只算比率，不带运行明细，所以这里现取）。
   多个归档日各自带着同一份整区间 runHistory，按构建号去重即可，不会重复出行。 */
function openBuildsOf(key, dim, from, to){
  key = key || ''; dim = (dim === 'bs' ? 'bs' : 'va');
  const label = key.replace('|', ' / ') || '流水线';
  const days = DAYS.filter(function(d){ return d.date >= from && d.date <= to; });
  if (!days.length){
    openModal('构建记录｜' + rfEsc(label),
      '<div class="dim">该区间内没有归档日（' + rfEsc(from) + ' ~ ' + rfEsc(to) + '）。</div>');
    return;
  }
  openModal('构建记录｜' + rfEsc(label), '<div class="loading">正在读取运行历史…</div>');
  const dirs = [], runs = [], seen = {};
  let chain = Promise.resolve();
  days.forEach(function(d){
    chain = chain.then(function(){
      return jget(BACK_TO + d.path + '/_day.json').then(function(day){
        let pl = null;
        (day.pipelines || []).forEach(function(x){ if (x.key === key) pl = x; });
        if (!pl) return;
        dirs.push({date: d.date, path: pl.path, pl: pl});
        return jget(BACK_TO + pl.path + '/step_2_runs.json').then(function(s2){
          ((s2 || {}).runHistory || []).forEach(function(h){
            const rn = String(h.rn);
            if (!rn || rn === 'undefined' || seen[rn]) return;
            seen[rn] = 1;
            const end = String(h.end || '');
            runs.push({rn: h.rn, start: h.start, end: end, day: end.slice(0, 10),
                       status: h.status, url: h.url, iva: h.iva,
                       bc: h.bc, bpc: h.bpc, cpr: h.cpr, ftt: h.ftt});
          });
        }).catch(function(){ /* 老归档没有 step_2_runs.json：下面统一提示 */ });
      }).catch(function(){ /* 单日读取失败跳过，不影响其它日 */ });
    });
  });
  chain.then(function(){
    const ctx = {dirs: dirs, days: days, from: from, to: to, runs: runs};
    if (buildsTable(ctx, dim, [], label)) return;
    openModal('构建记录（0 次运行）｜' + rfEsc(label),
      '<div class="dim">该流水线在区间 ' + rfEsc(from) + ' ~ ' + rfEsc(to)
      + ' 内没有读到运行历史（归档缺少 step_2_runs.json）。</div>');
  });
}
/* 兜底：老归档没有 step_2_runs.json 的运行历史时，仍按「当前维度的归档记录」列出构建 */
function openBuildsLegacy(dim){
  const picked = [];
  RECS.forEach(function(r, i){
    if (dim && (r.light || {}).track !== dim) return;
    picked.push({r: r, i: i});
  });
  if (!picked.length){
    openModal('构建记录（0 条）｜' + rfEsc(DIMNAME[dim] || '全部维度'),
      '<div class="dim">该区间内没有读到这条流水线的运行历史（归档缺少 step_2_runs.json），'
      + '也没有「' + rfEsc(DIMNAME[dim] || '当前维度') + '」维度的失败记录。</div>');
    return;
  }
  const rows = picked.map(function(x){
    const r = x.r, i = x.i;
    const v = r.verdict || {};
    const ftt = r.failedTaskType || r.ftt;
    const lb = (r.links || {}).openlibing || '';
    const bn = lb
      ? '<a href="' + rfEsc(lb) + '" target="_blank" rel="noopener" title="在 openLiBing 查看该次执行">#'
        + rfEsc(nz(r.buildNumber)) + '</a>'
      : '#' + rfEsc(nz(r.buildNumber));
    return '<tr>'
      + '<td class="nowrap">' + bn + '</td>'
      + '<td class="nowrap">' + statusTag(r.status) + '</td>'
      + '<td class="nowrap dim">' + (ftt ? rfEsc(ftt) : '<span class="dim">--</span>') + '</td>'
      + '<td class="nowrap">' + rfEsc(((CATS[v.category] || [v.text || '--']))[0]) + '</td>'
      + '<td class="nowrap dim">' + rfEsc(tm(r.startedAt)) + '</td>'
      + '<td class="nowrap dim">' + rfEsc(tm(r.endedAt)) + '</td>'
      + '</tr>';
  }).join('');
  openModal('构建记录（' + picked.length + ' 条）｜' + rfEsc(DIMNAME[dim] || '全部维度'),
    '<div class="mtbl"><table class="rca"><colgroup><col style="width:11%"><col style="width:11%">'
    + '<col style="width:15%"><col style="width:15%">'
    + '<col style="width:24%"><col style="width:24%"></colgroup>'
    + '<thead><tr><th>构建</th><th>状态</th><th>失败类型</th><th>定性</th>'
    + '<th>开始时间</th><th>结束时间</th></tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div>'
    + '<div class="mrec-note">归档里没有运行历史（step_2_runs.json），只列出当前维度的失败记录（共 '
    + picked.length + ' 条）。点构建号直接在 openLiBing 打开该次执行。</div>');
}

/* 弹窗入口（记录明细页已下线，点构建号直接跳 openLiBing）：
   data-builds      → 流水线页「查看记录」/「构建记录」按钮 → 本页构建记录弹窗
   data-cases       → 根因表「共 N 条失败用例」→ 失败用例详情
   data-evidence    → 根因表「证据」→ 日志证据逐行
   data-builds-of   → 组件级清单里的比率数字 → 该流水线按需拉运行历史再弹窗 */
document.addEventListener('click', function(ev){
  const el = (ev.target && ev.target.closest)
    ? ev.target.closest('[data-builds],[data-cases],[data-evidence],[data-builds-of],#mclose,#mrec')
    : null;
  if (!el) return;
  if (el.id === 'mclose'){ closeModal(); return; }
  if (el.id === 'mrec' && ev.target === el){ closeModal(); return; }
  if (el.hasAttribute('data-builds')){ ev.preventDefault(); openBuilds(); return; }
  if (el.hasAttribute('data-cases')){ ev.preventDefault(); openCases(Number(el.getAttribute('data-cases'))); return; }
  if (el.hasAttribute('data-evidence')){ ev.preventDefault(); openEvidence(Number(el.getAttribute('data-evidence'))); return; }
  if (el.hasAttribute('data-builds-of')){
    ev.preventDefault();
    openBuildsOf(el.getAttribute('data-builds-of'), el.getAttribute('data-dim'),
                 el.getAttribute('data-from'), el.getAttribute('data-to'));
  }
});
document.addEventListener('keydown', function(ev){ if (ev.key === 'Escape') closeModal(); });

boot();
""".replace("__INDEX__", INDEX_NAME)


def write_page(path, title, body, script, extra_css):
    path.write_text(ui.page(title, body, script, extra_css), encoding="utf-8")
    return path


def main():
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    idx = write_page(HTML_DIR / INDEX_NAME, INDEX_TITLE, INDEX_BODY, INDEX_JS, INDEX_EXTRA_CSS)
    det = write_page(HTML_DIR / DETAIL_NAME, DETAIL_TITLE, DETAIL_BODY, DETAIL_JS, DETAIL_EXTRA_CSS)

    days = iter_archive_days()
    page_doc = doc("step_9_pages", files=[INDEX_NAME, DETAIL_NAME], dayCount=len(days))
    meta = {
        "_doc": page_doc,
        "generatedAt": page_doc["generatedAt"],
        "dayCount": len(days),
        "firstDay": days[0][0] if days else None,
        "lastDay": days[-1][0] if days else None,
        "shell": {"title": ui.BRAND_TITLE, "subtitle": ui.BRAND_SUB,
                  "order": ["品牌行（标题 + 一行副标题）", "公共日期筛选条（吸顶 · 系统级全局筛选）", "页面自身内容"],
                  "note": "首页与二级页共用 lib/ui.py 的同一份 header + toolbar，且都排在最前；"
                          "筛选条 position:sticky 吸顶，切页/滚动都不动位置，语义始终是整个系统的数据筛选。"
                          "首页表格行高在渲染后按「可用高度 / 行数」写入 --rowh，13 条数据刚好铺满表格区。"},
        "files": [
            {"name": INDEX_NAME, "role": "首页（组件级概览）",
             "reads": ["archive/_index.json", "archive/_products.json",
                       "archive/<年>/<年-月>/<日>/_day.json"],
             "fields": ["开源组件", "版本可用度", "编译成功率", "口径内流水线",
                        "待分析（可用度）", "待分析（编译）"],
             "drilldown": "首页不做展开：只有「待分析流水线数」是非零时可点数字（同旧版 .lnk），"
                          "点进二级页 ?comp=组件&dim=va|bs 看该组件的流水线清单；"
                          "组件级清单里点比率数字弹「构建记录」、点「查看记录 →」进流水线明细（100% 的行显示 -- 不可点），"
                          "流水线明细里点「共 N 条失败用例」/「证据」各自弹窗",
             "note": "开源组件行取自 _products.json 的全量清单（无待分析流水线的显示 0）；"
                     "表格版式与旧报告 table.ov 一致，不含任何展开/折叠行"},
            {"name": DETAIL_NAME, "role": "二级页（?comp=组件&dim=va|bs 组件级流水线清单 / ?key=组件|流水线 流水线级根因明细）",
             "reads": ["archive/<日>/_day.json", "archive/<日>/<组件>/<流水线>/_manifest.json",
                       "…/records/run_<构建号>.json"],
             "fields": ["构建编号", "任务", "用例", "错误信息", "定性", "根因", "建议"],
             "compFields": {"dim=va": ["流水线", "平台", "版本可用度", "查看记录"],
                            "dim=bs": ["流水线", "平台", "编译成功率", "查看记录"]},
             "compNote": "组件级清单按维度下钻：dim=va 只渲染版本可用度列、dim=bs 只渲染编译成功率列，"
                         "另一维度不渲染；不再展示归档日；比率显示为纯数字百分比（如 80.0%，"
                         "取该流水线区间内各归档日流线级比率的算术平均），不带黄/绿/红圆点或灯色文字；"
                         "比率数字本身可点 → 弹「构建记录」（点击时才按需读该流水线的 step_2_runs.json）；"
                         "比率 100% 的行「查看记录」直接显示 --（不给链接、不响应点击），因为区间内没有失败记录可下钻",
             "note": "版式同旧报告：一条流水线一个浅蓝底 h3 区块 + 一张 7 列固定列宽（8/11/11/22/7/24/17）的 table.rca，"
                     "table-layout:fixed 保证列宽不随内容变化；每行 = 一条执行记录，第一列为「构建编号」（跳 openLiBing），"
                     "已删除「根因编号」列；"
                     "「用例」列展示第一条失败用例名 + 可点的「共 N 条失败用例」→ 弹窗逐条给用例详情；"
                     "「错误信息」列只给真因原文 + 可点的「证据」→ 弹窗逐行输出日志/报告原始行（含时间/级别/位置）；"
                     "已下线记录明细弹窗，弹窗只剩「失败用例」「日志证据」「构建记录」三个入口；"
                     "不读 step_6_records.json（单份可达 80KB+），逐条读单条记录"},
        ],
        "dataInline": False,
        "dateBasis": {"field": "pipelineRunEndtime",
                      "rule": "页面筛选与归档归属一致，均为执行结束时间"},
        "serving": "python run_pipeline.py --serve   # 或 python -m http.server 8765 --directory out",
        "filesWritten": [{"file": idx.name, "bytes": idx.stat().st_size},
                         {"file": det.name, "bytes": det.stat().st_size}],
    }
    (HTML_DIR / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    print("== 第 9 步 展示页已生成（只含静态壳，业务数据运行时读 out/archive/）")
    print("== 首页   %s  %.1f KB" % (rel_html(idx), idx.stat().st_size / 1024.0))
    print("== 二级页 %s  %.1f KB" % (rel_html(det), det.stat().st_size / 1024.0))
    print("== 数据源 out/archive/：%d 个归档日%s"
          % (len(days), ("（%s ~ %s）" % (days[0][0], days[-1][0])) if days else "（暂无，先跑采集）"))
    print("== 说明   %s：记录生成方式/数据源/口径" % META_NAME)
    print("!! 直接双击 file:// 打开会被浏览器拦截 fetch，请用 python run_pipeline.py --serve 起本地服务访问")
    return 0


def rel_html(p):
    """打印用：相对 out/ 的路径（out/html/index.html）。"""
    return rel(p)


if __name__ == "__main__":
    sys.exit(main())
