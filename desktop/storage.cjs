'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const DATA_NAME = 'InfiniteCanvasData';
function canonical(value) { return path.resolve(value).replace(/[\\/]+$/, '').toLowerCase(); }
function inside(parent, child) { const rel = path.relative(parent, child); return !rel || (!rel.startsWith('..' + path.sep) && rel !== '..' && !path.isAbsolute(rel)); }
function readIni(file) {
  if (!fs.existsSync(file)) return {};
  const bytes = fs.readFileSync(file);
  const text = bytes[0] === 0xff && bytes[1] === 0xfe ? bytes.subarray(2).toString('utf16le') : bytes.toString('utf8');
  return Object.fromEntries(text.split(/\r?\n/).filter(line => /^[a-zA-Z]+=/.test(line)).map(line => { const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)]; }));
}
function writeIni(file, values) {
  const lines = Object.entries(values).filter(([, value]) => value !== undefined && value !== '').map(([key, value]) => {
    if (/[\r\n\0]/.test(String(value))) throw Error('存储路径无效。');
    return `${key}=${value}`;
  });
  fs.mkdirSync(path.dirname(file), { recursive: true });
  // Windows NSIS ReadINIStr understands Unicode INI with a UTF-16LE BOM.
  fs.writeFileSync(file + '.tmp', Buffer.concat([Buffer.from([0xff,0xfe]), Buffer.from('[InfiniteCanvas]\r\n' + lines.join('\r\n') + '\r\n', 'utf16le')]));
  fs.renameSync(file + '.tmp', file);
}
function noLinks(root) {
  // Resolve ancestors too: a junction must not turn an owned path into another folder.
  let current = path.resolve(root);
  while (!fs.existsSync(current)) { const parent = path.dirname(current); if (parent === current) break; current = parent; }
  if (fs.existsSync(current) && (fs.lstatSync(current).isSymbolicLink() || canonical(fs.realpathSync(current)) !== canonical(current))) throw Error('数据路径不能使用链接或目录联接。');
}
function filesIn(root) {
  noLinks(root);
  const files = [];
  function walk(dir, rel = '') {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const name = path.join(rel, entry.name), full = path.join(root, name);
      const stat = fs.lstatSync(full);
      if (stat.isSymbolicLink()) throw Error('数据目录包含链接，已停止操作，原数据保持不变。');
      if (stat.isDirectory()) walk(full, name);
      else if (stat.isFile()) files.push({ name, size: stat.size });
      else throw Error('数据目录中存在不支持的文件。');
    }
  }
  if (fs.existsSync(root)) walk(root);
  return files;
}
function digest(file) {
  const hash = crypto.createHash('sha256'), buffer = Buffer.allocUnsafe(1024 * 1024), fd = fs.openSync(file, 'r');
  try { let bytes; while ((bytes = fs.readSync(fd, buffer, 0, buffer.length, null))) hash.update(buffer.subarray(0, bytes)); }
  finally { fs.closeSync(fd); }
  return hash.digest('hex');
}
class Storage {
  constructor({ installDir, localAppData, isolatedRoot }) {
    this.installDir = path.resolve(installDir);
    this.id = crypto.createHash('sha256').update(canonical(this.installDir)).digest('hex').slice(0, 24);
    this.defaultRoot = path.join(this.installDir, DATA_NAME);
    this.legacyRoot = path.join(localAppData, 'InfiniteCanvas');
    this.controlRoot = isolatedRoot || path.join(localAppData, 'InfiniteCanvasInstallations', this.id);
    this.configFile = path.join(this.controlRoot, 'storage.ini');
    this.isolated = !!isolatedRoot;
    this.config = readIni(this.configFile);
    this.root = this.isolated ? path.resolve(isolatedRoot) : this.config.path || this.defaultRoot;
  }
  owned(root) { const info = readIni(path.join(root, 'owner.ini')); return info.owner === this.id && canonical(root) !== canonical(this.installDir) && path.basename(root).toLowerCase() === DATA_NAME.toLowerCase(); }
  writable(root) {
    noLinks(root);
    try { fs.mkdirSync(root, { recursive: true }); } catch { throw Error('该位置不可写，请选择有写入权限的数据目录。'); }
    const probe = path.join(root, `.write-check-${crypto.randomUUID()}`);
    try { fs.writeFileSync(probe, '1', { flag: 'wx' }); } catch { throw Error('该位置不可写，请选择有写入权限的数据目录。'); }
    finally { if (fs.existsSync(probe)) fs.unlinkSync(probe); }
  }
  mark(root, extra = {}) { writeIni(path.join(root, 'owner.ini'), { owner: this.id, installation: this.installDir, ...extra }); }
  commit(values) { writeIni(this.configFile, values); this.config = values; this.root = values.path; }
  backups() { try { return [...new Set([...JSON.parse(this.config.backups || '[]'), this.config.lastBackup].filter(value=>typeof value === 'string' && value))]; } catch { return this.config.lastBackup ? [this.config.lastBackup] : []; } }
  completed(source, target, archived = '') {
    const backups = this.backups().map(item=>canonical(item) === canonical(target) ? archived : item).filter(Boolean);
    backups.push(source);
    return { path: target, lastBackup: source, backups: JSON.stringify([...new Set(backups)]) };
  }
  bootstrap() {
    if (this.isolated) { this.writable(this.root); return; }
    if (!this.config.path && !this.owned(this.defaultRoot) && fs.existsSync(path.join(this.legacyRoot, 'userdata'))) {
      this.commit({ path: this.legacyRoot, legacy: '1' });
    }
    if (this.config.pending) {
      try { this.finishMigration(); }
      catch (error) { this.migrationError = error.message; const previous = { ...this.config }; delete previous.pending; delete previous.migration; this.commit(previous); }
    }
    if (this.config.path && !fs.existsSync(this.root)) throw Error('原数据目录不可用，请检查磁盘连接。不会自动创建空资料替代原数据。');
    this.writable(this.root);
    if (!this.config.path) {
      if (!this.owned(this.root) && fs.readdirSync(this.root).length) throw Error('默认数据文件夹已有未识别资料，请选择其他空目录。');
      this.mark(this.root); this.commit({ path: this.root });
    }
    else if (!this.config.legacy && !this.owned(this.root)) throw Error('数据目录归属不匹配，已停止加载。');
  }
  info() { return { path: this.root, defaultPath: this.defaultRoot, isDefault: canonical(this.root) === canonical(this.defaultRoot), legacy: this.config.legacy === '1', lastBackup: this.config.lastBackup || '', backups: this.backups(), migrationError: this.migrationError || '' }; }
  target(parent) { return path.basename(parent).toLowerCase() === DATA_NAME.toLowerCase() ? path.resolve(parent) : path.join(parent, DATA_NAME); }
  validateTarget(target) {
    if (path.basename(target).toLowerCase() !== DATA_NAME.toLowerCase() || inside(this.root, target) || inside(target, this.root) || inside(this.controlRoot, target) || inside(target, this.controlRoot)) throw Error('新目录不能与当前资料或运行目录重叠。');
    if (['resources', 'locales'].some(name => inside(path.join(this.installDir, name), target))) throw Error('数据目录不能放入软件的程序资源文件夹。');
    noLinks(target);
    if (fs.existsSync(target) && fs.readdirSync(target).length && !(this.owned(target) && this.backups().some(item=>canonical(item) === canonical(target)))) throw Error('目标文件夹已有资料，请选择空位置。原资料不会被覆盖或合并。');
    this.writable(path.dirname(target));
  }
  schedule(target) {
    this.validateTarget(target);
    this.commit({ ...this.config, path: this.root, pending: target, migration: crypto.randomUUID() });
  }
  finishMigration() {
    const source = this.config.path, target = this.config.pending, migration = this.config.migration;
    if (this.owned(target) && readIni(path.join(target, 'owner.ini')).migration === migration) {
      this.commit(this.completed(source, target, readIni(path.join(target, 'owner.ini')).archived)); return;
    }
    this.validateTarget(target);
    const files = filesIn(source).filter(file => file.name !== 'owner.ini' && !/^runtime([\\/]|$)/i.test(file.name) && !/^browser-profile[\\/](Cache|Code Cache|GPUCache)([\\/]|$)/i.test(file.name) && !/^browser-profile[\\/]Singleton/i.test(file.name));
    const required = files.reduce((sum, item) => sum + item.size, 0);
    const disk = fs.statfsSync(path.dirname(target));
    if (disk.bavail * disk.bsize < required + 32 * 1024 * 1024) throw Error('目标磁盘剩余空间不足，原资料保持不变。');
    const staging = target + '.migrating-' + migration;
    if (fs.existsSync(staging)) throw Error('上次迁移的临时文件仍在，请先检查该位置。');
    fs.mkdirSync(staging);
    // Keep failed staging for diagnosis; never delete any source or unknown files.
    for (const file of files) {
      const from = path.join(source, file.name), to = path.join(staging, file.name);
      fs.mkdirSync(path.dirname(to), { recursive: true }); fs.copyFileSync(from, to, fs.constants.COPYFILE_EXCL);
      if (digest(from) !== digest(to)) throw Error('迁移校验失败，继续保留原资料。');
    }
    const archived = fs.existsSync(target) && fs.readdirSync(target).length ? target + '.backup-' + migration : '';
    this.mark(staging, { migration, archived });
    if (archived) { filesIn(target); fs.renameSync(target, archived); }
    else if (fs.existsSync(target)) fs.rmdirSync(target); // Target was checked empty; never recursive.
    try { fs.renameSync(staging, target); }
    catch (error) { if (archived) fs.renameSync(archived, target); throw error; }
    this.commit(this.completed(source, target, archived));
  }
  uninstall(removePersonal = false) {
    if (removePersonal) {
      if (!this.owned(this.root)) throw Error('数据目录未通过归属校验，已保留资料。');
      filesIn(this.root); // Reject junctions anywhere before deletion.
      fs.rmSync(this.root, { recursive: true });
    }
    const runtime = path.join(this.controlRoot, 'runtime');
    if (fs.existsSync(runtime)) { filesIn(runtime); fs.rmSync(runtime, { recursive: true }); }
    if (removePersonal && fs.existsSync(this.configFile)) fs.unlinkSync(this.configFile);
  }
}
module.exports = { Storage, readIni, writeIni, filesIn, digest, DATA_NAME };
