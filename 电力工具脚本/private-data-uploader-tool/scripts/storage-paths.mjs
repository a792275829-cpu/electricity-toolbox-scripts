import path from 'node:path';

export function dataPath(value) {
  if (!process.env.TOOLBOX_DATA_ROOT || !process.env.TOOLBOX_CODE_ROOT) return value;
  const relative = path.relative(path.resolve(process.env.TOOLBOX_CODE_ROOT), path.resolve(value));
  if (path.isAbsolute(relative) || relative === '..' || relative.startsWith(`..${path.sep}`)) return value;
  return path.join(process.env.TOOLBOX_DATA_ROOT, relative);
}
