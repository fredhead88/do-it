import { spawn } from 'node:child_process';

/**
 * spawnWithStdin(cmd, args, input) -> stdout string
 * Spawns a process, writes input to stdin, returns stdout.
 * Sequential (no parallelism).
 */
function spawnWithStdin(cmd, args, input) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      env: { ...process.env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', d => { stdout += d; });
    child.stderr.on('data', d => { stderr += d; });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(`${cmd} exited ${code}: ${stderr.slice(0, 300)}`));
      } else {
        resolve(stdout.trim());
      }
    });
    if (input) {
      child.stdin.write(input);
    }
    // Always close stdin so the subprocess knows there is no more input
    child.stdin.end();
  });
}

/**
 * runCodex(prompt) -> stdout string
 * Spawns: codex exec --skip-git-repo-check <prompt>
 * Passes the prompt as a positional argument. Sequential. Returns model output.
 */
export async function runCodex(prompt) {
  // codex exec accepts the prompt as a positional argument.
  // The --skip-git-repo-check flag bypasses the git-repo requirement.
  const raw = await spawnWithStdin(
    'codex',
    ['exec', '--skip-git-repo-check', prompt],
    null,
  );
  // codex output format:
  //   [header block]
  //   user
  //   <prompt>
  //   codex
  //   <model response>
  //   tokens used
  //   <N>
  //   <model response again>
  // Extract the model response: text between "codex\n" and "tokens used"
  const beforeTokens = raw.split(/\ntokens used\b/i)[0];
  const lines = beforeTokens.split('\n');
  const codexIdx = lines.lastIndexOf('codex');
  if (codexIdx >= 0 && codexIdx < lines.length - 1) {
    return lines.slice(codexIdx + 1).join('\n').trim();
  }
  // Fallback: everything before "tokens used"
  return beforeTokens.trim();
}

/**
 * runClaude(prompt) -> stdout string
 * Spawns: claude -p <prompt>
 * Sequential. Returns model output.
 */
export async function runClaude(prompt) {
  const raw = await spawnWithStdin(
    'claude',
    ['-p', prompt],
    null,
  );
  return raw.trim();
}
