#!/usr/bin/env node

/**
 * Hydra · Sovereign Multi-Headed AI Shell (Node / npm runtime)
 * Zero external dependencies. Fast, streaming, multi-provider, multi-head CLI.
 */

const http = require('http');
const https = require('https');
const net = require('net');
const process = require('process');
const readline = require('readline');

const VERSION = '1.0.0';

const DEFAULT_SYSTEM_PROMPT = process.env.HYDRA_SYSTEM_PROMPT ||
  'You are a world-class sovereign systems engineer. Speak concisely, rigorously, and without corporate filler or disclaimers.';

const MODEL_MAP = {
  // Anthropic Claude
  'opus 5.5': 'anthropic/claude-opus-5.5',
  'opus': 'anthropic/claude-opus-5.5',
  'opus 5': 'anthropic/claude-opus-5',
  'sonnet 5.5': 'anthropic/claude-sonnet-5.5',
  'sonnet': 'anthropic/claude-sonnet-5.5',
  'claude-5.5-sonnet': 'anthropic/claude-sonnet-5.5',
  'claude-sonnet-5.5': 'anthropic/claude-sonnet-5.5',
  'sonnet 3.7': 'anthropic/claude-3.7-sonnet',
  'haiku 4.5': 'anthropic/claude-haiku-4.5',
  'haiku': 'anthropic/claude-haiku-4.5',
  'fable 5.1': 'anthropic/claude-fable-5.1',
  'fable': 'anthropic/claude-fable-5.1',

  // OpenAI / Sol
  'sol 6.1': 'openai/gpt-6.1-sol',
  'sol 6.1 pro': 'openai/gpt-6.1-sol-pro',
  'sol': 'openai/gpt-6.1-sol',
  'gpt-6.1-sol': 'openai/gpt-6.1-sol',
  'luna': 'openai/gpt-6-luna',
  'astra': 'openai/gpt-6-astra',
  'gpt-5.5': 'openai/gpt-5.5',
  'gpt-5': 'openai/gpt-5.5',
  'gpt-4o': 'openai/gpt-4o',
  'o3': 'openai/o3',
  'o3-mini': 'openai/o3-mini',
  'o4-mini': 'openai/o4-mini',
  'o4': 'openai/o4-mini',
  'o1': 'openai/o1',

  // Google Gemini & Gemma
  'gemini 3.8': 'google/gemini-3.8-flash',
  'gemini 3.7': 'google/gemini-3.7-flash',
  'gemini 3.5': 'google/gemini-3.5-flash',
  'gemini': 'google/gemini-3.8-flash',
  'gemini-flash': 'google/gemini-3.8-flash',
  'gemini 2.5': 'google/gemini-2.5-pro',
  'gemini-pro': 'google/gemini-2.5-pro',
  'gemma 4': 'google/gemma-4-26b-a4b-it',

  // Alibaba / Qwen
  'qwen 3.8': 'qwen/qwen3.8-27b',
  'qwen': 'qwen/qwen3.8-27b',
  'qwen-coder': 'alibaba/qwen3-coder',
  'qwen coder': 'alibaba/qwen3-coder',
  'qwen 3b': 'qwen/qwen-2.5-3b-instruct',
  'qwen 3': 'qwen/qwen-2.5-coder-32b-instruct',

  // xAI / SpaceX AI Grok
  'grok 4.7': 'x-ai/grok-4.7',
  'grok 4.6': 'x-ai/grok-4.6',
  'grok': 'x-ai/grok-4.7',
  'grok 2': 'x-ai/grok-2-1212',

  // Meta Llama
  'llama 4': 'meta-llama/llama-4-maverick',
  'llama 4 maverick': 'meta-llama/llama-4-maverick',
  'llama 4 scout': 'meta-llama/llama-4-scout',
  'llama 3.3': 'meta-llama/llama-3.3-70b-instruct',
  'llama': 'meta-llama/llama-3.3-70b-instruct',

  // DeepSeek
  'deepseek': 'deepseek/deepseek-chat',
  'deepseek r1': 'deepseek/deepseek-r1',
  'deepseek-chat': 'deepseek/deepseek-chat',
};

