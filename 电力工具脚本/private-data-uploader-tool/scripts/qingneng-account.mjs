import fs from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { dataPath } from './storage-paths.mjs';

async function readAccountJson(filePath) {
  try {
    return JSON.parse(await fs.readFile(filePath, 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return {};
    throw new Error(`无法读取清能账号配置：${filePath}`);
  }
}

export async function loadSharedAccount(workspace) {
  const account = await readAccountJson(dataPath(path.join(workspace, '.toolbox/qingneng/config.json')));
  if (Object.keys(account).length && (!account.username || !account.password)) {
    throw new Error('清能网站账号配置不完整，请在任务中心下方重新填写。');
  }
  return account;
}

function markerPath(config) {
  return path.join(path.dirname(config.authStatePath), '.qingneng-session/config.json');
}

export async function writePrivateJson(target, value) {
  await fs.mkdir(path.dirname(target), { recursive: true });
  const temporary = path.join(path.dirname(target), `.auth-${randomUUID()}.tmp`);
  try {
    const handle = await fs.open(temporary, 'wx', 0o600);
    try {
      await handle.writeFile(JSON.stringify(value));
      await handle.sync();
    } finally {
      await handle.close();
    }
    await fs.rename(temporary, target);
  } finally {
    await fs.rm(temporary, { force: true });
  }
}

export async function prepareAccountSession(context, config) {
  const marker = await readAccountJson(markerPath(config)).catch(() => ({}));
  context.qingnengAccountChanged = Boolean(config.accountRevision) && marker.revision !== config.accountRevision;
  if (context.qingnengAccountChanged) await context.clearCookies();
  return context.qingnengAccountChanged;
}

export async function markAccountSession(context, config) {
  if (!config.accountRevision) return;
  const target = markerPath(config);
  await writePrivateJson(target, { revision: config.accountRevision });
  context.qingnengAccountChanged = false;
}
