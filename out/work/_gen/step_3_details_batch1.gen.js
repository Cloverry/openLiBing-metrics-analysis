(async () => {
const PARAMS = {"runs": [{"key": "MindIE|MindIE-Motor", "rn": 117, "st": "11-06 07:04", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300036, "pipelineId": "d05b26dfc2e5450e8fff23ddf4cd0eb6", "pipelineRunId": "c5a424f5e96646668648f056869831c1"}, {"key": "MindStudio|msot", "rn": 261, "st": "11-06 06:00", "status": "FAILED", "cached": false, "track": "va", "dims": ["va"], "projectId": 300037, "pipelineId": "b67d335347fa4646acd3b96d1c55573e", "pipelineRunId": "e509275186da4ee38affa7c15dfb2ecc"}, {"key": "MindStudio|msot", "rn": 262, "st": "11-06 15:32", "status": "CANCELED", "cached": false, "track": "va", "dims": ["va"], "projectId": 300037, "pipelineId": "b67d335347fa4646acd3b96d1c55573e", "pipelineRunId": "1987bc3c2f5d4cebbe0e0e932bff7a70"}, {"key": "MindStudio|msot", "rn": 263, "st": "11-06 17:50", "status": "CANCELED", "cached": false, "track": "va", "dims": ["va"], "projectId": 300037, "pipelineId": "b67d335347fa4646acd3b96d1c55573e", "pipelineRunId": "ac82d90d09b040ac9feff4bd31c2b8d9"}, {"key": "Ascend-CANN|ops-transformer", "rn": 159, "st": "11-05 21:48", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300057, "pipelineId": "309180d5c2904adf942f5678d9356d99", "pipelineRunId": "b4949a7031d4498bac640dfd47f33f6f"}, {"key": "Ascend-CANN|ops-transformer", "rn": 160, "st": "11-06 02:05", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300057, "pipelineId": "309180d5c2904adf942f5678d9356d99", "pipelineRunId": "ed3c63923a3a4ed49e7ef37d6d193c96"}, {"key": "Ascend-CANN|ops-transformer", "rn": 161, "st": "11-06 11:17", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300057, "pipelineId": "309180d5c2904adf942f5678d9356d99", "pipelineRunId": "92382b4273874bcba766bb1503573dca"}, {"key": "Ascend-CANN|ops-transformer", "rn": 162, "st": "11-06 17:24", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300057, "pipelineId": "309180d5c2904adf942f5678d9356d99", "pipelineRunId": "80dd952d74c54404a0185c758f730b46"}, {"key": "Ascend-CANN|ops-math", "rn": 136, "st": "11-06 02:05", "status": "FAILED", "cached": false, "track": "va", "dims": ["va", "bs"], "projectId": 300057, "pipelineId": "aa3d520fe64f4071a09178d2d603278e", "pipelineRunId": "5a5246b83f144e66b1244ecbbc0ee412"}], "delay": 250, "conc": 4};
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