const FREE_MODELS = [
  'qwen/qwen3.8-27b:free',
  'meta-llama/llama-3.3-70b-instruct:free',
  'google/gemma-4-26b-a4b-it:free',
  'deepseek/deepseek-chat:free',
  'nvidia/nemotron-3.5-lightning:free',
];

const SWARM_HEADS = {
  architect: {
    title: 'Architect',
    model: 'anthropic/claude-opus-5.5',
    system: 'You are the Lead Systems Architect. Analyze the requirements, state invariants, data flows, and architectural failure modes. Produce a minimal, robust architecture design.'
  },
  coder: {
    title: 'Implementer',
    model: 'anthropic/claude-sonnet-5.5',
    system: 'You are the Principal Software Engineer. Provide complete, executable, clean implementation code adhering strictly to zero-dependency principles and production standards.'
  },
  auditor: {
    title: 'Inspector',
    model: 'openai/gpt-6.1-sol',
    system: 'You are the Security & Performance Inspector. Audit the proposed design and code for edge cases, resource leaks, security vulnerabilities, and verification gates.'
  },
  synthesizer: {
    title: 'Synthesizer',
    model: 'google/gemini-3.8-flash',
    system: 'You are the Swarm Lead Synthesizer. Review all perspectives, resolve conflicting tradeoffs, and emit a final prioritized execution roadmap.'
  }
};

const HELP_BANNER = `
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
    hydra setup                          # Interactive setup & app/agent integration guide
    cat file.txt | hydra <alias>         # Interactive pipe input

POPULAR ALIASES:
    opus 5.5, sol 6.1, sonnet 5.5, gemini 2.5, gemini 3.5, qwen 3b, grok, llama

OPTIONS:
    --system <prompt>       Custom system prompt
    --model <id>            Explicit model override
    --temperature <float>   Sampling temperature (default: 0.7)
    --max-tokens <int>      Maximum generation tokens
    --no-stream             Disable real-time SSE streaming
    --json                  Output raw JSON
    --heads <roles>         Comma-separated swarm heads (e.g. architect,coder,auditor)
    --list-models           List all registered aliases and providers
    --guide, --setup        Show setup & application/agent integration guide
    -v, --version           Display version
    -h, --help              Show this help message
`;

function resolveModel(alias) {
  const clean = alias.trim().toLowerCase();
  return MODEL_MAP[clean] || alias;
}

function isCompoundAlias(arg1, arg2) {
  const candidate = `${arg1} ${arg2}`.trim().toLowerCase();
  return Boolean(MODEL_MAP[candidate]);
}

function checkPortOpen(host, port, timeoutMs = 400) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let isConnected = false;
    socket.setTimeout(timeoutMs);
    socket.once('connect', () => {
      isConnected = true;
      socket.destroy();
      resolve(true);
    });
    socket.once('timeout', () => {
      socket.destroy();
      resolve(false);
    });
    socket.once('error', () => {
      socket.destroy();
      resolve(false);
    });
    socket.connect(port, host);
  });
}

async function detectLocalEndpoint() {
  if (process.env.LOCAL_AI_BASE) {
    let base = process.env.LOCAL_AI_BASE.replace(/\/+$/, '');
    if (!base.endsWith('/v1')) base += '/v1';
    return { url: `${base}/chat/completions`, name: 'Custom Local AI' };
  }

  const ollamaHost = (process.env.OLLAMA_HOST || 'http://localhost:11434').replace(/\/+$/, '');
  let ollamaPort = 11434;
  try {
    const parsed = new URL(ollamaHost);
    if (parsed.port) ollamaPort = parseInt(parsed.port, 10);
  } catch (_) {}

  if (await checkPortOpen('127.0.0.1', ollamaPort)) {
    return { url: `${ollamaHost}/v1/chat/completions`, name: 'Ollama' };
  }

  const llamaHost = (process.env.LLAMACPP_HOST || 'http://localhost:8080').replace(/\/+$/, '');
  if (await checkPortOpen('127.0.0.1', 8080)) {
    return { url: `${llamaHost}/v1/chat/completions`, name: 'llama.cpp' };
  }

  if (await checkPortOpen('127.0.0.1', 8000)) {
    return { url: 'http://localhost:8000/v1/chat/completions', name: 'EasyLM' };
  }

  return { url: `${ollamaHost}/v1/chat/completions`, name: 'Ollama (unverified)' };
}

