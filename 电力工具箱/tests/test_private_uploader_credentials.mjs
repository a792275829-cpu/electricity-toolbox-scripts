import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { loadSharedAccount, markAccountSession, prepareAccountSession, writePrivateJson } from '../../电力工具脚本/private-data-uploader-tool/scripts/qingneng-account.mjs';

const workspace = await fs.mkdtemp(path.join(os.tmpdir(), 'qingneng-test-'));
try {
  assert.deepEqual(await loadSharedAccount(workspace), {});
  const accountPath = path.join(workspace, '.toolbox/qingneng/config.json');
  await fs.mkdir(path.dirname(accountPath), { recursive: true });
  const original = { username: 'original', password: ' secret ', revision: 'first' };
  await fs.writeFile(accountPath, JSON.stringify(original), { mode: 0o600 });
  assert.deepEqual(await loadSharedAccount(workspace), original);
  let clears = 0;
  const context = { async clearCookies() { clears += 1; } };
  const config = { authStatePath: path.join(workspace, 'tool/auth_state.json'), accountRevision: 'first' };
  assert.equal(await prepareAccountSession(context, config), true);
  await markAccountSession(context, config);
  assert.equal(await prepareAccountSession(context, config), false);
  const edited = { username: 'edited', password: 'new secret', revision: 'second' };
  await fs.writeFile(accountPath, JSON.stringify(edited));
  assert.deepEqual(await loadSharedAccount(workspace), edited);
  config.accountRevision = edited.revision;
  assert.equal(await prepareAccountSession(context, config), true);
  await markAccountSession(context, config);
  assert.equal(await prepareAccountSession(context, config), false);
  assert.equal(clears, 2);
  await fs.writeFile(path.join(workspace, 'tool/.qingneng-session/config.json'), 'broken metadata');
  assert.equal(await prepareAccountSession(context, config), true);
  assert.equal(config.authStatePath, path.join(workspace, 'tool/auth_state.json'));
  await writePrivateJson(config.authStatePath, { cookies: [{ name: 'sample' }] });
  assert.equal((await fs.stat(config.authStatePath)).mode & 0o777, 0o600);
  assert.equal(JSON.parse(await fs.readFile(config.authStatePath, 'utf8')).cookies[0].name, 'sample');
  const blockedTarget = path.join(workspace, 'tool/blocked');
  await fs.mkdir(blockedTarget);
  await assert.rejects(writePrivateJson(blockedTarget, { cookies: [] }));
  assert.equal((await fs.stat(blockedTarget)).isDirectory(), true);
  assert.equal((await fs.readdir(path.dirname(blockedTarget))).some(name => name.endsWith('.tmp')), false);
  await fs.writeFile(accountPath, '{"password":"sensitive-value"');
  await assert.rejects(loadSharedAccount(workspace), error => !error.message.includes('sensitive-value'));
  console.log('Private uploader credentials: load, edit, session revision, isolation OK');
} finally {
  await fs.rm(workspace, { recursive: true, force: true });
}
