import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { loadSharedAccount, markAccountSession, prepareAccountSession, writePrivateJson } from './qingneng-account.mjs';
import { dataPath } from './storage-paths.mjs';
import { acquireAuthLock } from './auth-lock.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..');
const dataDir = dataPath(rootDir);
const defaultConfigPath = path.join(dataDir, 'upload.config.json');

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

function safeErrorMessage(error) {
  return String(error?.message || error || '未知错误').split('\nCall log:')[0].trim();
}

function loginFailure(kind, message) {
  const error = new Error(message);
  error.login_kind = kind;
  return error;
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
  const loginConfigPath = dataPath(path.resolve(config.loginConfigPath ?? path.join(dataDir, 'config.json')));
  const loginConfig = await readJson(loginConfigPath, {});
  const sharedAccount = await loadSharedAccount(path.resolve(rootDir, '../..'));
  const sourceDir = args.source ?? config.sourceDir;
  if (args.mode !== 'login' && !sourceDir) throw new Error('请选择复盘文件夹或配置 sourceDir。');
  return {
    ...config,
    loginConfigPath,
    username: sharedAccount.username ?? loginConfig.username,
    password: sharedAccount.password ?? loginConfig.password,
    accountRevision: sharedAccount.revision ?? '',
    headless: args.headless ?? config.headless ?? loginConfig.headless ?? true,
    sourceDir: path.resolve(sourceDir ?? dataDir),
    authStatePath: dataPath(path.resolve(config.authStatePath ?? path.join(dataDir, 'auth_state.json'))),
    userDataDir: dataPath(path.resolve(config.userDataDir ?? path.join(dataDir, '.browser-profile-uploader'))),
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

async function defaultChromeExecutablePath() {
  if (process.env.TOOLBOX_PORTABLE === '1' && await pathExists(chromium.executablePath())) {
    return chromium.executablePath();
  }
  const candidates = [];
  if (process.platform === 'darwin') {
    candidates.push(
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      path.join(process.env.HOME ?? '', 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
    );
  }

  for (const candidate of candidates) {
    if (candidate && await pathExists(candidate)) return candidate;
  }
  return undefined;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isExcelFile(name) {
  const lower = name.toLowerCase();
  return !name.startsWith('~$') && (lower.endsWith('.xlsx') || lower.endsWith('.xls'));
}

async function collectPlan(sourceDir) {
  const entries = await fs.readdir(sourceDir, { withFileTypes: true });
  const dirs = entries.filter((entry) => entry.isDirectory());
  const entriesByDirectory = new Map();
  const plan = [];

  for (const target of targets) {
    const matchedDirs = dirs.filter((dir) => target.aliases.some((alias) => dir.name.includes(alias)));
    const files = [];

    for (const dir of matchedDirs) {
      const unitDir = path.join(sourceDir, dir.name);
      if (!entriesByDirectory.has(unitDir)) {
        entriesByDirectory.set(unitDir, await fs.readdir(unitDir, { withFileTypes: true }));
      }
      const unitEntries = entriesByDirectory.get(unitDir);
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
  const proxyServer = process.env.HTTPS_PROXY || process.env.HTTP_PROXY || process.env.ALL_PROXY
    || process.env.https_proxy || process.env.http_proxy || process.env.all_proxy;
  if (proxyServer) {
    launchOptions.proxy = { server: proxyServer };
  }
  const chromeExecutablePath = config.chromeExecutablePath && await pathExists(config.chromeExecutablePath)
    ? config.chromeExecutablePath
    : await defaultChromeExecutablePath();
  if (chromeExecutablePath) {
    launchOptions.executablePath = chromeExecutablePath;
  }

  await fs.mkdir(config.userDataDir, { recursive: true });
  const context = await chromium.launchPersistentContext(config.userDataDir, {
    ...launchOptions,
    acceptDownloads: true,
  });
  await prepareAccountSession(context, config);
  if (!context.qingnengAccountChanged && await pathExists(config.authStatePath)) {
    try {
      const state = await readJson(config.authStatePath);
      if (Array.isArray(state?.cookies) && state.cookies.length > 0) {
        await context.addCookies(state.cookies);
      }
    } catch (error) {
      log(`读取私有上传登录态失败，将重新登录：${error.message}`);
    }
  }
  log('浏览器启动完成。');
  return { browser: context.browser(), context };
}

async function saveAuthState(context, config) {
  await writePrivateJson(config.authStatePath, await context.storageState());
  await markAccountSession(context, config);
}

async function isLoggedIn(context, config, quiet = false) {
  if (!quiet) log('检查登录态...');
  const response = await context.request.get(`${config.baseUrl}/usercenter/web/pf/tenant/user/info`, {
    timeout: 8_000,
  }).catch(() => undefined);
  if (!response || response.status() >= 500) {
    throw loginFailure('network', '登录服务暂不可达，请检查内网、VPN 或代理后重试。');
  }
  if (response.status() >= 400) return false;
  const body = await response.json().catch(() => undefined);
  return body?.retCode === 'T200' && Boolean(body?.data?.tenantId);
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
    throw loginFailure('credentials', '请先保存完整的账号密码。');
  }

  log('获取登录公钥...');
  let keyResponse;
  try {
    keyResponse = await context.request.get(`${config.baseUrl}/usercenter/web/pf/login/info/publicKey`, {
      timeout: 15_000,
    });
  } catch (error) {
    throw loginFailure('network', '获取登录公钥失败，请确认内网/VPN/代理可访问登录服务。');
  }
  if (keyResponse.status() >= 400) {
    throw loginFailure('server', `获取登录公钥失败 HTTP ${keyResponse.status()}`);
  }
  const keyBody = await keyResponse.json().catch(() => { throw loginFailure('protocol', '登录服务返回格式异常。'); });
  const publicKey = keyBody?.data;
  if (typeof publicKey !== 'string' || !publicKey) throw loginFailure('protocol', '登录公钥无效。');

  let encryptedPassword;
  try { encryptedPassword = encryptPassword(publicKey, config.password); }
  catch { throw loginFailure('protocol', '登录公钥格式异常。'); }
  log('提交登录请求...');
  const loginResponse = await context.request.post(`${config.baseUrl}/usercenter/web/login`, {
    params: {
      loginMode: 2,
      username: config.username,
      password: encryptedPassword,
    },
    timeout: 20_000,
  }).catch(() => { throw loginFailure('network', '提交登录请求失败，请检查网络后重试。'); });
  const loginBody = await loginResponse.json().catch(() => null);
  if (loginResponse.status() >= 500 || loginResponse.status() === 429) {
    throw loginFailure('server', `登录服务暂不可用（HTTP ${loginResponse.status()}），请稍后重试。`);
  }
  if (!loginBody || typeof loginBody !== 'object') throw loginFailure('protocol', '登录服务返回格式异常。');
  if (loginResponse.status() >= 400 || loginBody.retCode !== 'T200') {
    const verification = /验证|滑块|短信|captcha/i.test(String(loginBody.retMsg || ''));
    throw loginFailure(verification ? 'verification' : 'credentials', verification
      ? '网站需要人工验证，请打开当前工具登录窗口。' : `自动登录被拒绝（HTTP ${loginResponse.status()}），请检查账号、密码或账户状态。`);
  }
}

async function ensureLogin(context, config) {
  if (!context.qingnengAccountChanged && await isLoggedIn(context, config)) {
    log('登录态有效。');
    return;
  }
  log('登录态失效，正在自动登录...');
  try {
    await apiLogin(context, config);
  } catch (error) {
    if (error.login_kind !== 'verification' || config.headless) throw error;
    const page = await context.newPage();
    try {
      await page.goto(`${config.baseUrl}/usercenter/#/login`, { waitUntil: 'domcontentloaded', timeout: 20_000 });
      await page.locator('#username, input[placeholder*="账号"]').first().fill(config.username, { timeout: 5000 }).catch(() => {});
      await page.locator('input[type="password"]').first().fill(config.password, { timeout: 5000 }).catch(() => {});
      await page.locator('button[type="submit"], button:has-text("登录")').first().click({ timeout: 5000 }).catch(() => {});
      log('请在登录窗口完成人工验证（最长等待5分钟）。');
      const deadline = Date.now() + 300_000;
      while (Date.now() < deadline && !page.isClosed()) {
        if (await isLoggedIn(context, config, true)) {
          await saveAuthState(context, config);
          return;
        }
        await sleep(1000);
      }
    } finally { await page.close().catch(() => {}); }
    throw error;
  }
  if (!await isLoggedIn(context, config)) {
    throw loginFailure('session', '自动登录后仍未检测到有效登录态。');
  }
  await saveAuthState(context, config);
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
  const releaseLock = await acquireAuthLock(config, undefined, log);
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
  const releaseLock = await acquireAuthLock(config, undefined, log);
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
  if (error.login_kind && process.env.TOOLBOX_CLI_PROTOCOL === '1') console.log('TOOLBOX_ERROR=' + JSON.stringify({ code: ['network', 'server'].includes(error.login_kind) ? 'network' : 'authentication',
    stage: 'login', login_kind: error.login_kind, exception_type: 'LoginError', message: error.message }));
  const detail = error?.stack || error?.message || String(error);
  console.error(detail.split('\nCall log:')[0]);
  process.exitCode = 1;
});
