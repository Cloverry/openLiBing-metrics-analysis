(async () => {
const PARAMS = {"runs": [{"key": "openUBMC|openUBMC", "rn": 2449, "st": "11-11 22:51", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 2, "pipelineId": "440c21006cf141e9806d6029c5185b53", "pipelineRunId": "e5f814f030eb4e5691d2f39289555c49"}, {"key": "Ascend-CANN|ops-cv", "rn": 173, "st": "11-11 01:05", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300057, "pipelineId": "66b8958d6e0349ddb01143765c64afa5", "pipelineRunId": "933d961d2a7144d98c124ce939d99002"}], "delay": 250, "conc": 4};
return (async () => {
  const csrf = (document.cookie.split('; ').find(c => c.startsWith('csrf-token-open-li-bing=')) || '').split('=').slice(1).join('=');
  if (!csrf) return JSON.stringify({ ok: false, error: 'NOT_LOGGED_IN' });
  const G = '/gateway/openlibing-cicd';
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const out = [];
  const CONC = Math.max(1, PARAMS.conc || 1);
  async function fetchOne(r) {
    try {
      const res = await fetch(G + '/project/pipeline/pipeline-run/detail?projectId=' + r.projectId + '&pipelineId=' + r.pipelineId + '&pipelineRunId=' + r.pipelineRunId, {
        headers: { 'Csrf-Token-Open-Li-Bing': csrf }
      });
      const j = await res.json();
      out.push({ key: r.key, rn: r.rn, st: r.st, rstatus: r.status, rid: r.pipelineRunId, track: r.track, dims: r.dims, code: j.code, data: j.data });
    } catch (e) { out.push({ key: r.key, rn: r.rn, rid: r.pipelineRunId, err: String(e && e.message || e) }); }
  }
  for (let i = 0; i < PARAMS.runs.length; i += CONC) {
    await Promise.all(PARAMS.runs.slice(i, i + CONC).map(fetchOne));
    if (i + CONC < PARAMS.runs.length) await sleep(PARAMS.delay);
  }
  return JSON.stringify({ ok: true, details: out });
})()

})()