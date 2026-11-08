// 私有数据批量上传的纯逻辑：单元清单、分片、multipart 组装、响应归纳。
// 这里不碰浏览器，便于用 node 直接跑断言测试。
import path from 'node:path';

export const MAX_FILES = 12;
export const MAX_BYTES = 10 * 1024 * 1024;
export const ALLOWED_EXTENSIONS = ['.csv', '.xls', '.xlsx', '.zip', '.pdf'];

// 六个上传单元；energyType 决定导入页版本（NEW_ENERGY 走 PrivateDataManageCE）。
export const TARGETS = [
  {
    key: '海风',
    aliases: ['海风'],
    company: '华能广东汕头海上风电有限责任公司',
    tenantId: 'e4c88ecc8ec18540018eeb6d767241fe',
    pagePath: '/gdfire/PrivateDataManageCE/DataImport',
    energyType: 'NEW_ENERGY',
  },
  {
    key: '鮀莲',
    aliases: ['鮀莲', '金平'],
    company: '华能（汕头金平）新能源有限责任公司',
    tenantId: 'e4c9c04d959356250196b2b15da14f65',
    pagePath: '/gdfire/PrivateDataManageCE/DataImport',
    energyType: 'NEW_ENERGY',
  },
  {
    key: '归湖',
    aliases: ['归湖', '潮州', '潮安'],
    company: '华能（潮州潮安）新能源有限责任公司',
    tenantId: 'e4c17edf9942cca6019974cabef4188d',
    pagePath: '/gdfire/PrivateDataManageCE/DataImport',
    energyType: 'NEW_ENERGY',
  },
  {
    key: '东莞',
    aliases: ['东莞', '谢岗'],
    company: '谢岗电厂',
    tenantId: 'e4e6eb5c80731ac70180faa7f96904eb',
    pagePath: '/gdfire/PrivateDataManage/DataImport',
    energyType: 'FIRE',
  },
  {
    key: '汕头',
    aliases: ['汕头'],
    company: '汕头电厂',
    tenantId: 'e4e6eb5c80731ac70180fab1ba2f0559',
    pagePath: '/gdfire/PrivateDataManage/DataImport',
    energyType: 'FIRE',
  },
  {
    key: '海门',
    aliases: ['海门'],
    company: '海门电厂',
    tenantId: 'e4e6eb5c80731ac70180fab3532d0592',
    pagePath: '/gdfire/PrivateDataManage/DataImport',
    energyType: 'FIRE',
  },
];

// 功率预测数据的来源编码，与网站“数据来源”下拉一致。
export const POWER_SOURCES = {
  北京东润环能: 'BEIJING_DR_HN',
  国能日新: 'GUO_HN_RI_XIN',
  南瑞: 'NAN_RUI',
  北京华软恒信: 'BEIJING_HR_HX',
  其他: 'OTHER',
  电力交易平台: 'ELECTRIC_TRADE_PLATFORM',
  调度OPS平台: 'DISPATCH_OPS_PLATFORM',
};

// 列表接口里 personalDataSource 是数字 id，日志按中文名显示。
export const SOURCE_NAMES = {
  0: '其他',
  1: '电力交易平台',
  2: '调度OPS平台',
  100: '北京东润环能',
  101: '国能日新',
  102: '南瑞',
  103: '北京华软恒信',
  BEIJING_DR_HN: '北京东润环能',
  GUO_HN_RI_XIN: '国能日新',
  NAN_RUI: '南瑞',
  BEIJING_HR_HX: '北京华软恒信',
  ELECTRIC_TRADE_PLATFORM: '电力交易平台',
  DISPATCH_OPS_PLATFORM: '调度OPS平台',
  OTHER: '其他',
};

export function sourceName(value) {
  const key = String(value ?? '').trim();
  if (!key) return '';
  return SOURCE_NAMES[key] ?? key;
}

/**
 * personalDataTypeSourceMap：新能源单元的功率预测数据必须带来源，否则该行无法定位。
 * 申报功率预测数据只有“调度OPS平台”一个选项，直接固定；原始功率预测数据由用户选。
 */
export function buildSourceMap({ source } = {}, energyType) {
  const map = {};
  if (energyType === 'NEW_ENERGY') map.DECLARE_POWER_PREDICT = 'DISPATCH_OPS_PLATFORM';
  if (source) {
    const code = POWER_SOURCES[source];
    if (!code) throw new Error(`未知数据来源：${source}（可选：${Object.keys(POWER_SOURCES).join('、')}）`);
    map.RAW_POWER_PREDICT = code;
  }
  return map;
}

