(async () => {
const PARAMS = {"startDate": "2026-11-06", "endDate": "2026-11-09", "pageSize": 100, "delay": 250, "conc": 4, "pipelines": [{"key": "vllm-ascend|a2.yaml", "projectId": 300127, "pipelineId": "228664975"}, {"key": "vllm-ascend|a3.yaml", "projectId": 300127, "pipelineId": "228664976"}, {"key": "Triton_Ascend|Ascend", "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6"}, {"key": "SgLang|nightly-test-npu.yml", "projectId": 300134, "pipelineId": "209837578"}]};
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