function readStdin(timeoutMs = 40) {
  return new Promise((resolve) => {
    if (process.stdin.isTTY) {
      return resolve('');
    }
    let data = '';
    let timer = null;
    function finish() {
      if (timer) clearTimeout(timer);
      try {
        process.stdin.removeAllListeners('data');
        process.stdin.removeAllListeners('end');
      } catch (_) {}
      resolve(data.trim());
    }
    timer = setTimeout(finish, timeoutMs);
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => {
      data += chunk;
      if (timer) clearTimeout(timer);
      timer = setTimeout(finish, timeoutMs);
    });
    process.stdin.on('end', finish);
  });
}

function adaptModelForUrl(endpointUrl, model) {
  const urlLower = endpointUrl.toLowerCase();
  if (urlLower.includes('vercel')) {
    if (model.startsWith('x-ai/')) return model.replace('x-ai/', 'spacexai/');
    if (model.startsWith('meta-llama/')) {
      let vId = model.replace('meta-llama/', 'meta/');
      if (vId.endsWith('-instruct')) vId = vId.slice(0, -'-instruct'.length);
      return vId;
    }
    if (model.startsWith('qwen/')) return model.replace('qwen/', 'alibaba/');
    if (model === 'openai/gpt-6.1-sol-pro') return 'openai/gpt-6.1-sol';
    if (model === 'openai/gpt-6-luna-pro') return 'openai/gpt-6-luna';
  } else if (urlLower.includes('openrouter')) {
    if (model.startsWith('spacexai/')) return model.replace('spacexai/', 'x-ai/');
    if (model.startsWith('alibaba/')) return model.replace('alibaba/', 'qwen/');
    if (model.startsWith('meta/')) return model.replace('meta/', 'meta-llama/');
  }
  return model;
}

function streamRequest(endpointUrl, headers, payload, onChunk) {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(endpointUrl);
    const transport = parsedUrl.protocol === 'https:' ? https : http;
    const effectivePayload = {
      ...payload,
      model: adaptModelForUrl(endpointUrl, payload.model)
    };
    const bodyStr = JSON.stringify(effectivePayload);

    const req = transport.request(parsedUrl, {
      method: 'POST',
      headers: {
        ...headers,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(bodyStr)
      }
    }, (res) => {
      if (res.statusCode < 200 || res.statusCode >= 300) {
        let errData = '';
        res.on('data', (d) => { errData += d; });
        res.on('end', () => {
          let msg = errData;
          try {
            const parsed = JSON.parse(errData);
            if (parsed.error && parsed.error.message) msg = parsed.error.message;
          } catch (_) {}
          reject(new Error(`HTTP ${res.statusCode}: ${msg}`));
        });
        return;
      }

      let buffer = '';
      res.on('data', (chunk) => {
        buffer += chunk.toString('utf8');
        const lines = buffer.split('\n');
        buffer = lines.pop(); // keep last incomplete line

        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line || line.startsWith(':')) continue;
          if (line === 'data: [DONE]') {
            return;
          }
          if (line.startsWith('data: ')) {
            try {
              const parsed = JSON.parse(line.slice(6));
              const delta = parsed.choices?.[0]?.delta?.content || '';
              if (delta) onChunk(delta);
            } catch (_) {}
          }
        }
      });

      res.on('end', () => {
        resolve();
      });
    });

    req.on('error', (err) => reject(err));
    req.write(bodyStr);
    req.end();
  });
}

