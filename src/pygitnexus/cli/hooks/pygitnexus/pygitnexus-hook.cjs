#!/usr/bin/env node
/**
 * PyGitNexus CodeBuddy Hook
 *
 * PreToolUse  — intercepts Grep/Glob/Bash searches and augments
 *               with graph context from the PyGitNexus index.
 * PostToolUse — detects stale index after git mutations and notifies
 *               the agent to reindex.
 *
 * Mirrors the gitnexus-hook.cjs pattern, adapted for pygitnexus CLI.
 */

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

/**
 * Read JSON input from stdin synchronously.
 */
function readInput() {
  try {
    const data = fs.readFileSync(0, 'utf-8');
    return JSON.parse(data);
  } catch {
    return {};
  }
}

/**
 * Find the .pygitnexus directory by walking up from startDir.
 * Returns the path to .pygitnexus/ or null if not found.
 */
function findPyGitNexusDir(startDir) {
  let dir = startDir || process.cwd();
  for (let i = 0; i < 5; i++) {
    const candidate = path.join(dir, '.pygitnexus');
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

/**
 * Look up the current project in the global registry.
 * Returns { registered: true/false, indexedAt: string, matchedPath: string }.
 */
function lookupRegistry(cwd) {
  const registryPath = path.join(process.env.HOME || '', '.pygitnexus', 'registry.json');
  const result = { registered: false, indexedAt: '', matchedPath: '' };
  try {
    const registry = JSON.parse(fs.readFileSync(registryPath, 'utf-8'));
    const projectPath = path.resolve(cwd);
    for (const [key, entry] of Object.entries(registry)) {
      if (key === '_groups') continue;
      const entryPath = path.resolve(entry.path || '');
      // Match if current dir is the repo or a subdirectory of it
      if (projectPath === entryPath || projectPath.startsWith(entryPath + path.sep)) {
        result.registered = true;
        result.indexedAt = entry.indexed_at || '';
        result.matchedPath = entryPath;
        break;
      }
    }
  } catch {
    /* no registry file */
  }
  return result;
}

/**
 * Extract search pattern from tool input.
 */
function extractPattern(toolName, toolInput) {
  if (toolName === 'Grep') {
    return toolInput.pattern || null;
  }

  if (toolName === 'Glob') {
    const raw = toolInput.pattern || '';
    const match = raw.match(/[*\/]([a-zA-Z][a-zA-Z0-9_-]{2,})/);
    return match ? match[1] : null;
  }

  if (toolName === 'Bash') {
    const cmd = toolInput.command || '';
    if (!/\brg\b|\bgrep\b/.test(cmd)) return null;

    const tokens = cmd.split(/\s+/);
    let foundCmd = false;
    let skipNext = false;
    const flagsWithValues = new Set([
      '-e', '-f', '-m', '-A', '-B', '-C', '-g',
      '--glob', '-t', '--type', '--include', '--exclude',
    ]);

    for (const token of tokens) {
      if (skipNext) {
        skipNext = false;
        continue;
      }
      if (!foundCmd) {
        if (/\brg$|\bgrep$/.test(token)) foundCmd = true;
        continue;
      }
      if (token.startsWith('-')) {
        if (flagsWithValues.has(token)) skipNext = true;
        continue;
      }
      const cleaned = token.replace(/['"]/g, '');
      return cleaned.length >= 3 ? cleaned : null;
    }
    return null;
  }

  return null;
}

/**
 * Resolve the pygitnexus CLI path.
 * Uses the injected __PYGITNEXUS_BIN_PATH__ placeholder, or falls back
 * to PATH lookup.
 */
function resolveCliPath() {
  let cliPath = '__PYGITNEXUS_BIN_PATH__';
  if (cliPath && fs.existsSync(cliPath)) return cliPath;
  // Fallback to PATH
  return '';
}

/**
 * Spawn a pygitnexus CLI command synchronously.
 */
function runPyGitNexusCli(cliPath, args, cwd, timeout) {
  if (cliPath) {
    return spawnSync(cliPath, args, {
      encoding: 'utf-8',
      timeout,
      cwd,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
  }
  // Fallback to PATH
  return spawnSync('pygitnexus', args, {
    encoding: 'utf-8',
    timeout,
    cwd,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
}

/**
 * PreToolUse handler — augment searches with graph context.
 */
function handlePreToolUse(input) {
  const cwd = input.cwd || process.cwd();
  if (!path.isAbsolute(cwd)) return;

  // Check if project has a local .pygitnexus/ or is registered globally
  const hasLocal = findPyGitNexusDir(cwd) !== null;
  const regInfo = lookupRegistry(cwd);
  if (!hasLocal && !regInfo.registered) return;

  const toolName = input.tool_name || '';
  const toolInput = input.tool_input || {};

  if (toolName !== 'Grep' && toolName !== 'Glob' && toolName !== 'Bash') return;

  const pattern = extractPattern(toolName, toolInput);
  if (!pattern || pattern.length < 3) return;

  const cliPath = resolveCliPath();
  let result = '';
  try {
    const child = runPyGitNexusCli(cliPath, ['query', pattern], cwd, 7000);
    if (!child.error && child.status === 0) {
      result = child.stdout || '';
    }
  } catch {
    /* graceful failure */
  }

  if (result && result.trim()) {
    sendHookResponse('PreToolUse', result.trim());
  }
}

/**
 * Emit a PostToolUse hook response with additional context for the agent.
 */
function sendHookResponse(hookEventName, message) {
  console.log(
    JSON.stringify({
      hookSpecificOutput: { hookEventName, additionalContext: message },
    }),
  );
}

/**
 * Walk up from startDir to find a .pygitnexus/ directory.
 * Returns the path to .pygitnexus/ or null if not found.
 */
function findDbDir(startDir) {
  let dir = startDir || process.cwd();
  for (let i = 0; i < 5; i++) {
    const candidate = path.join(dir, '.pygitnexus');
    if (fs.existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

/**
 * PostToolUse handler — detect index staleness after git mutations,
 * or prompt initial indexing if the project has never been indexed.
 */
function handlePostToolUse(input) {
  const toolName = input.tool_name || '';
  if (toolName !== 'Bash') return;

  const command = (input.tool_input || {}).command || '';
  if (!/\bgit\s+(commit|merge|rebase|cherry-pick|pull)(\s|$)/.test(command)) return;

  // Only proceed if the command succeeded
  const toolOutput = input.tool_output || {};
  if (toolOutput.exit_code !== undefined && toolOutput.exit_code !== 0) return;

  const cwd = input.cwd || process.cwd();
  if (!path.isAbsolute(cwd)) return;

  // Check registry: registered + indexed? / registered + stale? / never indexed?
  const regInfo = lookupRegistry(cwd);
  const dbDir = findDbDir(cwd);

  // Case 1: project is registered — check staleness
  if (regInfo.registered) {
    // If recently indexed (within 5 min), skip
    if (regInfo.indexedAt) {
      try {
        const indexedMs = new Date(regInfo.indexedAt).getTime();
        const now = Date.now();
        if (now - indexedMs < 5 * 60 * 1000) return;
      } catch {
        /* unparseable date — treat as stale */
      }
    }
    const reindexCmd = 'pygitnexus analyze ' + regInfo.matchedPath;
    sendHookResponse(
      'PostToolUse',
      `PyGitNexus index may be stale (last indexed: ${regInfo.indexedAt || 'unknown'}). ` +
        `Run \`${reindexCmd}\` to update the knowledge graph.`,
    );
    return;
  }

  // Case 2: project has a .pygitnexus/ dir (local-only)
  if (dbDir) {
    const reindexCmd = 'pygitnexus analyze ' + cwd;
    sendHookResponse(
      'PostToolUse',
      `PyGitNexus index may be stale. Run \`${reindexCmd}\` to update the knowledge graph.`,
    );
    return;
  }

  // Case 3: project has never been indexed — prompt the agent
  sendHookResponse(
    'PostToolUse',
    `This project has not been indexed by PyGitNexus yet. ` +
      `Run \`pygitnexus analyze ${cwd}\` to build the knowledge graph ` +
      `(enables code graph queries and impact analysis).`,
  );
}

// Dispatch map for hook events
const handlers = {
  PreToolUse: handlePreToolUse,
  PostToolUse: handlePostToolUse,
};

function main() {
  try {
    const input = readInput();
    const handler = handlers[input.hook_event_name || ''];
    if (handler) handler(input);
  } catch (err) {
    if (process.env.PYGITNEXUS_DEBUG) {
      console.error('PyGitNexus hook error:', (err.message || '').slice(0, 200));
    }
  }
}

main();
