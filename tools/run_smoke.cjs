'use strict';
// Real application test; all state stays inside this independent project.
const fs = require('node:fs');
const path = require('node:path');
const { spawn, execFile } = require('node:child_process');
const { assertPortFree } = require('../desktop/backend.cjs');
const project = path.resolve(__dirname, '..');
async function main() {
  await assertPortFree();
  const packaged = process.argv.includes('--packaged');
  const requested = process.argv.find(value => value.startsWith('--profile='))?.slice(10);
  if (requested && !/^[a-z0-9-]+$/.test(requested)) throw new Error('Smoke profile must be a folder name under .test-data.');
  const profile = path.join(project, '.test-data', requested || `${packaged ? 'packaged' : 'development'}-${Date.now()}`);
  fs.mkdirSync(profile, { recursive: true });
  const executable = packaged ? path.join(project, 'dist', 'win-unpacked', 'Infinite Canvas.exe') : require('electron');
  const env = { ...process.env, IC_DESKTOP_ROOT: profile };
  delete env.ELECTRON_RUN_AS_NODE;
  const args = [...(packaged ? [] : [project]), '--desktop-smoke'];
  const child = spawn(executable, args, { env, cwd: project, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '', errors = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { errors += chunk; });
  const timer = setTimeout(() => {
    if (child.exitCode === null && child.pid) execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }, () => {});
  }, 120000);
  try {
    const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', resolve); });
    const lines = output.split(/\r?\n/).filter(line => line.startsWith('IC_DESKTOP_SMOKE'));
    for (const line of lines) console.log(line);
    if (code !== 0 || !lines.some(line => line.startsWith('IC_DESKTOP_SMOKE '))) {
      // Electron diagnostics only; backend raw logs are never relayed by the shell.
      console.error(errors.slice(-5000).replace(/(?:Bearer\s+|sk-)[\w.-]+/gi, '[redacted]'));
      throw new Error(`Desktop smoke failed (exit ${code}).`);
    }
    // The app must leave its exclusive port free after exiting.
    await assertPortFree();
    console.log(JSON.stringify({ packaged, processExit: code, backendStopped: true, profile }));
  } finally { clearTimeout(timer); }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
