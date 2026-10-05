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
