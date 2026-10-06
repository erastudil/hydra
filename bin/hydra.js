#!/usr/bin/env node

/**
 * Hydra · Sovereign Multi-Headed AI Shell (Node runtime)
 * Zero external dependencies. Model aliases come from hydra_cli/catalog.json.
 */

const fs = require('fs');
const http = require('http');
const https = require('https');
const net = require('net');
const os = require('os');
const path = require('path');
const { URL } = require('url');

function loadCatalog() {
  const candidates = [
    path.join(__dirname, '..', 'hydra_cli', 'catalog.json'),
    path.join(__dirname, 'catalog.json'),
  ];
  for (const file of candidates) {
    if (fs.existsSync(file)) {
      return JSON.parse(fs.readFileSync(file, 'utf8'));
    }
  }
  throw new Error('Hydra catalog.json was not found next to this installation.');
}

const CATALOG = loadCatalog();
const VERSION = CATALOG.version || '1.1.0';
const MODEL_MAP = Object.fromEntries(
  Object.entries(CATALOG.aliases).map(([alias, spec]) => [alias, spec.model])
);

const DEFAULT_SYSTEM_PROMPT = process.env.HYDRA_SYSTEM_PROMPT ||
  'You are a world-class sovereign systems engineer. Speak concisely, rigorously, and without corporate filler or disclaimers.';

function cleanSecret(value) {
  const text = String(value || '').trim();
  if (text.includes('\n') || text.includes('\r')) return '';
  return text;
}

function gatewayKey() {
  return cleanSecret(process.env.AI_GATEWAY_API_KEY) || cleanSecret(process.env.VERCEL_AI_GATEWAY_TOKEN);
}

const SENSITIVE_SUFFIXES = ['_KEY', '_TOKEN', '_SECRET', '_PASSWORD', '_HOST', '_BASE', '_URL', '_ENDPOINT', '_ENDPOINT_ID'];
const SENSITIVE_EXACT = new Set(['CLOUDFLARE_ACCOUNT_ID', 'RUNPOD_ENDPOINT_ID']);

function sensitiveEnvKey(key) {
  const upper = String(key || '').toUpperCase();
  if (SENSITIVE_EXACT.has(upper)) return true;
  return SENSITIVE_SUFFIXES.some((suffix) => upper.endsWith(suffix));
}

function trustCwdEnv() {
  return ['1', 'true', 'yes'].includes(String(process.env.HYDRA_TRUST_CWD_ENV || '').trim().toLowerCase());
}

function applyEnvFile(file, trusted) {
  if (!fs.existsSync(file)) return;
  let raw = '';
  try {
    raw = fs.readFileSync(file, 'utf8');
  } catch (_) {
    return;
  }
  for (let line of raw.split(/\r?\n/)) {
    line = line.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    if (line.startsWith('export ')) line = line.slice('export '.length);
    const eq = line.indexOf('=');
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (value.length >= 2 && (value[0] === '"' || value[0] === "'") && value[0] === value[value.length - 1]) {
      value = value.slice(1, -1);
    }
    if (!key || process.env[key] !== undefined) continue;
    if (value.includes('\n') || value.includes('\r')) continue;
    if (!trusted && sensitiveEnvKey(key)) continue;
    process.env[key] = value;
  }
}

function loadDotenv() {
  applyEnvFile(path.join(os.homedir(), '.hydra', '.env'), true);
  applyEnvFile(path.join(process.cwd(), '.env'), trustCwdEnv());
}

function resolveRoute(alias) {
  const clean = String(alias || '').trim().toLowerCase();
  const spec = CATALOG.aliases[clean];
  if (!spec) return { model: alias, effort: null, reasoningMode: null };
  return {
    model: spec.model,
    effort: spec.effort || null,
    reasoningMode: spec.reasoning_mode || null,
  };
}

function consumeAlias(tokens) {
  if (!tokens.length) return { alias: '', rest: [] };
  const stop = tokens.indexOf('--');
  const window = stop === -1 ? tokens : tokens.slice(0, stop);
  const tail = stop === -1 ? [] : tokens.slice(stop + 1);
  const upper = Math.min(4, window.length);
  for (let count = upper; count >= 1; count -= 1) {
    const chunk = window.slice(0, count);
    if (chunk.some((part) => part.startsWith('-'))) continue;
    const candidate = chunk.join(' ').trim().toLowerCase();
    if (MODEL_MAP[candidate]) return { alias: candidate, rest: window.slice(count).concat(tail) };
  }
  if (window.length) return { alias: window[0], rest: window.slice(1).concat(tail) };
  return { alias: '', rest: tail };
}

function checkPortOpen(host, port, timeoutMs = 400) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;
    const finish = (open) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(open);
    };
    socket.setTimeout(timeoutMs);
    socket.once('connect', () => finish(true));
    socket.once('timeout', () => finish(false));
    socket.once('error', () => finish(false));
    socket.connect(port, host);
  });
}

function chatUrl(base) {
  let root = String(base || '').trim().replace(/\/+$/, '');
  if (root.endsWith('/chat/completions')) return root;
  if (!root.endsWith('/v1')) root += '/v1';
  return `${root}/chat/completions`;
}

