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

test('resolves glm and kolibri aliases correctly', () => {
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

  const kolibri = hydra.resolveRoute('kolibri');
  assert.equal(kolibri.model, 'Aleph-Alpha/Kolibri-1');
});

test('consumeAlias handles compound glm and single kolibri', () => {
  const prime = hydra.consumeAlias(['glm', '5.3', 'prime', 'write', 'code']);
  assert.equal(prime.alias, 'glm 5.3 prime');
  assert.deepEqual(prime.rest, ['write', 'code']);

  const flash = hydra.consumeAlias(['glm', '4.7', 'flash', 'quick']);
  assert.equal(flash.alias, 'glm 4.7 flash');
  assert.deepEqual(flash.rest, ['quick']);

  const kol = hydra.consumeAlias(['kolibri', 'hello']);
  assert.equal(kol.alias, 'kolibri');
  assert.deepEqual(kol.rest, ['hello']);
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