export function resolveTarget(unit) {
  const value = String(unit ?? '').trim();
  if (!value) throw new Error('请选择上传单元。');
  const target = TARGETS.find((item) => item.key === value)
    ?? TARGETS.find((item) => item.aliases.includes(value));
  if (!target) throw new Error(`未知上传单元：${value}（可选：${TARGETS.map((item) => item.key).join('、')}）`);
  return target;
}

export function isAllowedFile(name) {
  const lower = String(name).toLowerCase();
  return !String(name).startsWith('~$') && ALLOWED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/**
 * 网站同款限制：单次最多 12 个文件、合计不超过 10MB。
 * 超出时按顺序切成多批，单批内的文件不会被拆开。
 */
export function planBatches(files, { maxFiles = MAX_FILES, maxBytes = MAX_BYTES } = {}) {
  if (!Array.isArray(files) || files.length === 0) return [];
  const batches = [];
  let current = { files: [], bytes: 0 };
  for (const file of files) {
    const size = Number(file.size ?? 0);
    if (size > maxBytes) {
      throw new Error(`单个文件超过 10MB，网站不接受：${path.basename(file.path)}`);
    }
    const wouldExceed = current.files.length >= maxFiles || current.bytes + size > maxBytes;
    if (wouldExceed && current.files.length > 0) {
      batches.push(current);
      current = { files: [], bytes: 0 };
    }
    current.files.push(file);
    current.bytes += size;
  }
  if (current.files.length > 0) batches.push(current);
  return batches;
}

function mimeType(name) {
  const lower = String(name).toLowerCase();
  if (lower.endsWith('.xlsx')) return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  if (lower.endsWith('.xls')) return 'application/vnd.ms-excel';
  if (lower.endsWith('.csv')) return 'text/csv';
  if (lower.endsWith('.pdf')) return 'application/pdf';
  if (lower.endsWith('.zip')) return 'application/zip';
  return 'application/octet-stream';
}

/**
 * 手写 multipart：files 为同名重复字段，Playwright 的 multipart 选项不支持重复键。
 * 文件名按浏览器行为直接写 UTF-8 字节。
 */
export function buildMultipart(files, fields = {}) {
  const boundary = `----toolbox${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
  const parts = [];
  for (const [name, value] of Object.entries(fields)) {
    if (value === undefined || value === null) continue;
    parts.push(Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`,
      'utf8',
    ));
  }
  for (const file of files) {
    const filename = path.basename(file.path).replace(/"/g, '');
    parts.push(Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="files"; filename="${filename}"\r\n`
      + `Content-Type: ${mimeType(filename)}\r\n\r\n`,
      'utf8',
    ));
    parts.push(file.buffer, Buffer.from('\r\n'));
  }
  parts.push(Buffer.from(`--${boundary}--\r\n`, 'utf8'));
  return {
    body: Buffer.concat(parts),
    contentType: `multipart/form-data; boundary=${boundary}`,
  };
}

/** 接口返回逐文件结果数组：detail 非空即该文件失败。 */
export function summarizeImport(payload) {
  const items = Array.isArray(payload) ? payload : [];
  const succeeded = [];
  const failed = [];
  for (const item of items) {
    const detail = item?.detail;
    if (detail) failed.push({ date: item?.date ?? '', dataType: item?.dataType ?? '', detail: String(detail) });
    else succeeded.push({ date: item?.date ?? '', dataType: item?.dataType ?? '' });
  }
  return { items, succeeded, failed };
}

/** 上传后的状态快照只汇总已上传条数，未上传的其它数据项不属于本次上传结果。 */
export function summarizeStatus(rows) {
  const list = Array.isArray(rows) ? rows : [];
  return {
    total: list.length,
    uploaded: list.filter((row) => String(row?.importStatus) === '1').length,
  };
}

/** 接口没返回数据日期时，用 --date 或文件名里的 YYYYMMDD 兜底。 */
export function inferDatesFromNames(files, date) {
  if (date) return [date];
  const dates = [];
  for (const file of files ?? []) {
    const match = String(file.path ?? '').match(/(20\d{2})(\d{2})(\d{2})/);
    if (match) dates.push(`${match[1]}-${match[2]}-${match[3]}`);
  }
  return [...new Set(dates)].sort();
}

export function datesOf(items) {
  return [...new Set(items.map((item) => item.date).filter(Boolean))].sort();
}