async function detectLocalEndpoint() {
  if (process.env.LOCAL_AI_BASE && process.env.LOCAL_AI_BASE.trim()) {
    return { url: chatUrl(process.env.LOCAL_AI_BASE), name: 'Custom Local AI' };
  }
  if (process.env.OLLAMA_HOST && process.env.OLLAMA_HOST.trim()) {
    return { url: chatUrl(process.env.OLLAMA_HOST), name: 'Ollama' };
  }
  if (await checkPortOpen('127.0.0.1', 11434)) {
    return { url: chatUrl('http://127.0.0.1:11434'), name: 'Ollama' };
  }
  if (process.env.LLAMACPP_HOST && process.env.LLAMACPP_HOST.trim()) {
    return { url: chatUrl(process.env.LLAMACPP_HOST), name: 'llama.cpp' };
  }
  if (await checkPortOpen('127.0.0.1', 8080)) {
    return { url: chatUrl('http://127.0.0.1:8080'), name: 'llama.cpp' };
  }
  if (await checkPortOpen('127.0.0.1', 8000)) {
    return { url: chatUrl('http://127.0.0.1:8000'), name: 'EasyLM' };
  }
  return { url: chatUrl('http://127.0.0.1:11434'), name: 'Ollama (unverified)' };
}

function readStdin() {
  return new Promise((resolve) => {
    if (process.stdin.isTTY) return resolve('');
    const chunks = [];
    process.stdin.on('data', (chunk) => chunks.push(chunk));
    process.stdin.on('end', () => resolve(Buffer.concat(chunks).toString('utf8').trim()));
    process.stdin.on('error', () => resolve(''));
  });
}

function adaptModelForUrl(endpointUrl, model) {
  let host = '';
  try {
    host = new URL(endpointUrl).hostname.toLowerCase();
  } catch (_) {
    host = String(endpointUrl || '').toLowerCase();
  }
  if (host.endsWith('vercel.sh') || host.includes('.vercel.')) {
    if (model.startsWith('x-ai/')) return `spacexai/${model.slice('x-ai/'.length)}`;
    if (model.startsWith('meta-llama/')) {
      let rewritten = `meta/${model.slice('meta-llama/'.length)}`;
      if (rewritten.endsWith('-instruct')) rewritten = rewritten.slice(0, -'-instruct'.length);
      return rewritten;
    }
    if (model.startsWith('qwen/')) return `alibaba/${model.slice('qwen/'.length)}`;
  } else if (host === 'openrouter.ai' || host.endsWith('.openrouter.ai')) {
    if (model.startsWith('spacexai/')) return `x-ai/${model.slice('spacexai/'.length)}`;
    if (model.startsWith('alibaba/')) return `qwen/${model.slice('alibaba/'.length)}`;
    if (model.startsWith('meta/') && !model.startsWith('meta-llama/')) {
      return `meta-llama/${model.slice('meta/'.length)}`;
    }
  } else if (host.includes('cheaperinference.com') || host.includes('cheaperinference')) {
    if (model.includes('/')) {
      return model.slice(model.indexOf('/') + 1);
    }
  }
  return model;
}

function rejectsTemperature(model) {
  return (CATALOG.no_temperature_models || []).includes(model);
}

function ensureTemperature(model, temperature) {
  if (temperature === null || temperature === undefined) return;
  if (rejectsTemperature(model)) {
    const error = new Error(`${model} rejects temperature. Omit --temperature.`);
    error.usage = true;
    throw error;
  }
}

function completionTimeoutMs(effort, reasoningMode) {
  const longEffort = new Set(['high', 'xhigh', 'max']);
  if (reasoningMode || longEffort.has(String(effort || '').toLowerCase())) return 600000;
  return 180000;
}

function buildPayload(options) {
  ensureTemperature(options.model, options.temperature);
  const payload = {
    model: adaptModelForUrl(options.endpointUrl, options.model),
    messages: options.messages,
    stream: Boolean(options.stream),
  };
  if (options.temperature !== null && options.temperature !== undefined) {
    payload.temperature = options.temperature;
  }
  if (Number.isInteger(options.maxTokens) && options.maxTokens > 0) payload.max_tokens = options.maxTokens;
  const reasoning = {};
  if (options.effort) reasoning.effort = options.effort;
  if (options.reasoningMode) reasoning.mode = options.reasoningMode;
  if (Object.keys(reasoning).length) payload.reasoning = reasoning;
  return payload;
}

function requestJson(endpointUrl, headers, payload, timeoutMs) {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(endpointUrl);
    const transport = parsedUrl.protocol === 'https:' ? https : http;
    const bodyStr = JSON.stringify(payload);
    const req = transport.request(parsedUrl, {
      method: 'POST',
      headers: {
        ...headers,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(bodyStr),
      },
    }, (res) => {
      res.setEncoding('utf8');
      let data = '';
      let settled = false;
      const fail = (error) => {
        if (settled) return;
        settled = true;
        reject(error);
      };
      res.on('data', (chunk) => { data += chunk; });
      res.on('error', (error) => fail(error));
      res.on('aborted', () => fail(new Error('response aborted')));
      res.on('end', () => {
        if (settled) return;
        settled = true;
        if (res.statusCode < 200 || res.statusCode >= 300) {
          let msg = data;
          try {
            const parsed = JSON.parse(data);
            if (parsed.error && parsed.error.message) msg = parsed.error.message;
          } catch (_) {}
          reject(new Error(`HTTP ${res.statusCode}: ${msg}`));
          return;
        }
        try {
          const parsed = JSON.parse(data);
          if (parsed.error) {
            reject(new Error(parsed.error.message || JSON.stringify(parsed.error)));
            return;
          }
          const content = parsed.choices && parsed.choices[0] && parsed.choices[0].message
            ? parsed.choices[0].message.content
            : '';
          if (!content) {
            reject(new Error('empty completion'));
            return;
          }
          resolve(content);
        } catch (error) {
          reject(new Error(`Invalid JSON response: ${error.message}`));
        }
      });
    });
    req.setTimeout(timeoutMs, () => req.destroy(new Error(`Idle timeout after ${timeoutMs} ms`)));
    req.on('error', (error) => reject(error));
    req.write(bodyStr);
    req.end();
  });
}

