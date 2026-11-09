// CDP 页面脚本驱动：连接本地调试端口 Chrome，在 openLiBing 标签页执行一段 JS（已注入 PARAMS），结果写入文件。
// 用法: node cdp_run.js <codeFile> [--out resultFile] [--tab urlSubstr] [--port 9222] [--timeout ms]
// 退出码: 0 成功 / 1 执行失败 / 2 超时
const fs = require('fs');
const http = require('http');
const args = process.argv.slice(2);
const codeFile = args[0];

function argOf(flag, dflt) {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1] : dflt;
}
const outFile = argOf('--out', null);
const tabSub = argOf('--tab', 'openlibing');
const port = parseInt(argOf('--port', '9222'), 10);
const timeoutMs = parseInt(argOf('--timeout', '300000'), 10);

const sleep = ms => new Promise(r => setTimeout(r, ms));

function httpJSON(method, path) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path, method }, r => {
      let d = ''; r.on('data', c => d += c);
      r.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    });
    req.on('error', reject); req.end();
  });
}

(async () => {
  let finished = false;
  const watchdog = setTimeout(() => {
    if (!finished) {
      console.log(JSON.stringify({ ok: false, error: 'TIMEOUT after ' + timeoutMs + 'ms（浏览器无响应或执行挂起）' }));
      process.exit(2);
    }
  }, timeoutMs);

  let targets;
  try {
    targets = await httpJSON('GET', '/json/list');
  } catch (e) {
    console.log(JSON.stringify({ ok: false, error: '无法连接调试端口 127.0.0.1:' + port + '：' + e.message + '。请先启动带 --remote-debugging-port 的 Chrome' }));
    process.exit(1);
  }
  let page = tabSub
    ? targets.find(t => t.type === 'page' && t.url.includes(tabSub))
    : targets.find(t => t.type === 'page' && t.url.includes('openlibing'));
  if (!page) {
    page = await httpJSON('PUT', '/json/new?https://www.openlibing.com/apps/eCDashboard');
    await sleep(6000);
  }

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();

  ws.addEventListener('message', ev => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id); pending.delete(msg.id);
      if (msg.error) p.rej(new Error('CDP ' + JSON.stringify(msg.error)));
      else p.res(msg.result);
    }
  });
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', rej, { once: true });
  });

  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  await send('Runtime.enable');

  let result, err = null;
  try {
    const code = fs.readFileSync(codeFile, 'utf8');
    const r = await send('Runtime.evaluate', {
      expression: code, awaitPromise: true, returnByValue: true, userGesture: true
    });
    if (r.exceptionDetails) err = 'EXC: ' + JSON.stringify(r.exceptionDetails).slice(0, 1500);
    result = r.result && 'value' in r.result ? r.result.value : undefined;
  } catch (e) {
    err = 'EVAL_ERR: ' + e.message;
  }

  ws.close();
  finished = true;
  clearTimeout(watchdog);

  const summary = { ok: !err, error: err };
  if (outFile && !err) {
    try {
      let text;
      if (typeof result === 'string') text = result;
      else text = JSON.stringify(result === undefined ? null : result, null, 1);
      fs.writeFileSync(outFile, text, 'utf8');
      summary.outFile = outFile;
      summary.outBytes = Buffer.byteLength(text);
    } catch (e) {
      summary.ok = false;
      summary.error = 'WRITE_ERR: ' + e.message;
    }
  }
  console.log(JSON.stringify(summary));
  process.exit(summary.ok ? 0 : 1);
})().catch(e => {
  console.log(JSON.stringify({ ok: false, error: 'FATAL ' + e.message }));
  process.exit(1);
});
