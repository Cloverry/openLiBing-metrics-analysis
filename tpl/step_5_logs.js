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
      const res = await fetch(G + '/project/pipeline/logs', {
        method: 'POST', headers: H,
        body: JSON.stringify({
          projectId: it.projectId, pipelineId: it.pipelineId, pipelineRunId: it.rid,
          jobRunId: it.jobId, stepRunId: it.stepIds[0],
          sort: 'desc', limit: PARAMS.logLimit, startOffset: 0, endOffset: 0
        })
      });
      const j = await res.json();
      const d = j.data || {};
      const logText = d.log || '';
      const errLines = logText.split('\n').filter(l => /\[ERROR\]|\[FAIL|ERROR|FAIL|失败/i.test(l)).slice(0, PARAMS.maxErrLines);
      results.push({
        route: 'log', key: it.key, pipeline: it.pipeline, rn: it.rn, st: it.st,
        projectId: it.projectId, pipelineId: it.pipelineId, rid: it.rid,
        jobId: it.jobId, jobName: it.jobName, stepId: it.stepIds[0],
        fallback: !!it.fallback,
        code: j.code, has_more: d.has_more,
        logTail: logText.slice(-4000),
        errorLines: errLines
      });
    } catch (e) { errors.push({ key: it.key, rn: it.rn, jobName: it.jobName, err: String(e && e.message || e) }); }
  }
  for (let i = 0; i < PARAMS.items.length; i += CONC) {
    await Promise.all(PARAMS.items.slice(i, i + CONC).map(fetchOne));
    if (i + CONC < PARAMS.items.length) await sleep(PARAMS.delay);
  }
  return JSON.stringify({ ok: true, collected: results.length, results, errors });
})()
