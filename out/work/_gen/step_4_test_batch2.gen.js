(async () => {
const PARAMS = {"items": [{"key": "HPCKit|hmpi", "pipeline": "hmpi", "pipelineName": "Nightly-CI-Component-pipeline_hmpi", "projectId": 300073, "pipelineId": "ffe131425ad84d4b8b6c17c4c09476d9", "rid": "9c42f33ef29645839271036fce5cfe60", "rn": 210, "st": "11-06 01:01", "jobId": "52332eeed4bb46beb6dafb03d2aa1730", "jobName": "Test_hmpi", "stepIds": ["a0ab84e24dac48d9b7e1d50bc7702565"], "steps": [{"id": "a0ab84e24dac48d9b7e1d50bc7702565", "name": "Test_hmpi", "status": "FAILED", "errMsg": "{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "lastDispatchId": "7c96cc5b85204f4ebeb7e38b6014aefe", "jobIdParam": "215162ed521b406e9e13eb0c9f2ac64e"}], "jobErrMsg": "步骤Test_hmpi执行失败，错误信息：{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "jobLastDispatchId": "72295ee7da0b40ef8c7ceb4831c46e9d", "route": "test", "isBuild": false, "buildJobId": "215162ed521b406e9e13eb0c9f2ac64e"}, {"key": "HPCKit|kpex", "pipeline": "kpex", "pipelineName": "Nightly-CI-Component-pipeline_kpex", "projectId": 300073, "pipelineId": "fec036d3c59f4d27bb506138f273f113", "rid": "08890715f1e8442288cc08c1b92cc7b0", "rn": 139, "st": "11-06 00:41", "jobId": "1f0b204bb4994e47ae518ba383a72705", "jobName": "Test_kpex", "stepIds": ["c7043717702d4e13864659bb0dc0dabe"], "steps": [{"id": "c7043717702d4e13864659bb0dc0dabe", "name": "Test_kpex", "status": "FAILED", "errMsg": "{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "lastDispatchId": "14646c09e164482d91d42b964421e363", "jobIdParam": "c42eae717a2e4bf496f89ffc8070b535"}], "jobErrMsg": "步骤Test_kpex执行失败，错误信息：{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "jobLastDispatchId": "712aa351f8094a26a4148195bc2c447f", "route": "test", "isBuild": false, "buildJobId": "c42eae717a2e4bf496f89ffc8070b535"}, {"key": "Triton_Ascend|Ascend", "pipeline": "Ascend", "pipelineName": "Nightly-CI-Version-pipeline_Triton_Ascend", "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "rid": "9ae1e7dda41c4a78957a5ba09d2e5b3c", "rn": 104, "st": "11-06 17:00", "jobId": "2f8ebba6f3024d659e6f27bd51a95b98", "jobName": "Test_Triton_Ascend", "stepIds": ["6e91ceb17fcb4a54b87c3a718fba58f8"], "steps": [{"id": "6e91ceb17fcb4a54b87c3a718fba58f8", "name": "Test_Triton_Ascend", "status": "FAILED", "errMsg": "{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "lastDispatchId": "4e406501030d439b87085b5c49d861ed", "jobIdParam": "48c3b7763886441dafd7fdcb5550116f"}], "jobErrMsg": "步骤Test_Triton_Ascend执行失败，错误信息：{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "jobLastDispatchId": "f8c1f056c697430cab6bbea92d4c406a", "route": "test", "isBuild": false, "buildJobId": "48c3b7763886441dafd7fdcb5550116f"}], "pageSize": 100, "delay": 250, "conc": 4};
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

})()