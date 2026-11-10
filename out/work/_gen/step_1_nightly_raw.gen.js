(async () => {
const PARAMS = {"startDate": "2026-11-06", "endDate": "2026-11-09", "t1PageSize": 100, "detailPageSize": 10, "onlyProducts": [], "delay": 250, "maxTry": 3, "retryBackoffMs": [1000, 2000]};
return (async () => {
  const csrf = (document.cookie.split('; ').find(c => c.startsWith('csrf-token-open-li-bing=')) || '').split('=').slice(1).join('=');
  if (!csrf) return JSON.stringify({ ok: false, error: 'NOT_LOGGED_IN：请先在调试 Chrome 中登录 openLiBing' });
  const H = { 'Content-Type': 'application/json', 'Csrf-Token-Open-Li-Bing': csrf };
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // 重试口径：单次请求成功 = 响应体非空 + JSON 可解析 + code===200 + data 非空。
  // 空响应体 / 解析失败 / code!=200 / data 为空 一律重试，最多 MAX_TRY 次，退避 BACKOFF（1s、2s）。
  // 不比对「采到的流水线条数」与产品数：流水线数本身会增减，0 条也可能是真实结果。
  const MAX_TRY = Math.max(1, Number(PARAMS.maxTry) || 3);
  const BACKOFF = (Array.isArray(PARAMS.retryBackoffMs) && PARAMS.retryBackoffMs.length)
    ? PARAMS.retryBackoffMs : [1000, 2000];

  async function postJson(url, body) {
    let last = { code: -1, error: 'UNKNOWN' };
    for (let i = 1; i <= MAX_TRY; i++) {
      try {
        const res = await fetch(url, { method: 'POST', headers: H, body: JSON.stringify(body) });
        const text = (await res.text()) || '';
        let json = null;
        if (text.trim()) {
          try { json = JSON.parse(text); } catch (e) {
            last = { code: -1, error: 'JSON_PARSE_FAIL: ' + String(e && e.message || e), http: res.status, bodySize: text.length, attempt: i };
          }
        } else {
          last = { code: -1, error: 'EMPTY_BODY：响应体为空', http: res.status, attempt: i };
        }
        if (json) {
          if (json.code === 200 && json.data != null) return { ok: true, resp: json, attempts: i };
          if (json.code === 200) {
            last = { code: 200, error: 'EMPTY_DATA：code=200 但 data 为空', http: res.status, attempt: i };
          } else {
            last = Object.assign({}, json, {
              error: json.messageCn || json.msg || ('code=' + json.code),
              http: res.status, attempt: i
            });
          }
        }
      } catch (e) {
        last = { code: -1, error: String(e && e.message || e), attempt: i };
      }
      if (i < MAX_TRY) await sleep(Number(BACKOFF[Math.min(i - 1, BACKOFF.length - 1)]) || 1000);
    }
    return { ok: false, resp: last, attempts: MAX_TRY };
  }

  const t1r = await postJson('/gateway/openlibing-ops/manage/ops-overview/main',
    { startDate: PARAMS.startDate, endDate: PARAMS.endDate, page: 1, pageSize: PARAMS.t1PageSize });
  if (!t1r.ok) {
    return JSON.stringify({ ok: false, error: '产品清单(ops-overview/main)拉取失败: ' + last_error(t1r), t1: t1r.resp });
  }
  const all = t1r.resp.data.records || [];
  const products = all.map(r => ({
    name: r.projectName, projectId: r.projectId,
    nightlyVersionAvailabilityRate: r.nightlyVersionAvailabilityRate,
    nightlyBuildSuccessRate: r.nightlyBuildSuccessRate
  })).filter(p => p.name && p.projectId);
  if (!products.length) {
    return JSON.stringify({ ok: false, error: '产品清单为空：' + PARAMS.startDate + ' ~ ' + PARAMS.endDate + ' 看板未返回任何产品' });
  }
  const targets = PARAMS.onlyProducts.length
    ? products.filter(p => PARAMS.onlyProducts.includes(p.name))
    : products;

  const details = {}, retryInfo = {}, failures = [];
  for (const p of targets) {
    const r = await postJson('/gateway/openlibing-ops/manage/common/detail', {
      category: 'ops-nightly-detail', endDate: PARAMS.endDate, page: 1,
      pageSize: PARAMS.detailPageSize, projectId: p.projectId, startDate: PARAMS.startDate
    });
    details[p.name] = r.resp;
    retryInfo[p.name] = { ok: r.ok, attempts: r.attempts, error: r.ok ? null : last_error(r) };
    if (!r.ok) failures.push({ product: p.name, projectId: p.projectId, attempts: r.attempts, error: last_error(r) });
    await sleep(PARAMS.delay);
  }

  function last_error(r) {
    const x = r && r.resp || {};
    return x.error || x.messageCn || x.msg || ('code=' + x.code) || 'unknown';
  }

  return JSON.stringify({
    ok: true, startDate: PARAMS.startDate, endDate: PARAMS.endDate,
    maxTry: MAX_TRY, allProducts: products, targets, details, retryInfo, failures
  });
})()

})()