function streamRequest(endpointUrl, headers, payload, onChunk, timeoutMs) {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(endpointUrl);
    const transport = parsedUrl.protocol === 'https:' ? https : http;
    const bodyStr = JSON.stringify(payload);
    let settled = false;
    const fail = (error) => {
      if (settled) return;
      settled = true;
      reject(error);
    };
    const req = transport.request(parsedUrl, {
      method: 'POST',
      headers: {
        ...headers,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(bodyStr),
      },
    }, (res) => {
      if (res.statusCode < 200 || res.statusCode >= 300) {
        res.setEncoding('utf8');
        let errData = '';
        res.on('data', (chunk) => { errData += chunk; });
        res.on('end', () => {
          let msg = errData;
          try {
            const parsed = JSON.parse(errData);
            if (parsed.error && parsed.error.message) msg = parsed.error.message;
          } catch (_) {}
          fail(new Error(`HTTP ${res.statusCode}: ${msg}`));
        });
        return;
      }
      res.setEncoding('utf8');
      let buffer = '';
      let sawDone = false;
      res.on('data', (chunk) => {
        buffer += chunk;
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line || line.startsWith(':')) continue;
          if (line === 'data: [DONE]') {
            sawDone = true;
            return;
          }
          if (!line.startsWith('data: ')) continue;
          try {
            const parsed = JSON.parse(line.slice(6));
            if (parsed.error) {
              fail(new Error(parsed.error.message || JSON.stringify(parsed.error)));
              req.destroy();
              return;
            }
            const delta = parsed.choices && parsed.choices[0] && parsed.choices[0].delta
              ? parsed.choices[0].delta.content || ''
              : '';
            if (delta) onChunk(delta);
          } catch (_) {}
        }
      });
      res.on('error', (error) => fail(error));
      res.on('aborted', () => fail(new Error('response aborted')));
      res.on('end', () => {
        if (settled) return;
        if (!sawDone) {
          fail(new Error('stream ended before data: [DONE]'));
          return;
        }
        settled = true;
        resolve();
      });
    });
    req.setTimeout(timeoutMs, () => req.destroy(new Error(`Idle timeout after ${timeoutMs} ms`)));
    req.on('error', (error) => fail(error));
    req.write(bodyStr);
    req.end();
  });
}

function getCheaperInferenceProvider() {
  const key = cleanSecret(process.env.CHEAPERINFERENCE_API_KEY);
  if (!key) return null;
  const base = process.env.CHEAPERINFERENCE_API_BASE || 'https://api.cheaperinference.com/v1';
  return {
    id: 'cheaperinference',
    name: 'CheaperInference',
    url: chatUrl(base),
    headers: {
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/json',
    },
  };
}

function getRunPodProvider() {
  const key = cleanSecret(process.env.RUNPOD_API_KEY);
  const endpointUrl = (process.env.RUNPOD_ENDPOINT_URL || '').trim();
  const endpointId = (process.env.RUNPOD_ENDPOINT_ID || '').trim();

  let targetUrl = '';
  if (endpointUrl) {
    targetUrl = chatUrl(endpointUrl);
  } else if (endpointId) {
    targetUrl = chatUrl(`https://api.runpod.ai/v2/${endpointId}/openai/v1`);
  } else {
    return null;
  }

  const headers = { 'Content-Type': 'application/json' };
  if (key) {
    headers.Authorization = `Bearer ${key}`;
  } else if (!endpointUrl) {
    return null;
  }

  return {
    id: 'runpod',
    name: 'RunPod',
    url: targetUrl,
    headers,
  };
}

function getModalProvider() {
  const endpointUrl = (process.env.MODAL_ENDPOINT_URL || '').trim();
  if (!endpointUrl) return null;
  const key = cleanSecret(process.env.MODAL_API_KEY);
  const headers = { 'Content-Type': 'application/json' };
  if (key) {
    headers.Authorization = `Bearer ${key}`;
  }
  return {
    id: 'modal',
    name: 'Modal',
    url: chatUrl(endpointUrl),
    headers,
  };
}

function getFrontierProviders() {
  const providers = [];
  const openRouterKey = cleanSecret(process.env.OPENROUTER_API_KEY);
  const vercelKey = gatewayKey();
  if (openRouterKey) {
    providers.push({
      id: 'openrouter',
      name: 'OpenRouter',
      url: 'https://openrouter.ai/api/v1/chat/completions',
      headers: {
        Authorization: `Bearer ${openRouterKey}`,
        'HTTP-Referer': 'https://github.com/erastudil/hydra',
        'X-Title': 'Hydra Shell Utility',
      },
    });
  }
  if (vercelKey) {
    const base = (process.env.AI_GATEWAY_API_BASE || 'https://ai-gateway.vercel.sh/v1').replace(/\/+$/, '');
    providers.push({
      id: 'vercel',
      name: 'Vercel AI Gateway',
      url: `${base}/chat/completions`,
      headers: { Authorization: `Bearer ${vercelKey}` },
    });
  }
  const cheaper = getCheaperInferenceProvider();
  if (cheaper) providers.push(cheaper);
  const runpod = getRunPodProvider();
  if (runpod) providers.push(runpod);
  const modal = getModalProvider();
  if (modal) providers.push(modal);
  return providers;
}