function fetchRequest(endpointUrl, headers, payload) {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(endpointUrl);
    const transport = parsedUrl.protocol === 'https:' ? https : http;
    const effectivePayload = {
      ...payload,
      model: adaptModelForUrl(endpointUrl, payload.model),
      stream: false
    };
    const bodyStr = JSON.stringify(effectivePayload);

    const req = transport.request(parsedUrl, {
      method: 'POST',
      headers: {
        ...headers,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(bodyStr)
      }
    }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; });
      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          let msg = data;
          try {
            const parsed = JSON.parse(data);
            if (parsed.error && parsed.error.message) msg = parsed.error.message;
          } catch (_) {}
          return reject(new Error(`HTTP ${res.statusCode}: ${msg}`));
        }
        try {
          const parsed = JSON.parse(data);
          const content = parsed.choices?.[0]?.message?.content || '';
          resolve(content);
        } catch (e) {
          reject(new Error(`Invalid JSON response: ${e.message}`));
        }
      });
    });

    req.on('error', (err) => reject(err));
    req.write(bodyStr);
    req.end();
  });
}

function getFrontierProviders() {
  const providers = [];
  const openRouterKey = (process.env.OPENROUTER_API_KEY || '').trim();
  const vercelKey = (process.env.AI_GATEWAY_API_KEY || '').trim();

  if (openRouterKey) {
    providers.push({
      name: 'OpenRouter',
      url: 'https://openrouter.ai/api/v1/chat/completions',
      headers: {
        'Authorization': `Bearer ${openRouterKey}`,
        'HTTP-Referer': 'https://github.com/erastudil/hydra',
        'X-Title': 'Hydra Shell Utility'
      }
    });
  }

  if (vercelKey) {
    const base = (process.env.AI_GATEWAY_API_BASE || 'https://ai-gateway.vercel.sh/v1').replace(/\/+$/, '');
    providers.push({
      name: 'Vercel AI Gateway',
      url: `${base}/chat/completions`,
      headers: {
        'Authorization': `Bearer ${vercelKey}`
      }
    });
  }

  return providers;
}

function getFreeProvider() {
  const cfToken = (process.env.CLOUDFLARE_API_TOKEN || '').trim();
  const cfAccount = (process.env.CLOUDFLARE_ACCOUNT_ID || '').trim();

  if (cfToken && cfAccount) {
    return {
      provider: {
        name: 'Cloudflare Workers AI',
        url: `https://api.cloudflare.com/client/v4/accounts/${cfAccount}/ai/v1/chat/completions`,
        headers: {
          'Authorization': `Bearer ${cfToken}`
        }
      },
      model: process.env.HYDRA_CLOUDFLARE_MODEL || '@cf/meta/llama-3.3-70b-instruct'
    };
  }

  const openRouterKey = (process.env.OPENROUTER_API_KEY || '').trim();
  if (openRouterKey) {
    return {
      provider: {
        name: 'OpenRouter Free Forge',
        url: 'https://openrouter.ai/api/v1/chat/completions',
        headers: {
          'Authorization': `Bearer ${openRouterKey}`,
          'HTTP-Referer': 'https://github.com/erastudil/hydra',
          'X-Title': 'Hydra Free Forge'
        }
      },
      model: process.env.HYDRA_FREE_MODEL || 'meta-llama/llama-3.3-70b-instruct:free'
    };
  }

  throw new Error(
    'Free Forge requires either:\n' +
    '  - CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID, or\n' +
    '  - OPENROUTER_API_KEY\n' +
    'For local zero-key offline execution: hydra local "<prompt>"'
  );
}

