(async () => {
const PARAMS = {"startDate": "2026-11-06", "endDate": "2026-11-06", "pageSize": 100, "delay": 250, "conc": 4, "pipelines": [{"key": "boostkit|Megascan", "projectId": 300059, "pipelineId": "9485ce9d987d4717a944aa3932474391"}, {"key": "boostkit|Bigdata", "projectId": 300059, "pipelineId": "a74a64d931f34c98bc726e5ea599f8a3"}, {"key": "boostkit|KTAG", "projectId": 300059, "pipelineId": "a8c3dbdd3feb4c4ab4a4a336e1323ce3"}, {"key": "boostkit|Ultrascan", "projectId": 300059, "pipelineId": "b0040d6ccaae48b5b623931d34db5d24"}, {"key": "boostkit|KAE", "projectId": 300059, "pipelineId": "eaf0862dd3f84fc2b51862830d7fe19e"}, {"key": "HPCKit|kudnn", "projectId": 300073, "pipelineId": "ca83efb8c37648db8084e31b1e88b62c"}, {"key": "HPCKit|kupl", "projectId": 300073, "pipelineId": "ddb211b1e4a74f55a0e604fa95ef469e"}, {"key": "HPCKit|kuqcd", "projectId": 300073, "pipelineId": "eeedf3e4794449969ad6d7f67691d0be"}, {"key": "HPCKit|kpex", "projectId": 300073, "pipelineId": "fec036d3c59f4d27bb506138f273f113"}, {"key": "HPCKit|hmpi", "projectId": 300073, "pipelineId": "ffe131425ad84d4b8b6c17c4c09476d9"}, {"key": "MindSpeed|master", "projectId": 300091, "pipelineId": "0260b812efd04b0ebf649f3a4c533996"}, {"key": "MindSpeed|master", "projectId": 300091, "pipelineId": "094eb44f70df495783f375641b7143e6"}, {"key": "MindSpeed|master", "projectId": 300091, "pipelineId": "b30f40b85cce4153b175379589793abc"}, {"key": "MindSpeed|master", "projectId": 300091, "pipelineId": "e12bb930ffe84e3b9258f23c41644b58"}, {"key": "MindSpeed|master", "projectId": 300091, "pipelineId": "e593e0b8911044b69ce9d1be3c19032a"}]};
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