(async () => {
const PARAMS = {"startDate": "2026-11-06", "endDate": "2026-11-09", "pageSize": 100, "delay": 250, "conc": 4, "pipelines": [{"key": "MindCluster|VisionSDK", "projectId": 300038, "pipelineId": "ac8a7ce54ea347c08a697987c21653fc"}, {"key": "MindCluster|MultimodalSDK", "projectId": 300038, "pipelineId": "c39c0146a8b2492db59ec77bedb7170c"}, {"key": "MindCluster|RAGSDK", "projectId": 300038, "pipelineId": "fd920c7904cb47ad9ce80d4eaede5cb5"}, {"key": "Ascend-CANN|os", "projectId": 300057, "pipelineId": "03da2691669a4f2bbfcfd89356bc3576"}, {"key": "Ascend-CANN|ops-transformer", "projectId": 300057, "pipelineId": "309180d5c2904adf942f5678d9356d99"}, {"key": "Ascend-CANN|ops-cv", "projectId": 300057, "pipelineId": "66b8958d6e0349ddb01143765c64afa5"}, {"key": "Ascend-CANN|ops-nn", "projectId": 300057, "pipelineId": "6d402b76f9b14451872ef07ac1f864ba"}, {"key": "Ascend-CANN|hcomm", "projectId": 300057, "pipelineId": "917e9d4ef3624408999817444349c4dc"}, {"key": "Ascend-CANN|shmem", "projectId": 300057, "pipelineId": "93831fdaa3234c8a9dd0b8a0c220f939"}, {"key": "Ascend-CANN|ops-math", "projectId": 300057, "pipelineId": "aa3d520fe64f4071a09178d2d603278e"}, {"key": "Ascend-CANN|atb", "projectId": 300057, "pipelineId": "b4f302b7c22b4eacab56d4b120bf7d04"}, {"key": "Ascend-CANN|sip", "projectId": 300057, "pipelineId": "ca98a3e75ff84332a3abd4f091a0c233"}, {"key": "boostkit|avx2ki", "projectId": 300059, "pipelineId": "2944ce957b494b72a41150b5467b61b4"}, {"key": "boostkit|kqmalloc", "projectId": 300059, "pipelineId": "452bb706ced24726aa7686b92a966734"}, {"key": "boostkit|OmniStream", "projectId": 300059, "pipelineId": "636f9465b49d4a6591d5572ad6f9456f"}]};
return (async () => {
  // 【N2】POST /gateway/openlibing-ops/manage/common/detail  category=nightly-dashboard-detail
  // 返回每流水线逐运行看板明细（即执行记录来源）：pipelineRunNumber/Id/Status/起止时间 +
  // isVersionAvailable / buildCount / buildPassCount / failedTaskType / casePassRate 等，
  // 供 VA 双指标口径判定、双轨道圈定与编译成功率复算；记录超过一页时按 total 翻页拉全
  const csrf = (document.cookie.split('; ').find(c => c.startsWith('csrf-token-open-li-bing=')) || '').split('=').slice(1).join('=');
  if (!csrf) return JSON.stringify({ ok: false, error: 'NOT_LOGGED_IN' });
  const H = { 'Content-Type': 'application/json', 'Csrf-Token-Open-Li-Bing': csrf };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const out = {};
  const CONC = Math.max(1, PARAMS.conc || 1);
  async function fetchPipe(p) {
    try {
      const recs = [];
      let page = 1, total = 0, last = null;
      for (;;) {
        const res = await fetch('/gateway/openlibing-ops/manage/common/detail', {
          method: 'POST', headers: H,
          body: JSON.stringify({ category: 'nightly-dashboard-detail', endDate: PARAMS.endDate,
            page, pageSize: PARAMS.pageSize, pipelineId: p.pipelineId,
            projectId: p.projectId, startDate: PARAMS.startDate })
        });
        last = await res.json();
        const d = (last && last.data) || {};
        const list = d.records || [];
        recs.push(...list);
        total = d.total || recs.length;
        if (!list.length || recs.length >= total) break;
        page += 1;
        await sleep(PARAMS.delay);
      }
      out[p.key] = Object.assign({}, last, { data: { records: recs, total: total } });
    } catch (e) { out[p.key] = { code: -1, error: String(e && e.message || e) }; }
  }
  for (let i = 0; i < PARAMS.pipelines.length; i += CONC) {
    await Promise.all(PARAMS.pipelines.slice(i, i + CONC).map(fetchPipe));
    if (i + CONC < PARAMS.pipelines.length) await sleep(PARAMS.delay);
  }
  return JSON.stringify({ ok: true, dash: out });
})()

})()