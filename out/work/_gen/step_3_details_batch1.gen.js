(async () => {
const PARAMS = {"runs": [{"key": "Ascend-CANN|ops-transformer", "rn": 167, "st": "11-09 20:49", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300057, "pipelineId": "309180d5c2904adf942f5678d9356d99", "pipelineRunId": "1cf0a09871cf488abd9c7aaaf7a4319a"}, {"key": "Ascend-CANN|shmem", "rn": 143, "st": "11-10 03:00", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300057, "pipelineId": "93831fdaa3234c8a9dd0b8a0c220f939", "pipelineRunId": "0ea45a231d7547cf87aa39e2d3571b41"}, {"key": "Ascend-CANN|shmem", "rn": 144, "st": "11-10 09:51", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300057, "pipelineId": "93831fdaa3234c8a9dd0b8a0c220f939", "pipelineRunId": "ba1a1af3745e4d34880b78916b209766"}, {"key": "Ascend-CANN|shmem", "rn": 145, "st": "11-10 17:00", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300057, "pipelineId": "93831fdaa3234c8a9dd0b8a0c220f939", "pipelineRunId": "96a91e4f3750433c9c0c23a1b5877f54"}, {"key": "boostkit|Bigdata", "rn": 11, "st": "10-08 10:04", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300059, "pipelineId": "a74a64d931f34c98bc726e5ea599f8a3", "pipelineRunId": "36146a33b2ce44e08d0b6f12bc1c1e23"}, {"key": "HPCKit|kuqcd", "rn": 202, "st": "11-10 00:31", "status": "FAILED", "cached": false, "track": "bs", "dims": ["bs"], "projectId": 300073, "pipelineId": "eeedf3e4794449969ad6d7f67691d0be", "pipelineRunId": "3b55561e204d4486adc37b9f84403946"}], "delay": 250, "conc": 4};
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