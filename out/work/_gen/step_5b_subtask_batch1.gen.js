(async () => {
const PARAMS = {"items": [{"key": "boostkit|Bigdata", "pipeline": "Bigdata", "pipelineName": "Nightly-CI-Component-pipeline_Bigdata", "projectId": 300059, "pipelineId": "a74a64d931f34c98bc726e5ea599f8a3", "rid": "36146a33b2ce44e08d0b6f12bc1c1e23", "rn": 11, "st": "10-08 10:04", "jobId": "9dc4eb132ab440b8b52fcf8986df4971", "jobName": "Build_Gluten", "stepIds": ["60a19cdf907041628764b53c47e5cf56"], "steps": [{"id": "60a19cdf907041628764b53c47e5cf56", "name": "Build_Gluten", "status": "FAILED", "errMsg": "{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "lastDispatchId": "849f259004dc4cbc93815e693568cc17", "jobIdParam": "30880ad793e04de9bc793ecdb4fbbd7a"}], "jobErrMsg": "步骤Build_Gluten执行失败，错误信息：{\"errorMessage\":\" 构建任务执行失败!\",\"errorCode\":\"DEV-CODECI-35002\"}", "jobLastDispatchId": "837017a606a3428dbcd243804f9d7338", "route": "log", "isBuild": true, "buildJobId": "30880ad793e04de9bc793ecdb4fbbd7a"}], "delay": 250, "conc": 4, "pollTimeoutMs": 180000};
return (async () => {
  const csrf = (document.cookie.split('; ').find(c => c.startsWith('csrf-token-open-li-bing=')) || '').split('=').slice(1).join('=');
  if (!csrf) return JSON.stringify({ ok: false, error: 'NOT_LOGGED_IN' });
  const H = { 'Content-Type': 'application/json', 'Csrf-Token-Open-Li-Bing': csrf };
  const G = { 'Csrf-Token-Open-Li-Bing': csrf };
  const CICD = '/gateway/openlibing-cicd';
  const FW = '/gateway/openlibing-framework';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const results = [];
  const drill = [];
  const CONC = Math.max(1, PARAMS.conc || 1);
  async function pAll(arr, fn) {
    for (let i = 0; i < arr.length; i += CONC) {
      await Promise.all(arr.slice(i, i + CONC).map(fn));
      if (i + CONC < arr.length) await sleep(PARAMS.delay);
    }
  }

  // 阶段 1：逐 log 任务调 flow 接口拉构建子任务列表（buildflow 任务才有 builds；普通流水线返回空列表）
  async function flowOne(it) {
    const qs = 'projectId=' + it.projectId + '&pipelineId=' + it.pipelineId
      + '&pipelineRunId=' + it.rid + '&stepRunIds=' + it.stepIds.join(',');
    const row = { key: it.key, pipeline: it.pipeline, rn: it.rn, rid: it.rid,
                  jobId: it.jobId, jobName: it.jobName,
                  flow: 'failed', reason: '', builds: [] };
    try {
      const res = await fetch(CICD + '/project/pipeline/flow?' + qs, { headers: G });
      const j = await res.json();
      if (j.code === 200 && Array.isArray(j.data)) {
        row.flow = 'ok';
        for (const ent of j.data) {
          for (const b of ent.builds || []) {
            row.builds.push({ buildJobId: b.id, name: b.display_name, buildNo: String(b.build_no),
                              status: b.status, durationMs: b.build_duration,
                              startTime: b.start_time, finishTime: b.finish_time,
                              stepRunId: ent.step_run_id });
          }
        }
      } else {
        row.reason = 'flow code=' + j.code + ' ' + String(j.msg || '').slice(0, 120);
      }
    } catch (e) { row.reason = 'flow: ' + String(e && e.message || e); }
    if (row.flow === 'ok' && row.builds.some(b => b.status === 'FAILURE')) {
      drill.push({ it, failed: row.builds.filter(b => b.status === 'FAILURE') });
    }
    results.push(row);
  }
  await pAll(PARAMS.items, flowOne);

  // 阶段 2：失败子任务逐个创建 build-log/export 导出任务
  // jobId 用 T4 失败步骤 inputs 里 key=jobId 的构建 jobId（buildflow 调度任务专属），
  // buildNo 来自 flow builds[]；flow 的 builds[].id 仅用于 obs-product，此处不可用
  const pending = [];
  async function createOne(d) {
    for (const b of d.failed) {
      const sp = (d.it.steps || []).find(s => s.id === b.stepRunId);
      const jid = (sp && sp.jobIdParam) || d.it.buildJobId || null;
      if (!jid) {
        pending.push({ it: d.it, b, state: 'failed',
                       reason: '缺 buildJobId（T4 step inputs.key=jobId），无法下钻子任务日志' });
        continue;
      }
      const body = { projectId: d.it.projectId, jobId: jid, buildNo: b.buildNo,
                     stepRunId: b.stepRunId, pipelineId: d.it.pipelineId,
                     pipelineRunId: d.it.rid, taskName: d.it.jobName };
      try {
        const res = await fetch(CICD + '/project/pipeline/build-log/export', {
          method: 'POST', headers: H, body: JSON.stringify(body) });
        const j = await res.json();
        if (j.code === 200 && j.data) {
          pending.push({ it: d.it, b, state: 'pending', exportId: String(j.data) });
        } else {
          pending.push({ it: d.it, b, state: 'failed', reason: 'create: ' + JSON.stringify(j).slice(0, 160) });
        }
      } catch (e) {
        pending.push({ it: d.it, b, state: 'failed', reason: 'create: ' + String(e && e.message || e) });
      }
    }
  }
  await pAll(drill, createOne);

  // 阶段 3：轮询导出任务（「导出成功」取 OBS 直链；「生成中」继续；其它判失败）；
  // 每轮对所有未完成项并发查询
  async function checkOne(p) {
    if (p.state !== 'pending') return;
    let j = null;
    try {
      const res = await fetch(FW + '/export/detail?id=' + encodeURIComponent(p.exportId), { headers: G });
      j = await res.json();
    } catch (e) { /* 网络抖动，下一轮再查 */ }
    if (!j) return;
    const d = j.data || {};
    const msg = String(d.message || d.msg || '');
    if (msg.indexOf('导出成功') >= 0) {
      try {
        const res2 = await fetch(FW + '/export/download-url?id=' + encodeURIComponent(p.exportId), { headers: G });
        const j2 = await res2.json();
        if (j2 && j2.data) { p.state = 'ok'; p.url = String(j2.data); }
        else { p.state = 'failed'; p.reason = 'download-url 空: ' + JSON.stringify(j2).slice(0, 120); }
      } catch (e) { p.state = 'failed'; p.reason = 'download-url: ' + String(e && e.message || e); }
    } else if (msg && msg.indexOf('生成中') < 0) {
      p.state = 'failed'; p.reason = 'detail: ' + msg.slice(0, 120);
    }
  }
  const deadline = Date.now() + PARAMS.pollTimeoutMs;
  while (pending.some(p => p.state === 'pending') && Date.now() < deadline) {
    await Promise.all(pending.filter(p => p.state === 'pending').map(checkOne));
    if (pending.some(p => p.state === 'pending')) await sleep(2000);
  }
  for (const p of pending) if (p.state === 'pending') { p.state = 'timeout'; p.reason = '轮询超时'; }

  // 阶段 4：导出失败/超时的子任务走 build/log 分页兜底（sort=DESC 取尾部窗口，标记 truncated）
  async function pageOne(p) {
    if (p.state === 'ok') return;
    const sp = (p.it.steps || []).find(s => s.id === p.b.stepRunId);
    const jid = (sp && sp.jobIdParam) || p.it.buildJobId || null;
    if (!jid) {
      p.paging = 'failed';
      p.reason = (p.reason ? p.reason + '；' : '') + 'build/log: 缺 buildJobId';
      return;
    }
    try {
      const qs = 'projectId=' + p.it.projectId + '&jobId=' + jid + '&buildNo=' + p.b.buildNo
        + '&stepRunId=' + p.b.stepRunId + '&startOffset=0&endOffset=0&sort=DESC'
        + '&pipelineId=' + p.it.pipelineId + '&pipelineRunId=' + p.it.rid;
      const res = await fetch(CICD + '/project/pipeline/build/log?' + qs, { headers: G });
      const j = await res.json();
      const d = j.data || {};
      let tail = '';
      for (const k of ['log', 'logs', 'logContent', 'content', 'lines', 'result']) {
        if (typeof d[k] === 'string' && d[k]) { tail = d[k]; break; }
      }
      p.paging = (j.code === 200 && tail) ? 'ok' : 'failed';
      if (j.code === 200 && !tail) tail = JSON.stringify(d).slice(0, 400000);
      p.logTail = tail;
      p.hasMore = d.has_more === undefined ? null : !!d.has_more;
      if (p.paging !== 'ok') {
        p.reason = (p.reason ? p.reason + '；' : '') + 'build/log: ' + JSON.stringify(j).slice(0, 120);
      }
    } catch (e) {
      p.paging = 'failed';
      p.reason = (p.reason ? p.reason + '；' : '') + 'build/log: ' + String(e && e.message || e);
    }
  }
  await pAll(pending, pageOne);

  const subLogs = pending.map(p => ({
    key: p.it.key, pipeline: p.it.pipeline, rn: p.it.rn, rid: p.it.rid,
    jobId: p.it.jobId, jobName: p.it.jobName,
    subName: p.b.name, buildJobId: p.b.buildJobId, buildNo: p.b.buildNo, stepRunId: p.b.stepRunId,
    export: p.state, exportId: p.exportId || null, url: p.url || null,
    reason: p.reason || null, paging: p.paging || null,
    logTail: p.logTail || null, hasMore: p.hasMore === undefined ? null : p.hasMore,
  }));
  return JSON.stringify({ ok: true, results, subLogs });
})()

})()