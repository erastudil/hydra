const assert = require('node:assert/strict');
const test = require('node:test');
const hydra = require('../bin/hydra.js');

test('longest alias stops at --', () => {
  const pro = hydra.consumeAlias(['sol', '6.1', 'pro', 'and', 'con']);
  assert.equal(pro.alias, 'sol 6.1 pro');
  assert.deepEqual(pro.rest, ['and', 'con']);
  const stopped = hydra.consumeAlias(['opus', '5.5', '--', 'high', 'ground']);
  assert.equal(stopped.alias, 'opus 5.5');
  assert.deepEqual(stopped.rest, ['high', 'ground']);
  const scout = hydra.consumeAlias(['llama', '4', 'scout', 'summarize']);
  assert.equal(scout.alias, 'llama 4 scout');
  assert.deepEqual(scout.rest, ['summarize']);
});

test('unknown pro suffix passes through', () => {
  const url = 'https://ai-gateway.vercel.sh/v1/chat/completions';
  assert.equal(
    hydra.adaptModelForUrl(url, 'openai/gpt-6.1-sol-pro'),
    'openai/gpt-6.1-sol-pro'
  );
});

test('temperature is omitted unless the caller sets it', () => {
  const base = {
    endpointUrl: 'https://openrouter.ai/api/v1/chat/completions',
    model: 'anthropic/claude-sonnet-5.5',
    messages: [],
    stream: false,
  };
  const omitted = hydra.buildPayload({ ...base, temperature: null });
  assert.equal(Object.hasOwn(omitted, 'temperature'), false);
  const zero = hydra.buildPayload({ ...base, temperature: 0 });
  assert.equal(zero.temperature, 0);
});

test('sol 6.1 pro sends mode pro on gpt-6.1-sol', () => {
  const route = hydra.resolveRoute('sol 6.1 pro');
  const payload = hydra.buildPayload({
    endpointUrl: 'https://ai-gateway.vercel.sh/v1/chat/completions',
    model: route.model,
    messages: [{ role: 'user', content: 'hi' }],
    stream: false,
    temperature: null,
    effort: route.effort,
    reasoningMode: route.reasoningMode,
  });
  assert.equal(payload.model, 'openai/gpt-6.1-sol');
  assert.equal(payload.reasoning.effort, 'high');
  assert.equal(payload.reasoning.mode, 'pro');
});

test('opus 5.5 refuses temperature locally', () => {
  assert.throws(
    () => hydra.buildPayload({
      endpointUrl: 'https://ai-gateway.vercel.sh/v1/chat/completions',
      model: 'anthropic/claude-opus-5.5',
      messages: [],
      stream: false,
      temperature: 0.2,
    }),
    /rejects temperature/
  );
});

test('resolves glm aliases correctly', () => {
  const g53 = hydra.resolveRoute('glm 5.3');
  assert.equal(g53.model, 'glm-5.3');
  assert.equal(g53.effort, 'high');

  const g53p = hydra.resolveRoute('glm 5.3 prime');
  assert.equal(g53p.model, 'glm-5.3-prime');
  assert.equal(g53p.effort, 'high');

  assert.equal(hydra.resolveRoute('glm').model, 'glm-5.3');
  assert.equal(hydra.resolveRoute('glm 5').model, 'glm-5.3');
  assert.equal(hydra.resolveRoute('glm 4.7').model, 'glm-4.7');
  assert.equal(hydra.resolveRoute('glm 4.7 flash').model, 'glm-4.7-flash');

  // Kolibri-1 is served by no configured provider, so the alias is gone.
  assert.equal(Object.hasOwn(hydra.CATALOG.aliases, 'kolibri'), false);
});

test('consumeAlias handles compound and single glm', () => {
  const prime = hydra.consumeAlias(['glm', '5.3', 'prime', 'write', 'code']);
  assert.equal(prime.alias, 'glm 5.3 prime');
  assert.deepEqual(prime.rest, ['write', 'code']);

  const flash = hydra.consumeAlias(['glm', '4.7', 'flash', 'quick']);
  assert.equal(flash.alias, 'glm 4.7 flash');
  assert.deepEqual(flash.rest, ['quick']);

  const single = hydra.consumeAlias(['glm', 'hello']);
  assert.equal(single.alias, 'glm');
  assert.deepEqual(single.rest, ['hello']);
});

