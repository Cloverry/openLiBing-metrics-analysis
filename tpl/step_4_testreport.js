(async () => {
  const csrf = (document.cookie.split('; ').find(c => c.startsWith('csrf-token-open-li-bing=')) || '').split('=').slice(1).join('=');
  if (!csrf) return JSON.stringify({ ok: false, error: 'NOT_LOGGED_IN' });
  const H = { 'Content-Type': 'application/json', 'Csrf-Token-Open-Li-Bing': csrf };
  const G = '/gateway/openlibing-cicd';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const results = [], errors = [];
  const CONC = Math.max(1, PARAMS.conc || 1);
  async function fetchOne(it) {
    try {
      const res = await fetch(G + '/project/pipeline/test-report', {
        method: 'POST', headers: H,
        body: JSON.stringify({
          projectId: it.projectId, pipelineId: it.pipelineId, pipelineRunId: it.rid,
          stepRunIds: it.stepIds.join(','), name: '', number: '',
          state: ['failed'], pageNumber: 1, pageSize: PARAMS.pageSize
        })
      });
      const j = await res.json();
      const d = j.data || {};
      const list = d.list || d.items || d.taskResults || [];
      const cases = (Array.isArray(list) ? list : []).map(c => ({
        name: c.name, number: c.number, state: c.state, description: c.description,
        beginTime: c.beginTime, endTime: c.endTime,
        resultDownloadUrl: c.resultDownloadUrl, envDownloadUrl: c.envDownloadUrl
      }));
      results.push({
        route: 'test', key: it.key, pipeline: it.pipeline, pipelineName: it.pipelineName,
        rn: it.rn, st: it.st,
        projectId: it.projectId, pipelineId: it.pipelineId, rid: it.rid,
        jobId: it.jobId, jobName: it.jobName, stepIds: it.stepIds,
        steps: it.steps || [], jobErrMsg: it.jobErrMsg || null,
        jobLastDispatchId: it.jobLastDispatchId || null,
        code: j.code,
        summary: { total: d.totalCount ?? d.total, passed: d.passedCount, failed: d.failedCount, passRate: d.passRate },
        failedCases: cases,
        empty: cases.length === 0
      });
    } catch (e) { errors.push({ key: it.key, rn: it.rn, jobName: it.jobName, err: String(e && e.message || e) }); }
  }
  for (let i = 0; i < PARAMS.items.length; i += CONC) {
    await Promise.all(PARAMS.items.slice(i, i + CONC).map(fetchOne));
    if (i + CONC < PARAMS.items.length) await sleep(PARAMS.delay);
  }
  return JSON.stringify({ ok: true, collected: results.length, results, errors });
})()
