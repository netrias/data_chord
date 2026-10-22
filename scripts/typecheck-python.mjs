import { existsSync, readFileSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

process.chdir(fileURLToPath(new URL('../', import.meta.url)));

// npm's engine-strict does not protect npm run when dependencies already exist.
const requiredNode = readFileSync('.nvmrc', 'utf8').trim();
if (process.versions.node !== requiredNode) {
  console.error(`Python typechecking requires Node ${requiredNode}; run nvm install && nvm use.`);
  process.exit(1);
}

const python = spawnSync('uv', ['run', '--frozen', 'python', '-c', 'import sys; print(sys.executable)'], {
  encoding: 'utf8',
  stdio: ['ignore', 'pipe', 'inherit'],
});
if (python.error || python.status !== 0) {
  if (python.error) console.error(python.error.message);
  process.exit(python.status ?? 1);
}
const interpreter = python.stdout.trim();
if (!isAbsolute(interpreter) || !existsSync(interpreter)) {
  console.error('uv did not return an existing absolute Python interpreter path.');
  process.exit(1);
}

const checker = spawnSync(process.execPath, [
  'node_modules/basedpyright/index.js', '--pythonpath', interpreter, ...process.argv.slice(2),
], { stdio: 'inherit' });
if (checker.error) console.error(checker.error.message);
process.exit(checker.status ?? 1);