test('cheaperinference strips vendor prefix', () => {
  const url = 'https://api.cheaperinference.com/v1/chat/completions';
  assert.equal(hydra.adaptModelForUrl(url, 'z-ai/glm-5.3'), 'glm-5.3');
  assert.equal(hydra.adaptModelForUrl(url, 'anthropic/claude-opus-5.5'), 'claude-opus-5.5');
  assert.equal(hydra.adaptModelForUrl(url, 'openai/gpt-6.1-sol'), 'gpt-6.1-sol');
  assert.equal(hydra.adaptModelForUrl(url, 'Aleph-Alpha/Kolibri-1'), 'Kolibri-1');
  assert.equal(hydra.adaptModelForUrl(url, 'glm-5.3'), 'glm-5.3');
});

test('cheaperinference provider builder', () => {
  const origKey = process.env.CHEAPERINFERENCE_API_KEY;
  const origBase = process.env.CHEAPERINFERENCE_API_BASE;
  try {
    delete process.env.CHEAPERINFERENCE_API_KEY;
    assert.equal(hydra.getCheaperInferenceProvider(), null);

    process.env.CHEAPERINFERENCE_API_KEY = 'ci-node-test';
    const p = hydra.getCheaperInferenceProvider();
    assert.equal(p.name, 'CheaperInference');
    assert.equal(p.url, 'https://api.cheaperinference.com/v1/chat/completions');
    assert.equal(p.headers.Authorization, 'Bearer ci-node-test');

    process.env.CHEAPERINFERENCE_API_BASE = 'https://custom.cheaper.io/v1';
    const custom = hydra.getCheaperInferenceProvider();
    assert.equal(custom.url, 'https://custom.cheaper.io/v1/chat/completions');
  } finally {
    if (origKey !== undefined) process.env.CHEAPERINFERENCE_API_KEY = origKey;
    else delete process.env.CHEAPERINFERENCE_API_KEY;
    if (origBase !== undefined) process.env.CHEAPERINFERENCE_API_BASE = origBase;
    else delete process.env.CHEAPERINFERENCE_API_BASE;
  }
});

test('runpod provider builder', () => {
  const origKey = process.env.RUNPOD_API_KEY;
  const origUrl = process.env.RUNPOD_ENDPOINT_URL;
  const origId = process.env.RUNPOD_ENDPOINT_ID;
  try {
    delete process.env.RUNPOD_API_KEY;
    delete process.env.RUNPOD_ENDPOINT_URL;
    delete process.env.RUNPOD_ENDPOINT_ID;
    assert.equal(hydra.getRunPodProvider(), null);

    process.env.RUNPOD_API_KEY = 'rp-key';
    process.env.RUNPOD_ENDPOINT_ID = 'ep-xyz';
    const p = hydra.getRunPodProvider();
    assert.equal(p.name, 'RunPod');
    assert.equal(p.url, 'https://api.runpod.ai/v2/ep-xyz/openai/v1/chat/completions');
    assert.equal(p.headers.Authorization, 'Bearer rp-key');

    process.env.RUNPOD_ENDPOINT_URL = 'https://custom.runpod.proxy/v1';
    const custom = hydra.getRunPodProvider();
    assert.equal(custom.url, 'https://custom.runpod.proxy/v1/chat/completions');
  } finally {
    if (origKey !== undefined) process.env.RUNPOD_API_KEY = origKey;
    else delete process.env.RUNPOD_API_KEY;
    if (origUrl !== undefined) process.env.RUNPOD_ENDPOINT_URL = origUrl;
    else delete process.env.RUNPOD_ENDPOINT_URL;
    if (origId !== undefined) process.env.RUNPOD_ENDPOINT_ID = origId;
    else delete process.env.RUNPOD_ENDPOINT_ID;
  }
});

