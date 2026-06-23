import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..');
const defaultConfigPath = path.join(rootDir, 'upload.config.json');

const targets = [
  {
    key: '海风',
    aliases: ['海风'],
    company: '华能广东汕头海上风电有限责任公司',
    tenantId: 'e4c88ecc8ec18540018eeb6d767241fe',
    pagePath: '/gdfire/PrivateDataManageCE/DataImport',
  },
  {
    key: '鮀莲',
    aliases: ['鮀莲', '金平'],
    company: '华能（汕头金平）新能源有限责任公司',
    tenantId: 'e4c9c04d959356250196b2b15da14f65',
    pagePath: '/gdfire/PrivateDataManageCE/DataImport',
  },
  {
    key: '归湖',
    aliases: ['归湖', '潮州', '潮安'],
    company: '华能（潮州潮安）新能源有限责任公司',
    tenantId: 'e4c17edf9942cca6019974cabef4188d',
    pagePath: '/gdfire/PrivateDataManageCE/DataImport',
  },
  {
    key: '东莞',
    aliases: ['东莞', '谢岗'],
    company: '谢岗电厂',
    tenantId: 'e4e6eb5c80731ac70180faa7f96904eb',
    pagePath: '/gdfire/PrivateDataManage/DataImport',
  },
  {
    key: '汕头',
    aliases: ['汕头'],
    company: '汕头电厂',
    tenantId: 'e4e6eb5c80731ac70180fab1ba2f0559',
    pagePath: '/gdfire/PrivateDataManage/DataImport',
  },
  {
    key: '海门',
    aliases: ['海门'],
    company: '海门电厂',
    tenantId: 'e4e6eb5c80731ac70180fab3532d0592',
    pagePath: '/gdfire/PrivateDataManage/DataImport',
  },
];

function log(message = '') {
  console.log(message);
}

function parseArgs(argv) {
  const args = {
    mode: 'plan',
    source: undefined,
    configPath: defaultConfigPath,
    headless: undefined,
  };

  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--plan') args.mode = 'plan';
    else if (arg === '--execute') args.mode = 'execute';
    else if (arg === '--login') args.mode = 'login';
    else if (arg === '--headed') args.headless = false;
    else if (arg === '--headless') args.headless = true;
    else if (arg === '--source') args.source = argv[++i];
    else if (arg === '--config') args.configPath = argv[++i];
    else throw new Error(`未知参数：${arg}`);
  }

  return args;
}

async function readJson(filePath, fallback = undefined) {
  try {
    return JSON.parse(await fs.readFile(filePath, 'utf8'));
  } catch (error) {
    if (fallback !== undefined && error.code === 'ENOENT') return fallback;
    throw error;
  }
}

async function loadConfig(args) {
  const config = await readJson(args.configPath);
  const loginConfigPath = path.resolve(config.loginConfigPath ?? path.join(rootDir, 'config.json'));
  const loginConfig = await readJson(loginConfigPath, {});
  return {
    ...config,
    loginConfigPath,
    username: loginConfig.username,
    password: loginConfig.password,
    headless: args.headless ?? config.headless ?? loginConfig.headless ?? true,
    sourceDir: path.resolve(args.source ?? config.sourceDir),
    authStatePath: path.resolve(config.authStatePath ?? path.join(rootDir, 'auth_state.json')),
    userDataDir: path.resolve(config.userDataDir ?? path.join(rootDir, '.browser-profile-uploader')),
    chromeExecutablePath: config.chromeExecutablePath,
    baseUrl: config.baseUrl ?? 'https://xhxt.chng.com.cn',
  };
}