async function runSwarm(task, selectedRoles = ['architect', 'coder', 'auditor'], customModel = null, jsonMode = false, stream = true) {
  const providers = getFrontierProviders();
  if (!providers.length) {
    throw new Error('Multi-agent swarm requires OPENROUTER_API_KEY or AI_GATEWAY_API_KEY.');
  }
  const provider = providers[0];

  if (!jsonMode) {
    process.stderr.write(`\n[HYDRA SWARM] Fanning out ${selectedRoles.length} autonomous heads in parallel...\n`);
  }

  const results = await Promise.all(selectedRoles.map(async (role) => {
    const head = SWARM_HEADS[role] || {
      title: role.toUpperCase(),
      model: 'anthropic/claude-3.7-sonnet',
      system: `You are a specialized agent for ${role}.`
    };
    const model = customModel || head.model;
    const start = Date.now();
    try {
      const content = await fetchRequest(provider.url, provider.headers, {
        model,
        messages: [
          { role: 'system', content: head.system },
          { role: 'user', content: `Task: ${task}\n\nExecute your specialized mandate with rigorous, production-grade output.` }
        ]
      });
      const durationSec = ((Date.now() - start) / 1000).toFixed(1);
      return { role, title: head.title, model, durationSec, content, error: null };
    } catch (e) {
      const durationSec = ((Date.now() - start) / 1000).toFixed(1);
      return { role, title: head.title, model, durationSec, content: '', error: e.message };
    }
  }));

  if (!jsonMode) {
    for (const res of results) {
      const border = '='.repeat(64);
      process.stdout.write(`\n${border}\n`);
      process.stdout.write(`[HEAD: ${res.title.toUpperCase()}] · ${res.model} (${res.durationSec}s)\n`);
      process.stdout.write(`${border}\n\n`);
      if (res.error) {
        process.stdout.write(`[ERROR]: ${res.error}\n`);
      } else {
        process.stdout.write(`${res.content}\n`);
      }
    }

    if (results.filter(r => !r.error).length >= 2) {
      process.stderr.write(`\n[HYDRA SWARM] Dispatching Synthesizer head to unify conclusions...\n`);
      const synthHead = SWARM_HEADS.synthesizer;
      const synthModel = customModel || synthHead.model;
      let synthPrompt = `Original Task: ${task}\n\nHead Findings:\n\n`;
      for (const r of results) {
        if (!r.error) synthPrompt += `--- ${r.title} (${r.model}) ---\n${r.content}\n\n`;
      }
      synthPrompt += `Consolidate these findings into a unified, decisive action roadmap. Resolve any contradictions and provide the final engineering consensus.`;

      const start = Date.now();
      try {
        const synthContent = await fetchRequest(provider.url, provider.headers, {
          model: synthModel,
          messages: [
            { role: 'system', content: synthHead.system },
            { role: 'user', content: synthPrompt }
          ]
        });
        const durationSec = ((Date.now() - start) / 1000).toFixed(1);
        const border = '='.repeat(64);
        process.stdout.write(`\n${border}\n`);
        process.stdout.write(`🔮 [HEAD: FINAL SYNTHESIS] · ${synthModel} (${durationSec}s)\n`);
        process.stdout.write(`${border}\n\n`);
        process.stdout.write(`${synthContent}\n`);
      } catch (e) {
        process.stdout.write(`\n[SYNTHESIZER ERROR]: ${e.message}\n`);
      }
    }
  } else {
    process.stdout.write(JSON.stringify(results, null, 2) + '\n');
  }
}