test('modal provider builder', () => {
  const origUrl = process.env.MODAL_ENDPOINT_URL;
  const origKey = process.env.MODAL_API_KEY;
  try {
    delete process.env.MODAL_ENDPOINT_URL;
    delete process.env.MODAL_API_KEY;
    assert.equal(hydra.getModalProvider(), null);

    process.env.MODAL_ENDPOINT_URL = 'https://workspace--app.modal.run';
    const p = hydra.getModalProvider();
    assert.equal(p.name, 'Modal');
    assert.equal(p.url, 'https://workspace--app.modal.run/v1/chat/completions');
    assert.equal(p.headers.Authorization, undefined);

    process.env.MODAL_API_KEY = 'modal-secret';
    const withKey = hydra.getModalProvider();
    assert.equal(withKey.headers.Authorization, 'Bearer modal-secret');
  } finally {
    if (origUrl !== undefined) process.env.MODAL_ENDPOINT_URL = origUrl;
    else delete process.env.MODAL_ENDPOINT_URL;
    if (origKey !== undefined) process.env.MODAL_API_KEY = origKey;
    else delete process.env.MODAL_API_KEY;
  }
});

function withEnv(vars, fn) {
  const saved = {};
  for (const key of Object.keys(vars)) {
    saved[key] = process.env[key];
    if (vars[key] === undefined) delete process.env[key];
    else process.env[key] = vars[key];
  }
  try {
    return fn();
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

test('cloudflare default model is a real Workers AI id', () => {
  assert.equal(hydra.CATALOG.default_cloudflare_model, '@cf/meta/llama-3.3-70b-instruct-fp8-fast');
});

test('vercel-only models skip OpenRouter', () => {
  const providers = [
    { id: 'openrouter', name: 'OpenRouter', url: 'https://openrouter.ai/api/v1/chat/completions' },
    { id: 'vercel', name: 'Vercel AI Gateway', url: 'https://ai-gateway.vercel.sh/v1/chat/completions' },
  ];
  assert.deepEqual(hydra.providersForModel('anthropic/claude-opus-5.5-fast', providers).map((p) => p.id), ['vercel']);
  assert.deepEqual(hydra.providersForModel('anthropic/claude-sonnet-5.5', providers).map((p) => p.id), ['openrouter', 'vercel']);
  assert.throws(() => hydra.providersForModel('glm-5.3', providers), /CHEAPERINFERENCE_API_KEY/);
});

test('free forge falls back from Cloudflare to OpenRouter free models', () => {
  withEnv({
    CLOUDFLARE_API_TOKEN: 'cf-token-abcdef',
    CLOUDFLARE_ACCOUNT_ID: 'acct123456',
    OPENROUTER_API_KEY: 'sk-or-abcdef',
    HYDRA_FREE_MODEL: undefined,
    HYDRA_CLOUDFLARE_MODEL: undefined,
  }, () => {
    const candidates = hydra.getFreeCandidates(null);
    assert.equal(candidates[0].provider.id, 'cloudflare');
    assert.ok(candidates.length > 1);
    assert.ok(candidates.slice(1).every((c) => c.provider.id === 'openrouter-free' && c.model.endsWith(':free')));
    const cfOnly = hydra.getFreeCandidates('@cf/meta/llama-3.1-8b-instruct-fp8');
    assert.deepEqual(cfOnly.map((c) => c.provider.id), ['cloudflare']);
  });
});

test('redact hides account ids, URL paths, and secret values', () => {
  withEnv({ OPENROUTER_API_KEY: 'sk-or-v1-supersecret' }, () => {
    const clean = hydra.redact('https://api.cloudflare.com/client/v4/accounts/0123abcd/ai key sk-or-v1-supersecret /accounts/0123abcd/x');
    assert.equal(clean.includes('0123abcd'), false);
    assert.equal(clean.includes('sk-or-v1-supersecret'), false);
    assert.ok(clean.includes('https://api.cloudflare.com/...'));
  });
});

test('version comes from the catalog', () => {
  assert.equal(hydra.VERSION, '1.2.2');
});

test('dead alice-emap alias is gone; current gateway aliases resolve', () => {
  assert.equal(Object.hasOwn(hydra.CATALOG.aliases, 'alice-emap'), false);
  assert.equal(hydra.resolveRoute('sol 5.6').model, 'openai/gpt-5.6-sol');
  assert.equal(hydra.resolveRoute('muse').model, 'meta/muse-spark-1.3');
  assert.equal(hydra.resolveRoute('llama 3.1 8b').model, 'meta-llama/Llama-3.1-8B-Instruct');
});

test('sanitizeStreamText strips CR and mouse-tracking ANSI', () => {
  assert.equal(hydra.sanitizeStreamText('a\rb\x1b[?1000hc'), 'abc');
});
