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

export function datesOf(items) {
  return [...new Set(items.map((item) => item.date).filter(Boolean))].sort();
}