function printSetupGuide() {
  console.log(`
================================================================================
  HYDRA SETUP & INTEGRATION GUIDE · v${VERSION} (Node.js)
================================================================================

1. QUICK SETUP & CREDENTIALS
--------------------------------------------------------------------------------
Hydra resolves API keys from your environment or a local .env file.
Supported providers:

  A. Cloudflare Workers AI (Zero cost or your existing paid plan):
     export CLOUDFLARE_API_TOKEN="your-token"
     export CLOUDFLARE_ACCOUNT_ID="your-account-id"

  B. OpenRouter (Access to 200+ models with unified billing or free tiers):
     export OPENROUTER_API_KEY="sk-or-v1-..."

  C. Vercel AI Gateway (Automated multi-provider edge routing):
     export VERCEL_AI_GATEWAY_TOKEN="your-token"

  D. Zero-Configuration Modes (NO KEYS REQUIRED):
     • hydra free "<prompt>"   -> Routes to free public endpoints
     • hydra local "<prompt>"  -> Routes to local Ollama (11434), llama.cpp (8080), or EasyLM (8000)

2. SHELL SCRIPTS & UNIX PIPES
--------------------------------------------------------------------------------
Pipe outputs directly from your shell into any model:

  # Review recent git diff with Sonnet 5.5
  git diff | hydra sonnet 5.5 "Audit for security issues and edge cases"

  # Process log files without streaming into a variable
  SUMMARY=$(cat /var/log/syslog | hydra free "Extract top 3 error clusters" --no-stream)

  # Check compilation errors with Sol 6.1
  cargo check 2>&1 | hydra sol 6.1 "Suggest exact minimal diff to fix errors"

3. INTEGRATING INTO NODE.JS / TYPESCRIPT APPLICATIONS
--------------------------------------------------------------------------------
Run via global CLI or npx with zero npm install:

  import { execSync } from 'child_process';

  function callHydra(alias, prompt) {
    return execSync(\`npx hydra-cli "\${alias}" "\${prompt.replace(/"/g, '\\\\"')}" --no-stream\`, {
      encoding: 'utf-8',
      env: process.env
    }).trim();
  }

  const analysis = callHydra('sonnet 5.5', 'Analyze this payload');

4. INTEGRATING INTO AUTONOMOUS AGENTS (TOOL PATTERN)
--------------------------------------------------------------------------------
Agents can invoke Hydra as a zero-dependency external tool:

  const toolDefinition = {
    name: "summon_model",
    description: "Query frontier models (Sonnet 5.5, Opus 5.5, Sol 6.1, Grok) or free/local models",
    parameters: {
      model_alias: "sonnet 5.5 | opus 5.5 | sol 6.1 | grok | free | local",
      prompt: "The detailed instruction or analysis request"
    }
  };

5. MULTI-AGENT SWARMS
--------------------------------------------------------------------------------
Spawn 4 parallel specialized model heads (Architect, Implementer, Auditor, Synthesizer):

  hydra swarm "Architect a low-latency tick-by-tick orderbook"

Custom heads:
  hydra swarm "Design consensus loop" --heads architect,auditor

Docs & Source: https://github.com/erastudil/hydra
================================================================================
`);
}

