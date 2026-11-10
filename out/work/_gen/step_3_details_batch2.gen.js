(async () => {
const PARAMS = {"runs": [{"key": "HPCKit|kupl", "rn": 306, "st": "11-09 00:01", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300073, "pipelineId": "ddb211b1e4a74f55a0e604fa95ef469e", "pipelineRunId": "26bf4fe5f1344892abe80a21ff2ca906"}, {"key": "HPCKit|kuqcd", "rn": 201, "st": "11-09 00:31", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300073, "pipelineId": "eeedf3e4794449969ad6d7f67691d0be", "pipelineRunId": "e710e7a44622472784b4187e1928c525"}, {"key": "HPCKit|kpex", "rn": 142, "st": "11-09 00:41", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300073, "pipelineId": "fec036d3c59f4d27bb506138f273f113", "pipelineRunId": "62a8b0fdea804558b0321f6b09c49dc2"}, {"key": "HPCKit|hmpi", "rn": 213, "st": "11-09 01:01", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300073, "pipelineId": "ffe131425ad84d4b8b6c17c4c09476d9", "pipelineRunId": "2f71f7f0cb6643dfa23b3d5f8ebb9755"}], "delay": 250, "conc": 4};
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