const PROVIDER_KEY_NAMES = {
  openrouter: 'OPENROUTER_API_KEY',
  vercel: 'AI_GATEWAY_API_KEY',
  cheaperinference: 'CHEAPERINFERENCE_API_KEY',
  runpod: 'RUNPOD_API_KEY',
  modal: 'MODAL_ENDPOINT_URL',
};

// Models that only some providers serve (catalog model_providers). Others may use any provider.
function providersForModel(model, providers) {
  const configured = providers || getFrontierProviders();
  const allowed = (CATALOG.model_providers || {})[model];
  if (!allowed || !allowed.length) return configured;
  const usable = configured
    .filter((p) => allowed.includes(p.id))
    .sort((a, b) => allowed.indexOf(a.id) - allowed.indexOf(b.id));
  if (configured.length && !usable.length) {
    const names = allowed.map((id) => PROVIDER_KEY_NAMES[id] || id).join(', ');
    throw new Error(`${model} is only served by: ${allowed.join(', ')}. Set ${names} to use it.`);
  }
  return usable;
}

// Scrub credential values, account ids, and URL paths from text meant for a terminal.
function redact(text) {
  let out = String(text);
  for (const [key, value] of Object.entries(process.env)) {
    const secret = (value || '').trim();
    if (sensitiveEnvKey(key) && secret.length >= 6) out = out.split(secret).join('<redacted>');
  }
  out = out.replace(/[a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^\s"'<>()]+/g, (raw) => {
    try {
      const u = new URL(raw);
      const more = (u.pathname && u.pathname !== '/') || u.search || u.hash;
      return `${u.protocol}//${u.host}${more ? '/...' : ''}`;
    } catch (_) {
      return '<url>';
    }
  });
  return out.replace(/(\/accounts\/)[^/\s"']+/g, '$1<redacted>');
}

// Ordered { provider, model } pairs: Cloudflare first, then OpenRouter free models.
function getFreeCandidates(modelOverride) {
  const cfToken = cleanSecret(process.env.CLOUDFLARE_API_TOKEN);
  const cfAccount = cleanSecret(process.env.CLOUDFLARE_ACCOUNT_ID);
  const openRouterKey = cleanSecret(process.env.OPENROUTER_API_KEY);
  const cloudflare = cfToken && cfAccount ? {
    id: 'cloudflare',
    name: 'Cloudflare Workers AI',
    url: `https://api.cloudflare.com/client/v4/accounts/${cfAccount}/ai/v1/chat/completions`,
    headers: { Authorization: `Bearer ${cfToken}` },
  } : null;
  const openrouter = openRouterKey ? {
    id: 'openrouter-free',
    name: 'OpenRouter Free Forge',
    url: 'https://openrouter.ai/api/v1/chat/completions',
    headers: {
      Authorization: `Bearer ${openRouterKey}`,
      'HTTP-Referer': 'https://github.com/erastudil/hydra',
      'X-Title': 'Hydra Free Forge',
    },
  } : null;
  if (!cloudflare && !openrouter) {
    throw new Error(
      'Free Forge requires either:\n' +
      '  - CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID, or\n' +
      '  - OPENROUTER_API_KEY\n' +
      'For local execution with no cloud key: hydra local "<prompt>"'
    );
  }
  if (modelOverride && modelOverride.startsWith('@cf/')) {
    if (!cloudflare) throw new Error(`${modelOverride} is a Cloudflare model. Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID.`);
    return [{ provider: cloudflare, model: modelOverride }];
  }
  if (modelOverride) return [{ provider: openrouter || cloudflare, model: modelOverride }];
  const out = [];
  if (cloudflare) {
    out.push({ provider: cloudflare, model: process.env.HYDRA_CLOUDFLARE_MODEL || CATALOG.default_cloudflare_model });
  }
  if (openrouter) {
    const models = [process.env.HYDRA_FREE_MODEL || CATALOG.default_free_model, ...(CATALOG.free_models || [])];
    for (const model of [...new Set(models.filter(Boolean))]) out.push({ provider: openrouter, model });
  }
  return out;
}

function getFreeProvider() {
  return getFreeCandidates(null)[0];
}

function headConfig(role, customModel) {
  const head = CATALOG.swarm[role] || {
    title: role.toUpperCase(),
    model: 'anthropic/claude-sonnet-5.5',
    system: `You are a specialized agent for ${role}.`,
  };
  const cfg = { ...head };
  if (customModel) {
    cfg.model = customModel;
    delete cfg.effort;
    delete cfg.reasoning_mode;
  }
  return cfg;
}

async function runHead(role, task, providers, customModel, temperature, wrapTask, maxTokens, context) {
  const head = headConfig(role, customModel);
  const started = Date.now();
  let userContent = wrapTask
    ? `Task: ${task}\n\nExecute your specialized mandate with rigorous, production-grade output.`
    : task;
  if (context) {
    userContent += '\n\nThe other heads produced the work below. Review it directly: name concrete defects ' +
      `in their design and code, and say what must change.\n\n${context}`;
  }
  let lastError = null;
  let candidates;
  try {
    candidates = providersForModel(head.model, providers);
  } catch (error) {
    candidates = [];
    lastError = error;
  }
  for (const provider of candidates) {
    try {
      const content = await requestJson(provider.url, provider.headers, buildPayload({
        endpointUrl: provider.url,
        model: head.model,
        messages: [
          { role: 'system', content: head.system },
          { role: 'user', content: userContent },
        ],
        stream: false,
        temperature,
        maxTokens,
        effort: head.effort || null,
        reasoningMode: head.reasoning_mode || null,
      }), completionTimeoutMs(head.effort, head.reasoning_mode));
      return {
        role,
        title: head.title,
        model: head.model,
        provider: provider.name,
        durationSec: Number(((Date.now() - started) / 1000).toFixed(1)),
        content,
        error: null,
        status: 'ok',
      };
    } catch (error) {
      if (error.usage) throw error;
      lastError = error;
    }
  }
  return {
    role,
    title: head.title,
    model: head.model,
    durationSec: Number(((Date.now() - started) / 1000).toFixed(1)),
    content: '',
    error: lastError ? redact(lastError.message) : 'No provider available',
    status: 'failed',
  };
}

function printHead(result) {
  const border = '='.repeat(64);
  process.stdout.write(`\n${border}\n`);
  const via = result.provider ? ` via ${result.provider}` : '';
  process.stdout.write(`[HEAD: ${String(result.title).toUpperCase()}] · ${result.model}${via} (${result.durationSec}s)\n`);
  process.stdout.write(`${border}\n\n`);
  process.stdout.write(result.error ? `[ERROR]: ${result.error}\n` : `${result.content}\n`);
}

const REVIEW_ROLES = ['auditor'];

async function runSwarm(task, selectedRoles, customModel, jsonMode, temperature, maxTokens) {
  const providers = getFrontierProviders();
  if (!providers.length) {
    throw new Error('Multi-agent swarm requires OPENROUTER_API_KEY, AI_GATEWAY_API_KEY, or CHEAPERINFERENCE_API_KEY.');
  }
  const requested = selectedRoles || ['architect', 'coder', 'auditor'];
  const explicitSynth = requested.includes('synthesizer');
  let workerRoles = requested.filter((role) => role !== 'synthesizer');
  if (!workerRoles.length) workerRoles = ['architect', 'coder', 'auditor'];
  let reviewRoles = workerRoles.filter((role) => REVIEW_ROLES.includes(role));
  let firstRoles = workerRoles.filter((role) => !reviewRoles.includes(role));
  if (!firstRoles.length) {
    firstRoles = reviewRoles;
    reviewRoles = [];
  }
  if (!jsonMode) {
    process.stderr.write(`\n[HYDRA SWARM] Fanning out ${firstRoles.length} specialist heads in parallel...\n`);
  }
  const finished = await Promise.all(firstRoles.map((role) => (
    runHead(role, task, providers, customModel, temperature, true, maxTokens, null)
  )));
  const byRole = Object.fromEntries(finished.map((result) => [result.role, result]));
  if (reviewRoles.length) {
    const reviewed = firstRoles.map((role) => byRole[role]).filter((r) => !r.error);
    const context = reviewed.map((r) => `--- ${r.title} (${r.model}) ---\n${r.content}\n\n`).join('') || null;
    if (!jsonMode) {
      process.stderr.write(`\n[HYDRA SWARM] Dispatching review head(s) over ${reviewed.length} specialist result(s)...\n`);
    }
    const reviews = await Promise.all(reviewRoles.map((role) => (
      runHead(role, task, providers, customModel, temperature, true, maxTokens, context)
    )));
    for (const r of reviews) byRole[r.role] = r;
  }
  const results = workerRoles.map((role) => byRole[role]);
  if (!jsonMode) results.forEach(printHead);
  const successes = results.filter((result) => !result.error);
  if (successes.length && (explicitSynth || successes.length >= 2)) {
    if (!jsonMode) {
      process.stderr.write('\n[HYDRA SWARM] Dispatching Synthesizer head to unify conclusions...\n');
    }
    let synthPrompt = `Original Task: ${task}\n\nBelow are the findings from the autonomous heads:\n\n`;
    for (const result of successes) {
      synthPrompt += `--- ${result.title} (${result.model}) ---\n${result.content}\n\n`;
    }
    synthPrompt += 'Consolidate these findings into a unified, decisive action roadmap. Resolve any contradictions and provide the final engineering consensus.';
    const synth = await runHead('synthesizer', synthPrompt, providers, customModel, temperature, false, maxTokens, null);
    results.push(synth);
    if (!jsonMode) printHead(synth);
  }
  if (jsonMode) process.stdout.write(`${JSON.stringify(results, null, 2)}\n`);
  return results.some((result) => result.error) ? 1 : 0;
}

const GREEN_PHOSPHOR = process.stdout.isTTY && !process.env.NO_COLOR ? '\x1b[38;5;46m' : '';
const GREEN_MID = process.stdout.isTTY && !process.env.NO_COLOR ? '\x1b[38;5;40m' : '';
const COLOR_RESET = process.stdout.isTTY && !process.env.NO_COLOR ? '\x1b[0m' : '';

const HYDRA_7_HEADS_ART = `
            [1]        [2]        [3]        [4]        [5]        [6]        [7]
           HERMES       PI      ARCHITECT  SOVEREIGN   CODER     AUDITOR   SYNTHESIS
          (\\___/)    (\\___/)    (\\___/)    <(\\___/)>   (\\___/)    (\\___/)    (\\___/)
          /0   0\\    /o   o\\    /^   ^\\    { 0   0 }   /^   ^\\    /o   o\\    /0   0\\
         ( ==Y== )  ( ==v== )  ( ==w== )  (  ==X==  ) ( ==w== )  ( ==v== )  ( ==Y== )
          )     (    )     (    )     (   / )     ( \\  )     (    )     (    )     (
         /       \\  /       \\  /       \\ ( /       \\ )/       \\  /       \\  /       \\
        /   | |   \\/   | |   \\/   | |   \\ V   | |   V /   | |   \\/   | |   \\/   | |   \\
       |    | |        | |        | |    |    | |   |   | |        | |        | |    |
       \\    \\ \\       / /        / /     |    | |   |    \\ \\        \\ \\       / /    /
        \\    \\ \\_____/ /        / /      \\    | |   /     \\ \\________\\ \\_____/ /    /
         \\    \\_______/        / /        \\___/ \\__/       \\_______/  \\_______/    /
          \\                   / /          |       |        \\                     /
           '.               .' /           |  VII  |         \\                  .'
             '.           .'  /            |       |          \\               .'
               '---------'   /             /_______\\           \\   '---------'
                            /             /         \\           \\
                           (             /   HYDRA   \\           )
                            '._________.'|   CORE    |'._________.'
                                         \\           /
                                          '---------'
`;

const HELP_BANNER = `${GREEN_MID}${HYDRA_7_HEADS_ART}${COLOR_RESET}
  ___ ___            .___
 /   |   \\___.__.  __| _/___________
/    ~    <   |  | / __ |\\_  __ \\__  \\
\\    Y    /\\___  |/ /_/ | |  | \\// __ \\_
 \\___|_  / / ____|\\____ | |__|  (____  /
       \\/  \\/          \\/            \\/
      Sovereign Multi-Headed AI Shell · v${VERSION} (Node.js)

USAGE:
    hydra <model-alias> "<prompt>"       # Direct frontier model summoning
    hydra free "<prompt>"                # Zero-cost Free Forge routing
    hydra local "<prompt>"               # Offline local inference (Ollama/llama.cpp/EasyLM)
    hydra swarm "<task>"                 # Multi-agent swarm fan-out (Architect, Coder, Auditor)
    hydra agent "<prompt>"               # Autonomous ReAct agent with MCP tools
    hydra <alias> --mcp "<prompt>"       # Tool-augmented execution loop
    hydra serve [--port 7777]            # Sovereign OpenAI Gateway for Hermes and Pi
    hydra mcp list                       # List configured community MCP servers & tools
    hydra banner                         # Display 7-headed Sovereign Hydra in terminal green
    hydra setup                          # Interactive setup & app/agent integration guide
    cat file.txt | hydra <alias>         # The pipe is the prompt
    cat file.txt | hydra <alias> - "instruction"
    hydra <alias> -- <prompt>            # Keep prompt words that match an alias

POPULAR ALIASES:
    opus 5.5 high, sol 6.1 pro, sonnet 5.5, gemini 3.8, grok 4.7, llama 4 scout, hermes, pi

OPTIONS:
    --system <prompt>       Custom system prompt
    --model <id>            Explicit model override
    --effort <level>        Reasoning effort
    --reasoning-mode <mode> Reasoning mode, such as pro
    --temperature <float>   Sampling temperature. Omitted unless you set it.
    --max-tokens <int>      Maximum generation tokens
    --no-stream             Disable real-time SSE streaming
    --json                  Output raw JSON
    --mcp                   Enable Model Context Protocol (MCP) tools
    --heads <roles>         Comma-separated swarm heads
    --list-models           List registered aliases
    -v, --version           Display version
    -h, --help              Show this help message
`;

function printSetupGuide() {
  console.log(`
================================================================================
  HYDRA SETUP & INTEGRATION GUIDE · v${VERSION} (Node.js)
================================================================================

1. CREDENTIALS
--------------------------------------------------------------------------------
Hydra reads the process environment, then ~/.hydra/.env.
A project .env may set ordinary settings such as HYDRA_FREE_MODEL.
Keys, tokens, and host URLs in a project .env stay unloaded unless HYDRA_TRUST_CWD_ENV=1.

  export AI_GATEWAY_API_KEY="your-token"
  # VERCEL_AI_GATEWAY_TOKEN is accepted as an alias.
  export OPENROUTER_API_KEY="sk-or-v1-..."
  export CHEAPERINFERENCE_API_KEY="your-key"
  export RUNPOD_API_KEY="..."
  export RUNPOD_ENDPOINT_ID="..." # or RUNPOD_ENDPOINT_URL
  export MODAL_ENDPOINT_URL="..."
  export CLOUDFLARE_API_TOKEN="..."
  export CLOUDFLARE_ACCOUNT_ID="..."

hydra free needs Cloudflare or OpenRouter. hydra local needs no cloud key.

2. NODE INTEGRATION
--------------------------------------------------------------------------------
  import { execFileSync } from 'node:child_process';

  function callHydra(alias, prompt) {
    return execFileSync('hydra', [alias, prompt, '--no-stream'], {
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'pipe'],
    }).trim();
  }

3. SWARM
--------------------------------------------------------------------------------
Specialists run in parallel. One synthesizer runs after their text exists.

  hydra swarm "Architect a low-latency order book"
  hydra swarm "Design the consensus loop" --heads architect,auditor

Docs & Source: https://github.com/erastudil/hydra
================================================================================
`);
}

function printModels() {
  console.log(`\n--- Hydra Registered Models & Aliases (v${VERSION}) ---`);
  for (const alias of Object.keys(MODEL_MAP).sort()) {
    const route = resolveRoute(alias);
    const extra = [
      route.effort ? `effort=${route.effort}` : '',
      route.reasoningMode ? `mode=${route.reasoningMode}` : '',
    ].filter(Boolean).join(' ');
    console.log(`  ${alias.padEnd(20)} -> ${route.model}${extra ? ` (${extra})` : ''}`);
  }
  console.log('');
}

async function summon(options) {
  const providers = providersForModel(options.model, getFrontierProviders());
  if (!providers.length) {
    throw new Error(
      `No frontier credentials found to summon '${options.alias}'.\n` +
      'Export OPENROUTER_API_KEY, AI_GATEWAY_API_KEY, or CHEAPERINFERENCE_API_KEY.\n' +
      'Free-tier cloud models: hydra free "<prompt>"\n' +
      'This machine only:        hydra local "<prompt>"'
    );
  }
  let lastErr = null;
  for (const provider of providers) {
    let emitted = false;
    try {
      const payload = buildPayload({
        endpointUrl: provider.url,
        model: options.model,
        messages: options.messages,
        stream: options.stream && !options.jsonMode,
        temperature: options.temperature,
        maxTokens: options.maxTokens,
        effort: options.effort,
        reasoningMode: options.reasoningMode,
      });
      if (options.stream && !options.jsonMode) {
        await streamRequest(provider.url, provider.headers, payload, (chunk) => {
          emitted = true;
          process.stdout.write(chunk);
        }, completionTimeoutMs(options.effort, options.reasoningMode));
        process.stdout.write('\n');
        return 0;
      }
      const text = await requestJson(
        provider.url,
        provider.headers,
        payload,
        completionTimeoutMs(options.effort, options.reasoningMode)
      );
      if (options.jsonMode) {
        console.log(JSON.stringify({
          model: options.model,
          provider: provider.name,
          effort: options.effort,
          reasoning_mode: options.reasoningMode,
          content: text,
        }, null, 2));
      } else {
        console.log(text);
      }
      return 0;
    } catch (error) {
      if (error.usage) throw error;
      if (emitted) {
        throw new Error(`Stream from ${provider.name} truncated after output started: ${error.message}`);
      }
      lastErr = error;
    }
  }
  throw new Error(`All providers failed for '${options.model}'. Last error: ${lastErr && lastErr.message}`);
}

async function main() {
  loadDotenv();
  let rawArgs = process.argv.slice(2);
  const early = rawArgs[0];
  if (early === '-h' || early === '--help' || early === 'help') {
    console.log(HELP_BANNER);
    return 0;
  }
  if (early === '-v' || early === '--version' || early === 'version') {
    console.log(`hydra ${VERSION}`);
    return 0;
  }
  if (early === 'banner' || early === '--banner') {
    console.log(`${GREEN_PHOSPHOR}${HYDRA_7_HEADS_ART}${COLOR_RESET}\n  ___ ___            .___\n /   |   \\___.__.  __| _/___________\n/    ~    <   |  | / __ |\\_  __ \\__  \\\n\\    Y    /\\___  |/ /_/ | |  | \\// __ \\_\n \\___|_  / / ____|\\____ | |__|  (____  /\n       \\/  \\/          \\/            \\/\n      Sovereign Multi-Headed AI Shell · v${VERSION} (Node.js)\n`);
    return 0;
  }
  if (early === 'setup' || early === 'guide' || early === '--setup' || early === '--guide') {
    printSetupGuide();
    return 0;
  }
  if (early === '--list-models' || early === 'list-models' || early === 'models') {
    printModels();
    return 0;
  }

  let earlyPrompt = null;
  if (!rawArgs.length) {
    const piped = await readStdin();
    if (!piped) {
      console.log(HELP_BANNER);
      return 0;
    }
    rawArgs = ['sonnet 5.5'];
    earlyPrompt = piped;
  }

  const consumed = consumeAlias(rawArgs);
  let systemPrompt = DEFAULT_SYSTEM_PROMPT;
  let modelOverride = null;
  let effortFlag = null;
  let modeFlag = null;
  let temperature = null;
  let maxTokens = null;
  let stream = true;
  let jsonMode = false;
  let swarmHeads = null;
  const promptTokens = [];
  const rest = consumed.rest;

  for (let i = 0; i < rest.length; i += 1) {
    const arg = rest[i];
    if (arg === '--system' && i + 1 < rest.length) {
      systemPrompt = rest[++i];
    } else if (arg === '--model' && i + 1 < rest.length) {
      modelOverride = rest[++i];
    } else if (arg === '--effort' && i + 1 < rest.length) {
      effortFlag = rest[++i].trim().toLowerCase();
    } else if (arg === '--reasoning-mode' && i + 1 < rest.length) {
      modeFlag = rest[++i].trim().toLowerCase();
    } else if (arg === '--temperature' && i + 1 < rest.length) {
      const raw = rest[++i];
      const value = Number(raw);
      if (!Number.isFinite(value)) {
        process.stderr.write(`[ERROR] --temperature expects a number, got '${raw}'.\n`);
        return 1;
      }
      temperature = value;
    } else if (arg === '--max-tokens' && i + 1 < rest.length) {
      const raw = rest[++i];
      const value = Number(raw);
      if (!Number.isInteger(value)) {
        process.stderr.write(`[ERROR] --max-tokens expects an integer, got '${raw}'.\n`);
        return 1;
      }
      maxTokens = value;
    } else if (arg === '--heads' && i + 1 < rest.length) {
      swarmHeads = rest[++i].split(',').map((item) => item.trim()).filter(Boolean);
    } else if (arg === '--no-stream') {
      stream = false;
    } else if (arg === '--json') {
      jsonMode = true;
      stream = false;
    } else {
      promptTokens.push(arg);
    }
  }

  let prompt = '';
  if (earlyPrompt !== null) {
    prompt = earlyPrompt;
  } else if (promptTokens.includes('-') || promptTokens.length === 0) {
    const piped = await readStdin();
    if (promptTokens.includes('-')) {
      const instruction = promptTokens.filter((token) => token !== '-').join(' ').trim();
      if (piped && instruction) {
        prompt = `[Piped Input]:\n${piped}\n\n[Instruction]:\n${instruction}`;
      } else {
        prompt = piped || instruction;
      }
    } else {
      prompt = piped;
    }
  } else {
    prompt = promptTokens.join(' ').trim();
  }
  if (!prompt) {
    process.stderr.write(`[ERROR] No prompt or piped input provided for '${consumed.alias}'.\n`);
    return 1;
  }

  const cmd = consumed.alias.toLowerCase().trim();
  if (cmd === 'swarm') {
    return runSwarm(prompt, swarmHeads, modelOverride, jsonMode, temperature, maxTokens);
  }
  if (cmd === 'free') {
    const candidates = getFreeCandidates(modelOverride);
    let lastErr = null;
    for (const { provider, model: targetModel } of candidates) {
      if (lastErr) {
        process.stderr.write(`[HYDRA FREE] Previous route failed (${redact(lastErr.message)}). Trying ${provider.name} with ${targetModel}.\n`);
      }
      let emitted = false;
      try {
        const payload = buildPayload({
          endpointUrl: provider.url,
          model: targetModel,
          messages: [
            { role: 'system', content: systemPrompt },
            { role: 'user', content: prompt },
          ],
          stream: stream && !jsonMode,
          temperature,
          maxTokens,
        });
        if (stream && !jsonMode) {
          await streamRequest(provider.url, provider.headers, payload, (chunk) => {
            emitted = true;
            process.stdout.write(chunk);
          }, 180000);
          process.stdout.write('\n');
        } else {
          const text = await requestJson(provider.url, provider.headers, payload, 180000);
          if (jsonMode) {
            console.log(JSON.stringify({ model: targetModel, provider: provider.name, content: text }, null, 2));
          } else {
            console.log(text);
          }
        }
        return 0;
      } catch (error) {
        if (error.usage) throw error;
        if (emitted) throw new Error(`Stream from ${provider.name} truncated after output started: ${error.message}`);
        lastErr = error;
      }
    }
    throw new Error(`Every Free Forge route failed. Last error: ${lastErr && lastErr.message}`);
  }
  if (cmd === 'local') {
    const local = await detectLocalEndpoint();
    const targetModel = modelOverride || process.env.HYDRA_LOCAL_MODEL || CATALOG.default_local_model;
    const payload = buildPayload({
      endpointUrl: local.url,
      model: targetModel,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: prompt },
      ],
      stream: stream && !jsonMode,
      temperature,
      maxTokens,
    });
    if (stream && !jsonMode) {
      await streamRequest(local.url, local.headers || { 'Content-Type': 'application/json' }, payload, (chunk) => {
        process.stdout.write(chunk);
      }, 180000);
      process.stdout.write('\n');
    } else {
      const text = await requestJson(local.url, { 'Content-Type': 'application/json' }, payload, 180000);
      if (jsonMode) {
        console.log(JSON.stringify({ model: targetModel, provider: local.name, endpoint: local.url, content: text }, null, 2));
      } else {
        console.log(text);
      }
    }
    return 0;
  }

  const route = resolveRoute(consumed.alias);
  const model = modelOverride || route.model;
  const effort = modelOverride ? effortFlag : (effortFlag || route.effort);
  const reasoningMode = modelOverride ? modeFlag : (modeFlag || route.reasoningMode);
  return summon({
    alias: consumed.alias,
    model,
    effort,
    reasoningMode,
    temperature,
    maxTokens,
    stream,
    jsonMode,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: prompt },
    ],
  });
}

module.exports = {
  CATALOG,
  VERSION,
  resolveRoute,
  consumeAlias,
  adaptModelForUrl,
  buildPayload,
  getFrontierProviders,
  getCheaperInferenceProvider,
  getRunPodProvider,
  getModalProvider,
  getFreeProvider,
  getFreeCandidates,
  providersForModel,
  redact,
};

if (require.main === module) {
  main().then((code) => {
    process.exit(code || 0);
  }).catch((error) => {
    const label = error.usage ? '[ERROR]' : '[HYDRA ERROR]';
    process.stderr.write(`\n${label} ${redact(error.message)}\n`);
    process.exit(1);
  });
}
