(async () => {
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
