(async () => {
const PARAMS = {"items": [{"key": "HPCKit|kpex", "pipeline": "kpex", "pipelineName": "Nightly-CI-Component-pipeline_kpex", "projectId": 300073, "pipelineId": "fec036d3c59f4d27bb506138f273f113", "rid": "08890715f1e8442288cc08c1b92cc7b0", "rn": 139, "st": "11-06 00:41", "jobId": "1f0b204bb4994e47ae518ba383a72705", "jobName": "Test_kpex", "stepIds": ["c7043717702d4e13864659bb0dc0dabe"], "steps": [{"id": "c7043717702d4e13864659bb0dc0dabe", "name": "Test_kpex", "status": "FAILED", "errMsg": "{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "lastDispatchId": "14646c09e164482d91d42b964421e363", "jobIdParam": "c42eae717a2e4bf496f89ffc8070b535"}], "jobErrMsg": "步骤Test_kpex执行失败，错误信息：{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "jobLastDispatchId": "712aa351f8094a26a4148195bc2c447f", "route": "log", "fallback": true}, {"key": "HPCKit|kupl", "pipeline": "kupl", "pipelineName": "Nightly-CI-Component-pipeline_kupl", "projectId": 300073, "pipelineId": "ddb211b1e4a74f55a0e604fa95ef469e", "rid": "83e34fe2e1184610959638c360c41c45", "rn": 299, "st": "11-06 13:30", "jobId": "bb4373a54d9a4cc2b52a53177f551672", "jobName": "Test_kupl", "stepIds": ["f6dc1f81bdcc4297ada3a4ae12194456"], "steps": [{"id": "f6dc1f81bdcc4297ada3a4ae12194456", "name": "Test_kupl", "status": "FAILED", "errMsg": "{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "lastDispatchId": "8ed9394a40c5486c8645ab332e010102", "jobIdParam": "179b784b1f2f488595de68bfb2877fe8"}], "jobErrMsg": "步骤Test_kupl执行失败，错误信息：{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "jobLastDispatchId": "9aeeb8a852ea4a8dbf35e32b1c1cde17", "route": "log", "fallback": true}, {"key": "Triton_Ascend|Ascend", "pipeline": "Ascend", "pipelineName": "Nightly-CI-Version-pipeline_Triton_Ascend", "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "rid": "9ae1e7dda41c4a78957a5ba09d2e5b3c", "rn": 104, "st": "11-06 17:00", "jobId": "2f8ebba6f3024d659e6f27bd51a95b98", "jobName": "Test_Triton_Ascend", "stepIds": ["6e91ceb17fcb4a54b87c3a718fba58f8"], "steps": [{"id": "6e91ceb17fcb4a54b87c3a718fba58f8", "name": "Test_Triton_Ascend", "status": "FAILED", "errMsg": "{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "lastDispatchId": "4e406501030d439b87085b5c49d861ed", "jobIdParam": "48c3b7763886441dafd7fdcb5550116f"}], "jobErrMsg": "步骤Test_Triton_Ascend执行失败，错误信息：{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "jobLastDispatchId": "f8c1f056c697430cab6bbea92d4c406a", "route": "log", "fallback": true}], "delay": 250, "conc": 4, "pollTimeoutMs": 180000};
return (async () => {
  const csrf = (document.cookie.split('; ').find(c => c.startsWith('csrf-token-open-li-bing=')) || '').split('=').slice(1).join('=');
  if (!csrf) return JSON.stringify({ ok: false, error: 'NOT_LOGGED_IN' });
  const H = { 'Content-Type': 'application/json', 'Csrf-Token-Open-Li-Bing': csrf };
  const CICD = '/gateway/openlibing-cicd';
  const FW = '/gateway/openlibing-framework';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const results = [];
  const pending = [];
  const CONC = Math.max(1, PARAMS.conc || 1);

  // 阶段 1：创建导出任务（整 job 全量日志，同一 job 多个失败 step 共用一份）；按 CONC 分组并发
  async function createOne(it) {
    const body = {
      projectId: it.projectId, pipelineId: it.pipelineId, pipelineRunId: it.rid,
      jobRunId: it.jobId, stepRunId: it.stepIds[0], taskName: it.jobName
    };
    const ldi = (it.steps && it.steps[0] && it.steps[0].lastDispatchId) || it.jobLastDispatchId;
    if (ldi) body.lastDispatchId = ldi;
    try {
      const res = await fetch(CICD + '/project/pipeline/exec-log/export', {
        method: 'POST', headers: H, body: JSON.stringify(body)
      });
      const j = await res.json();
      if (j.code === 200 && j.data) {
        pending.push({ it, exportId: String(j.data) });
      } else {
        results.push({ ...it, export: 'failed', reason: 'create: ' + JSON.stringify(j).slice(0, 200) });
      }
    } catch (e) {
      results.push({ ...it, export: 'failed', reason: 'create: ' + String(e && e.message || e) });
    }
  }
  for (let i = 0; i < PARAMS.items.length; i += CONC) {
    await Promise.all(PARAMS.items.slice(i, i + CONC).map(createOne));
    if (i + CONC < PARAMS.items.length) await sleep(PARAMS.delay);
  }

  // 阶段 2：轮询导出任务状态（data.message「导出文件生成中」继续 /「导出成功」取直链 / 其它判失败）；
  // 每轮对所有未完成项并发查询
  async function checkOne(p) {
    let j = null;
    try {
      const res = await fetch(FW + '/export/detail?id=' + encodeURIComponent(p.exportId),
                              { headers: { 'Csrf-Token-Open-Li-Bing': csrf } });
      j = await res.json();
    } catch (e) { /* 网络抖动，下一轮再查 */ }
    if (!j) return;
    const d = j.data || {};
    const msg = String(d.message || d.msg || '');
    if (msg.indexOf('导出成功') >= 0) {
      p.done = true;
      try {
        const res2 = await fetch(FW + '/export/download-url?id=' + encodeURIComponent(p.exportId),
                                  { headers: { 'Csrf-Token-Open-Li-Bing': csrf } });
        const j2 = await res2.json();
        if (j2 && j2.data) {
          results.push({ ...p.it, export: 'ok', exportId: p.exportId, url: String(j2.data) });
        } else {
          results.push({ ...p.it, export: 'failed', reason: 'download-url 空: ' + JSON.stringify(j2).slice(0, 150) });
        }
      } catch (e) {
        results.push({ ...p.it, export: 'failed', reason: 'download-url: ' + String(e && e.message || e) });
      }
    } else if (msg && msg.indexOf('生成中') < 0) {
      p.done = true;
      results.push({ ...p.it, export: 'failed', reason: 'detail: ' + msg.slice(0, 150) });
    }
  }
  const deadline = Date.now() + PARAMS.pollTimeoutMs;
  while (pending.some(p => !p.done) && Date.now() < deadline) {
    await Promise.all(pending.filter(p => !p.done).map(checkOne));
    if (pending.some(p => !p.done)) await sleep(2000);
  }
  for (const p of pending) if (!p.done) results.push({ ...p.it, export: 'timeout', reason: '轮询超时' });

  return JSON.stringify({ ok: true, created: results.filter(r => r.export === 'ok').length, results });
})()

})()