async function main() {
  const pipedInput = await readStdin();
  const rawArgs = process.argv.slice(2);

  if (!rawArgs.length) {
    if (pipedInput) {
      rawArgs.push('sonnet 5.5');
    } else {
      console.log(HELP_BANNER);
      process.exit(0);
    }
  }

  if (rawArgs[0] === '-h' || rawArgs[0] === '--help' || rawArgs[0] === 'help') {
    console.log(HELP_BANNER);
    process.exit(0);
  }

  if (rawArgs[0] === '-v' || rawArgs[0] === '--version' || rawArgs[0] === 'version') {
    console.log(`hydra ${VERSION}`);
    process.exit(0);
  }

  if (rawArgs[0] === 'setup' || rawArgs[0] === 'guide' || rawArgs[0] === '--setup' || rawArgs[0] === '--guide') {
    printSetupGuide();
    process.exit(0);
  }

  let commandOrAlias = rawArgs.shift();

  if (rawArgs.length && isCompoundAlias(commandOrAlias, rawArgs[0])) {
    commandOrAlias = `${commandOrAlias} ${rawArgs.shift()}`;
  }

  let systemPrompt = DEFAULT_SYSTEM_PROMPT;
  let modelOverride = null;
  let temperature = 0.7;
  let maxTokens = null;
  let stream = true;
  let jsonMode = false;
  let swarmHeads = null;
  const promptTokens = [];

  for (let i = 0; i < rawArgs.length; i++) {
    const arg = rawArgs[i];
    if (arg === '--system' && i + 1 < rawArgs.length) {
      systemPrompt = rawArgs[++i];
    } else if (arg === '--model' && i + 1 < rawArgs.length) {
      modelOverride = rawArgs[++i];
    } else if (arg === '--temperature' && i + 1 < rawArgs.length) {
      temperature = parseFloat(rawArgs[++i]) || 0.7;
    } else if (arg === '--max-tokens' && i + 1 < rawArgs.length) {
      maxTokens = parseInt(rawArgs[++i], 10) || null;
    } else if (arg === '--heads' && i + 1 < rawArgs.length) {
      swarmHeads = rawArgs[++i].split(',').map(s => s.trim()).filter(Boolean);
    } else if (arg === '--no-stream') {
      stream = false;
    } else if (arg === '--json') {
      jsonMode = true;
      stream = false;
    } else {
      promptTokens.push(arg);
    }
  }

  let prompt = promptTokens.join(' ').trim();
  if (pipedInput) {
    prompt = prompt ? `[Piped Input]:\n${pipedInput}\n\n[Instruction]:\n${prompt}` : pipedInput;
  }

  if (!prompt) {
    process.stderr.write(`[ERROR] No prompt or piped input provided for '${commandOrAlias}'.\n`);
    process.exit(1);
  }

  const cmd = commandOrAlias.toLowerCase().trim();

  try {
    if (cmd === 'swarm') {
      await runSwarm(prompt, swarmHeads || ['architect', 'coder', 'auditor'], modelOverride, jsonMode, stream);
      process.exit(0);
    } else if (cmd === 'free') {
      const { provider, model } = getFreeProvider();
      const targetModel = modelOverride || model;
      const payload = {
        model: targetModel,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: prompt }
        ],
        stream,
        temperature
      };
      if (maxTokens) payload.max_tokens = maxTokens;

      if (stream && !jsonMode) {
        await streamRequest(provider.url, provider.headers, payload, (chunk) => {
          process.stdout.write(chunk);
        });
        process.stdout.write('\n');
      } else {
        const text = await fetchRequest(provider.url, provider.headers, payload);
        if (jsonMode) {
          console.log(JSON.stringify({ model: targetModel, provider: provider.name, content: text }, null, 2));
        } else {
          console.log(text);
        }
      }
      process.exit(0);
    } else if (cmd === 'local') {
      const local = await detectLocalEndpoint();
      const targetModel = modelOverride || process.env.HYDRA_LOCAL_MODEL || 'qwen2.5-coder:latest';
      const payload = {
        model: targetModel,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: prompt }
        ],
        stream,
        temperature
      };
      if (maxTokens) payload.max_tokens = maxTokens;

      if (stream && !jsonMode) {
        await streamRequest(local.url, { 'Content-Type': 'application/json' }, payload, (chunk) => {
          process.stdout.write(chunk);
        });
        process.stdout.write('\n');
      } else {
        const text = await fetchRequest(local.url, { 'Content-Type': 'application/json' }, payload);
        if (jsonMode) {
          console.log(JSON.stringify({ model: targetModel, provider: local.name, endpoint: local.url, content: text }, null, 2));
        } else {
          console.log(text);
        }
      }
      process.exit(0);
    } else {
      // Direct frontier summoning
      const providers = getFrontierProviders();
      if (!providers.length) {
        throw new Error(
          `No frontier credentials found to summon '${commandOrAlias}'.\n` +
          'Please export OPENROUTER_API_KEY or AI_GATEWAY_API_KEY in your environment,\n' +
          'or run zero-cost inference via: hydra free "<prompt>"\n' +
          'or local inference via:        hydra local "<prompt>"'
        );
      }

      const modelId = modelOverride || resolveModel(commandOrAlias);
      const payload = {
        model: modelId,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: prompt }
        ],
        stream,
        temperature
      };
      if (maxTokens) payload.max_tokens = maxTokens;

      let lastErr = null;
      for (const provider of providers) {
        try {
          if (stream && !jsonMode) {
            await streamRequest(provider.url, provider.headers, payload, (chunk) => {
              process.stdout.write(chunk);
            });
            process.stdout.write('\n');
            process.exit(0);
          } else {
            const text = await fetchRequest(provider.url, provider.headers, payload);
            if (jsonMode) {
              console.log(JSON.stringify({ model: modelId, provider: provider.name, content: text }, null, 2));
            } else {
              console.log(text);
            }
            process.exit(0);
          }
        } catch (e) {
          lastErr = e;
        }
      }
      throw new Error(`All providers failed for '${modelId}'. Last error: ${lastErr?.message}`);
    }
  } catch (err) {
    process.stderr.write(`\n[HYDRA ERROR] ${err.message}\n`);
    process.exit(1);
  }
}

main().catch((err) => {
  process.stderr.write(`\n[FATAL ERROR] ${err.message}\n`);
  process.exit(1);
});
