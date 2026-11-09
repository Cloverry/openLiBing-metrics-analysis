(async () => {
const PARAMS = {"runs": [{"key": "HPCKit|kupl", "rn": 299, "st": "11-06 13:30", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300073, "pipelineId": "ddb211b1e4a74f55a0e604fa95ef469e", "pipelineRunId": "83e34fe2e1184610959638c360c41c45"}, {"key": "HPCKit|kuqcd", "rn": 198, "st": "11-06 00:31", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300073, "pipelineId": "eeedf3e4794449969ad6d7f67691d0be", "pipelineRunId": "a36fec8406ed4de9ba4f868015c8f8e0"}, {"key": "HPCKit|kpex", "rn": 139, "st": "11-06 00:41", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300073, "pipelineId": "fec036d3c59f4d27bb506138f273f113", "pipelineRunId": "08890715f1e8442288cc08c1b92cc7b0"}, {"key": "HPCKit|hmpi", "rn": 210, "st": "11-06 01:01", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300073, "pipelineId": "ffe131425ad84d4b8b6c17c4c09476d9", "pipelineRunId": "9c42f33ef29645839271036fce5cfe60"}, {"key": "Triton_Ascend|Ascend", "rn": 104, "st": "11-06 17:00", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300130, "pipelineId": "9a4886d40e544cecb7da63c9597490a6", "pipelineRunId": "9ae1e7dda41c4a78957a5ba09d2e5b3c"}], "delay": 250, "conc": 4};
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