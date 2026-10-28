import fs from 'node:fs/promises';
import path from 'node:path';

function isAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; }
  catch (error) { return error.code !== 'ESRCH'; }
}

export async function acquireAuthLock(config, timeoutMs = 15 * 60 * 1000, log = () => {}) {
  const target = `${config.authStatePath}.lock`;
  await fs.mkdir(path.dirname(target), { recursive: true });
  const started = Date.now();
  let notified = false;
  while (true) {
    let handle;
    try {
      handle = await fs.open(target, 'wx', 0o600);
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
    }
    if (handle) {
      const identity = await handle.stat();
      const release = async () => {
        await handle.close();
        const current = await fs.stat(target).catch(() => undefined);
        if (current?.ino === identity.ino && current?.dev === identity.dev) await fs.unlink(target).catch(() => {});
      };
      try {
        await handle.writeFile(JSON.stringify({ pid: process.pid, startedAt: new Date().toISOString(), script: 'private-data-uploader' }));
        await handle.sync();
      } catch (error) { await release(); throw error; }
      log(`已获得登录态锁：${target}`);
      return release;
    }
    // Serialize stale-lock reapers. Empty/new locks may still be initializing.
    let reaper;
    try { reaper = await fs.open(`${target}.reap`, 'wx', 0o600); }
    catch (error) {
      if (error.code !== 'EEXIST') throw error;
      const info = await fs.stat(`${target}.reap`).catch(() => undefined);
      const owner = await fs.readFile(`${target}.reap`, 'utf8').then(JSON.parse).catch(() => ({}));
      if (info && Date.now() - info.mtimeMs > 60_000 && !isAlive(Number(owner?.pid))) {
        await fs.unlink(`${target}.reap`).catch(() => {});
      }
    }
    if (reaper) {
      try {
        await reaper.writeFile(JSON.stringify({ pid: process.pid }));
        const info = await fs.stat(target).catch(() => undefined);
        const owner = await fs.readFile(target, 'utf8').then(JSON.parse).catch(() => ({}));
        const pid = Number(owner?.pid);
        if (info && !isAlive(pid) && (pid > 0 || Date.now() - info.mtimeMs > 60_000)) {
          await fs.unlink(target).catch(() => {});
          continue;
        }
      } finally {
        await reaper.close();
        await fs.unlink(`${target}.reap`).catch(() => {});
      }
    }
    if (Date.now() - started >= timeoutMs) throw new Error(`等待登录态锁超时：${target}`);
    if (!notified) { log(`检测到其他脚本正在使用同一登录态，等待其完成：${target}`); notified = true; }
    await new Promise(resolve => setTimeout(resolve, Math.min(200, Math.max(1, timeoutMs - (Date.now() - started)))));
  }
}
