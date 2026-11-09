(async () => {
const PARAMS = {"items": [{"key": "HPCKit|kupl", "pipeline": "kupl", "pipelineName": "Nightly-CI-Component-pipeline_kupl", "projectId": 300073, "pipelineId": "ddb211b1e4a74f55a0e604fa95ef469e", "rid": "d0720a2f2a054736a8966e49063b89dc", "rn": 297, "st": "11-06 00:01", "jobId": "4045c06725184de7b48a42da6c8d440f", "jobName": "Test_kupl", "stepIds": ["2d5f5b553998494eaf1611bf698f477e"], "steps": [{"id": "2d5f5b553998494eaf1611bf698f477e", "name": "Test_kupl", "status": "FAILED", "errMsg": "{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "lastDispatchId": "f5d0af306d7f468b842d0f762c47c294", "jobIdParam": "179b784b1f2f488595de68bfb2877fe8"}], "jobErrMsg": "步骤Test_kupl执行失败，错误信息：{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "jobLastDispatchId": "f1357070ee02435ea0176444ae3f02b5", "route": "log", "fallback": true}, {"key": "HPCKit|kupl", "pipeline": "kupl", "pipelineName": "Nightly-CI-Component-pipeline_kupl", "projectId": 300073, "pipelineId": "ddb211b1e4a74f55a0e604fa95ef469e", "rid": "0323dea18d954ecea0b5cc44c1c34696", "rn": 298, "st": "11-06 12:00", "jobId": "878efef62881418ab98220513a834dfc", "jobName": "Test_kupl", "stepIds": ["b4ae9d9c622c42368d8d122134eafb34"], "steps": [{"id": "b4ae9d9c622c42368d8d122134eafb34", "name": "Test_kupl", "status": "FAILED", "errMsg": "{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "lastDispatchId": "5dc50b8421f64fe5b8e5497fb3b420ed", "jobIdParam": "179b784b1f2f488595de68bfb2877fe8"}], "jobErrMsg": "步骤Test_kupl执行失败，错误信息：{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "jobLastDispatchId": "1aa89241bcc94a9aa964a9e22e10fddc", "route": "log", "fallback": true}], "logLimit": 300, "maxErrLines": 40, "delay": 250, "conc": 4};
return (async () => {
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

})()