// 用例日志批量下载（T6 返回的 resultDownloadUrl 为 OBS 直链，无需鉴权）。
// 增量模式: 读取已有 manifest.json，已下载成功且文件在盘的 URL 跳过不重复下载；
//          新 URL 文件序号在已有最大序号之后续号（不覆盖已有 case_NNNN.html）；
//          每完成一份立即原子写回 manifest.json —— 中途中断/被杀也不会丢失 url→file 映射。
// 健壮性: 单个 URL 的任何异常（HTTP 非 200 / 超时 / 建流失败 / 回调抛错）都只落一条
//          manifest 记录并继续下一条，不拖垮整批；进程级异常先落 manifest 再以非 0 退出，
//          让调用方（第 4 步）能明确告警。
// 用法: node download_case_logs.js <urlsFile.json> <outDir> <concurrency> <timeoutMs>
// urlsFile 形如 {"urls": ["...", ...]}；产出 case_NNNN.html + manifest.json（url -> {file, status, at}）
const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');

const [, , urlsFile, outDir, concArg, timeoutArg] = process.argv;
const concurrency = parseInt(concArg || '8', 10);
const timeoutMs = parseInt(timeoutArg || '60000', 10);
const urls = JSON.parse(fs.readFileSync(urlsFile, 'utf8')).urls;
fs.mkdirSync(outDir, { recursive: true });

const manifestPath = path.join(outDir, 'manifest.json');
let manifest = {};
let nextIdx = 0;
if (fs.existsSync(manifestPath)) {
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    for (const k of Object.keys(manifest)) {
      const m = /case_(\d+)\.html/.exec(manifest[k].file || '');
      if (m) nextIdx = Math.max(nextIdx, parseInt(m[1], 10) + 1);
    }
  } catch (e) {
    console.log('warn=manifest-unreadable: ' + e.message);
    manifest = {};
  }
} else {
  // manifest 缺失（上次下载被中断）时，盘上已存在的 case_NNNN.html 无法反查 URL，
  // 只能按序号从头覆盖：序号从 0 开始，新下载会原地覆盖这些孤儿文件，不产生冗余。
  let orphans = 0;
  try {
    for (const f of fs.readdirSync(outDir)) if (/^case_\d+\.html$/.test(f)) orphans++;
  } catch (e) { /* 忽略 */ }
  if (orphans > 0) console.log('warn=manifest-missing: ' + orphans + ' 份历史日志无映射，将按序号覆盖');
}

// 立即原子落盘（tmp + rename）：并发下载时由 writing/dirty 串行化，避免半截 JSON
let writing = false, dirty = false;
function persist() {
  dirty = true;
  if (writing) return;
  writing = true;
  while (dirty) {
    dirty = false;
    const tmp = manifestPath + '.tmp';
    try {
      fs.writeFileSync(tmp, JSON.stringify(manifest, null, 1), 'utf-8');
      fs.renameSync(tmp, manifestPath);
    } catch (e) {
      console.log('warn=manifest-write: ' + (e.code || e.message));
    }
  }
  writing = false;
}

function once(url, file) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    try {
      const mod = url.startsWith('https') ? https : http;
      const req = mod.get(url, { timeout: timeoutMs, headers: { 'User-Agent': 'Mozilla/5.0' } }, res => {
        try {
          if (res.statusCode !== 200) { res.resume(); return done('HTTP' + res.statusCode); }
          const ws = fs.createWriteStream(file);
          res.on('error', e => done('read-err:' + (e.code || e.message)));
          ws.on('finish', () => done('ok'));
          ws.on('error', e => done('write-err:' + (e.code || e.message)));
          res.pipe(ws);
        } catch (e) {
          done('cb-err:' + (e.code || e.message));
          try { res.resume(); } catch (e2) { /* 忽略 */ }
        }
      });
      req.on('timeout', () => { req.destroy(); done('timeout'); });
      req.on('error', e => done('net:' + (e.code || e.message)));
    } catch (e) {
      done('throw:' + (e.code || e.message));
    }
  });
}

async function fetchOne(url, file, retries = 2) {
  for (let i = 0; ; i++) {
    const st = await once(url, file);
    if (st === 'ok' || i >= retries) return st;
    await new Promise(r => setTimeout(r, 800 * (i + 1)));
  }
}

const stat = { attempted: 0, ok: 0, reused: 0, failed: 0 };

process.on('uncaughtException', e => {
  console.log('warn=uncaught: ' + (e.code || e.message));
  persist();
  console.log(`downloaded=${stat.ok} reused=${stat.reused} failed=${stat.failed} `
    + `attempted=${stat.attempted}/${urls.length} total=${Object.keys(manifest).length}`);
  process.exit(2);
});

(async () => {
  let idx = 0;
  async function worker() {
    while (idx < urls.length) {
      const url = urls[idx++];
      const prev = manifest[url];
      if (prev && prev.status === 'ok' && fs.existsSync(path.join(outDir, prev.file))) {
        stat.reused++;
        process.stdout.write('s');
        continue;
      }
      const file = 'case_' + String(nextIdx++).padStart(4, '0') + '.html';
      const status = await fetchOne(url, path.join(outDir, file));
      if (status !== 'ok') {
        // 失败（含写到一半断流）不留半截文件，下一次重跑会重新分配序号下载
        try { fs.unlinkSync(path.join(outDir, file)); } catch (e) { /* 忽略 */ }
      }
      manifest[url] = { file, status, at: new Date().toISOString().slice(0, 19) };
      persist();
      stat.attempted++;
      if (status === 'ok') { stat.ok++; process.stdout.write('.'); }
      else { stat.failed++; process.stdout.write('x' + status + ' '); }
    }
  }
  await Promise.all(Array.from({ length: Math.max(1, concurrency) }, worker));
  persist();
  console.log(`\ndownloaded=${stat.ok} reused=${stat.reused} failed=${stat.failed} `
    + `attempted=${stat.attempted}/${urls.length} total=${Object.keys(manifest).length}`);
  process.exit(stat.failed > 0 ? 1 : 0);
})();
