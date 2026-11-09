(async () => {
  const csrf = (document.cookie.split('; ').find(c => c.startsWith('csrf-token-open-li-bing=')) || '').split('=').slice(1).join('=');
  if (!csrf) return JSON.stringify({ ok: false, error: 'NOT_LOGGED_IN：请先在调试 Chrome 中登录 openLiBing' });
  const H = { 'Content-Type': 'application/json', 'Csrf-Token-Open-Li-Bing': csrf };
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const t1Res = await fetch('/gateway/openlibing-ops/manage/ops-overview/main', {
    method: 'POST', headers: H,
    body: JSON.stringify({ startDate: PARAMS.startDate, endDate: PARAMS.endDate, page: 1, pageSize: PARAMS.t1PageSize })
  });
  const t1 = await t1Res.json();
  const all = ((t1.data || {}).records) || [];
  const products = all.map(r => ({
    name: r.projectName, projectId: r.projectId,
    nightlyVersionAvailabilityRate: r.nightlyVersionAvailabilityRate,
    nightlyBuildSuccessRate: r.nightlyBuildSuccessRate
  })).filter(p => p.name && p.projectId);
  const targets = PARAMS.onlyProducts.length
    ? products.filter(p => PARAMS.onlyProducts.includes(p.name))
    : products;

  const details = {};
  for (const p of targets) {
    try {
      const res = await fetch('/gateway/openlibing-ops/manage/common/detail', {
        method: 'POST', headers: H,
        body: JSON.stringify({
          category: 'ops-nightly-detail', endDate: PARAMS.endDate, page: 1,
          pageSize: PARAMS.detailPageSize, projectId: p.projectId, startDate: PARAMS.startDate
        })
      });
      details[p.name] = await res.json();
    } catch (e) { details[p.name] = { code: -1, error: String(e && e.message || e) }; }
    await sleep(PARAMS.delay);
  }
  return JSON.stringify({
    ok: true, startDate: PARAMS.startDate, endDate: PARAMS.endDate,
    allProducts: products, targets, details
  });
})()