async function pathExists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isProcessAlive(pid) {
  if (!pid || !Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function acquireAuthLock(config, timeoutMs = 15 * 60 * 1000) {
  const lockPath = `${config.authStatePath}.lock`;
  const startedAt = Date.now();
  let notified = false;

  while (true) {
    try {
      const handle = await fs.open(lockPath, 'wx');
      await handle.writeFile(JSON.stringify({
        pid: process.pid,
        startedAt: new Date().toISOString(),
        script: 'private-data-uploader',
      }, null, 2), 'utf8');
      log(`已获得登录态锁：${lockPath}`);
      return async () => {
        await handle.close().catch(() => {});
        await fs.unlink(lockPath).catch(() => {});
      };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      let owner;
      try {
        owner = JSON.parse(await fs.readFile(lockPath, 'utf8'));
      } catch {
        owner = {};
      }
      const lockAgeMs = Date.now() - (Date.parse(owner.startedAt ?? '') || 0);
      if (lockAgeMs > 6 * 60 * 60 * 1000 || (owner.pid && !isProcessAlive(Number(owner.pid)))) {
        await fs.unlink(lockPath).catch(() => {});
        continue;
      }
      if (Date.now() - startedAt > timeoutMs) {
        throw new Error(`等待登录态锁超时，请确认没有其他上传/抓取脚本仍在运行：${lockPath}`);
      }
      if (!notified) {
        log(`检测到其他脚本正在使用同一登录态，等待其完成：${lockPath}`);
        notified = true;
      }
      await sleep(2000);
    }
  }
}

function isExcelFile(name) {
  const lower = name.toLowerCase();
  return !name.startsWith('~$') && (lower.endsWith('.xlsx') || lower.endsWith('.xls'));
}

async function collectPlan(sourceDir) {
  const entries = await fs.readdir(sourceDir, { withFileTypes: true });
  const dirs = entries.filter((entry) => entry.isDirectory());
  const plan = [];

  for (const target of targets) {
    const matchedDirs = dirs.filter((dir) => target.aliases.some((alias) => dir.name.includes(alias)));
    const files = [];

    for (const dir of matchedDirs) {
      const unitDir = path.join(sourceDir, dir.name);
      const unitEntries = await fs.readdir(unitDir, { withFileTypes: true });
      for (const entry of unitEntries) {
        if (entry.isFile() && isExcelFile(entry.name)) {
          files.push(path.join(unitDir, entry.name));
        }
      }
    }

    files.sort((a, b) => a.localeCompare(b, 'zh-CN'));
    if (files.length > 0) {
      plan.push({ target, files, duplicates: findDuplicateKinds(files) });
    }
  }

  return plan;
}

function fileKind(filePath) {
  const name = path.basename(filePath);
  if (name.includes('日前')) return '日前';
  if (name.includes('实时')) return '实时';
  if (name.includes('日清')) return '日清';
  if (name.includes('月度')) return '月度';
  return undefined;
}

function findDuplicateKinds(files) {
  const counts = new Map();
  for (const file of files) {
    const kind = fileKind(file);
    if (kind) counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }
  return [...counts.entries()].filter(([, count]) => count > 1).map(([kind]) => kind);
}

function printPlan(plan) {
  if (plan.length === 0) {
    log('未找到可上传文件。请确认选择的文件夹下有海风、鮀莲、归湖、东莞、汕头、海门等子文件夹。');
    return;
  }

  log('上传计划:');
  for (const item of plan) {
    log(`- ${item.target.key} -> ${item.target.company}`);
    for (const file of item.files) log(`  ${file}`);
    if (item.duplicates.length > 0) {
      log(`  警告: 存在同类重复文件，后上传的可能覆盖前一次: ${item.duplicates.join('、')}`);
    }
  }
}

async function launchContext(config) {
  log(`启动浏览器：${config.headless ? '后台模式' : '可见模式'}`);
  const launchOptions = {
    headless: config.headless,
    viewport: { width: 1600, height: 950 },
  };
  if (config.chromeExecutablePath && await pathExists(config.chromeExecutablePath)) {
    launchOptions.executablePath = config.chromeExecutablePath;
  }

  const storageState = await pathExists(config.authStatePath) ? config.authStatePath : undefined;
  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({
    storageState,
    acceptDownloads: true,
    viewport: launchOptions.viewport,
  });
  log('浏览器启动完成。');
  return { browser, context };
}

async function saveAuthState(context, config) {
  await fs.mkdir(path.dirname(config.authStatePath), { recursive: true });
  await context.storageState({ path: config.authStatePath });
}

async function isLoggedIn(context, config) {
  log('检查登录态...');
  const response = await context.request.get(`${config.baseUrl}/usercenter/web/pf/tenant/user/info`, {
    timeout: 20_000,
  }).catch(() => undefined);
  if (!response || response.status() >= 400) return false;
  const body = await response.json().catch(() => undefined);
  return body?.retCode === 'T200';
}

function encryptPassword(publicKeyBase64, password) {
  const publicKey = crypto.createPublicKey({
    key: Buffer.from(publicKeyBase64, 'base64'),
    format: 'der',
    type: 'spki',
  });
  return crypto.publicEncrypt({
    key: publicKey,
    padding: crypto.constants.RSA_PKCS1_PADDING,
  }, Buffer.from(password, 'utf8')).toString('base64');
}

async function apiLogin(context, config) {
  if (!config.username || !config.password) {
    throw new Error('config.json 缺少 username/password，无法自动登录。');
  }

  log('获取登录公钥...');
  const keyResponse = await context.request.get(`${config.baseUrl}/usercenter/web/pf/login/info/publicKey`, {
    timeout: 20_000,
  });
  if (keyResponse.status() >= 400) {
    throw new Error(`获取登录公钥失败 HTTP ${keyResponse.status()}`);
  }
  const keyBody = await keyResponse.json();
  const publicKey = keyBody?.data;
  if (!publicKey) throw new Error('登录公钥为空。');

  const encryptedPassword = encryptPassword(publicKey, config.password);
  log('提交登录请求...');
  const loginResponse = await context.request.post(`${config.baseUrl}/usercenter/web/login`, {
    params: {
      loginMode: 2,
      username: config.username,
      password: encryptedPassword,
    },
    timeout: 20_000,
  });
  const loginBody = await loginResponse.json().catch(async () => ({ text: await loginResponse.text() }));
  if (loginResponse.status() >= 400 || (loginBody.retCode && loginBody.retCode !== 'T200')) {
    throw new Error(`自动登录失败 HTTP ${loginResponse.status()}: ${JSON.stringify(loginBody).slice(0, 300)}`);
  }

  await saveAuthState(context, config);
}

async function ensureLogin(context, config) {
  if (await isLoggedIn(context, config)) {
    log('登录态有效。');
    return;
  }
  log('登录态失效，正在自动登录...');
  await apiLogin(context, config);
  if (!await isLoggedIn(context, config)) {
    throw new Error('自动登录后仍未检测到有效登录态。');
  }
  log('自动登录成功。');
}

function isLoginPage(page) {
  return page.url().includes('/usercenter/#/login');
}

async function switchTenant(page, config, target) {
  let lastError;

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    log(`切换单位：${target.key} -> ${target.company}`);
    try {
      const switchResponse = await page.goto(`${config.baseUrl}/usercenter/web/switchTenant?tenantId=${target.tenantId}`, {
        waitUntil: 'domcontentloaded',
        timeout: 30_000,
      });
      log(`切换接口返回：HTTP ${switchResponse?.status() ?? 'unknown'}`);
      const importResponse = await page.goto(`${config.baseUrl}${target.pagePath}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60_000,
      });
      log(`导入页面返回：HTTP ${importResponse?.status() ?? 'unknown'}，当前URL：${page.url()}`);
      await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
    } catch (error) {
      lastError = error;
      if (attempt < 3) {
        log(`打开导入页面失败，正在重新检查登录态后重试（${attempt}/3）：${error.message}`);
        await ensureLogin(page.context(), config);
        await sleep(3000);
        continue;
      }
      break;
    }

    if (!isLoginPage(page)) {
      log('等待上传文件控件...');
      try {
        await page.locator('input[name="files"][type="file"], input[type="file"]').first().waitFor({
          state: 'attached',
          timeout: 45_000,
        });
        log('上传入口已就绪。');
        return;
      } catch (error) {
        lastError = error;
        if (attempt < 3) {
          log(`上传入口暂未就绪，重新检查登录态后重试（${attempt}/3）：${error.message}`);
          await ensureLogin(page.context(), config);
          await sleep(3000);
          continue;
        }
      }
    }

    if (attempt < 3 && isLoginPage(page)) {
      log('进入上传页面时发现登录态失效，正在自动重新登录并重试...');
      await ensureLogin(page.context(), config);
      continue;
    }

    break;
  }

  const debugDir = path.join(rootDir, 'logs', 'debug');
  await fs.mkdir(debugDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const screenshotPath = path.join(debugDir, `${target.key}-${stamp}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => {});
  const title = await page.title().catch(() => '');
  const bodyText = await page.locator('body').innerText({ timeout: 3000 }).catch(() => '');
  throw new Error([
    `等待上传文件控件超时：${target.key}`,
    `当前URL：${page.url()}`,
    `页面标题：${title}`,
    `截图：${screenshotPath}`,
    `页面文本片段：${bodyText.slice(0, 500)}`,
    `原始错误：${lastError?.message ?? '进入上传页面后被重定向到登录页'}`,
  ].join('\n'));
}

async function uploadFile(page, filePath, current, total) {
  log(`[${current}/${total}] 上传：${filePath}`);
  log('等待上传接口返回...');
  const startedAt = Date.now();
  const heartbeat = setInterval(() => {
    const elapsedSeconds = Math.round((Date.now() - startedAt) / 1000);
    log(`[${current}/${total}] 服务器仍在处理，已等待 ${elapsedSeconds} 秒：${path.basename(filePath)}`);
  }, 15_000);

  const responsePromise = page.waitForResponse((response) => (
    response.url().includes('/gdfire/api/data/personal/import') && response.request().method() === 'POST'
  ), { timeout: 120_000 });

  try {
    await page.locator('input[name="files"][type="file"], input[type="file"]').first().setInputFiles(filePath);
    const response = await responsePromise;
    const body = await response.json().catch(async () => ({ text: await response.text() }));
    if (response.status() >= 400 || body.retCode !== 'T200') {
      throw new Error(`上传失败 HTTP ${response.status()}: ${JSON.stringify(body).slice(0, 500)}`);
    }
  } finally {
    clearInterval(heartbeat);
  }
  log(`[${current}/${total}] 成功：${path.basename(filePath)}`);
}

async function executeUpload(plan, config) {
  const releaseLock = await acquireAuthLock(config);
  let browser;
  let context;
  try {
    ({ browser, context } = await launchContext(config));
    await ensureLogin(context, config);
    const page = await context.newPage();
    const totalFiles = plan.reduce((total, item) => total + item.files.length, 0);
    let currentFile = 0;
    log(`本次共需上传 ${totalFiles} 个文件。`);
    for (const item of plan) {
      await switchTenant(page, config, item.target);
      for (const file of item.files) {
        currentFile += 1;
        await uploadFile(page, file, currentFile, totalFiles);
      }
    }
    await saveAuthState(context, config);
  } finally {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    await releaseLock();
  }
}

async function loginOnly(config) {
  const releaseLock = await acquireAuthLock(config);
  let browser;
  let context;
  try {
    ({ browser, context } = await launchContext(config));
    await ensureLogin(context, config);
    await saveAuthState(context, config);
    log('登录态已保存。');
  } finally {
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    await releaseLock();
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const config = await loadConfig(args);

  if (args.mode === 'login') {
    await loginOnly(config);
    return;
  }

  const plan = await collectPlan(config.sourceDir);
  printPlan(plan);

  if (args.mode === 'plan') {
    log('');
    log('当前为预览模式。确认无误后运行 --execute。');
    return;
  }

  if (plan.length === 0) {
    throw new Error('没有可上传文件，已停止。');
  }
  await executeUpload(plan, config);
  log('全部上传完成。');
}

main().catch((error) => {
  console.error(error?.stack || error?.message || String(error));
  process.exitCode = 1;
});
