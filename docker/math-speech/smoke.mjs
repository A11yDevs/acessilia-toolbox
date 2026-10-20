// Build-time smoke test: the image only builds if MathJax converts, MathCAT
// speaks pt-BR and brailles CMU, and bad input is refused with the right code.
//
// golden.json pins the exact pt-BR speech of a set of formulas. A MathJax or
// MathCAT upgrade that changes any of them fails the build, so the new text
// is reviewed and golden.json updated on purpose.

import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';

const PORT = 5099;
const BASE = `http://127.0.0.1:${PORT}`;
const server = spawn(process.execPath, [new URL('./server.mjs', import.meta.url).pathname], {
  env: { ...process.env, MATH_SPEECH_PORT: String(PORT) },
  stdio: ['ignore', 'inherit', 'inherit'],
});

async function call(method, path, body) {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: response.status, json: await response.json() };
}

const checks = [
  ['health', () => call('GET', '/health'), (r) => r.status === 200 && r.json.status === 'ok'],
  ['version', () => call('GET', '/version'), (r) => r.json.mathjax && r.json.mathcat.includes('(')],
  ['convert', () => call('POST', '/convert', { latex: 'x=\\frac{a}{b}' }),
    (r) => r.status === 200 && r.json.mathml.includes('<mfrac>') && !r.json.mathml.includes('data-mjx')],
  ['invalid latex', () => call('POST', '/convert', { latex: '\\frac{a}{' }),
    (r) => r.status === 400 && r.json.detail.startsWith('invalid_latex')],
  ['html package off', () => call('POST', '/convert', { latex: '\\href{javascript:alert(1)}{x}' }),
    (r) => r.status === 400 && r.json.detail.startsWith('invalid_latex')],
  ['speak pt-BR', () => call('POST', '/speak', { latex: '\\frac{a}{b}' }),
    (r) => r.status === 200 && r.json.speech === 'a sobre b'],
  ['speak structure', () => call('POST', '/speak', { latex: '\\frac{d}{dx}\\sin x=\\cos x' }),
    (r) => r.status === 200 && r.json.speech.includes('seno') && r.json.speech.includes('cosseno')],
  ['braille CMU', () => call('POST', '/speak', { latex: '\\frac{a}{b}', braille: 'CMU' }),
    (r) => r.status === 200 && r.json.braille === '⠁⠲⠃'],
  ['invalid mathml', () => call('POST', '/speak', { mathml: '<math><mfrac><mi>a</mi>' }),
    (r) => r.status === 400 && r.json.detail.startsWith('invalid_mathml')],
  ['invalid parameter', () => call('POST', '/speak', { latex: 'x', style: 'Shout' }),
    (r) => r.status === 400 && r.json.detail.startsWith('invalid_parameter')],
  ['body too large', () => call('POST', '/speak', { latex: 'x'.repeat(300 * 1024) }),
    (r) => r.status === 413 && r.json.detail.startsWith('body_too_large')],
  ['expression too large', () => call('POST', '/convert', { latex: 'x'.repeat(20001) }),
    (r) => r.status === 413 && r.json.detail.startsWith('expression_too_large')],
  ['method not allowed', () => call('GET', '/speak'),
    (r) => r.status === 405 && r.json.detail.startsWith('method_not_allowed')],
  ['not found', () => call('GET', '/nope'),
    (r) => r.status === 404 && r.json.detail.startsWith('not_found')],
];

const golden = JSON.parse(readFileSync(new URL('./golden.json', import.meta.url), 'utf8'));
for (const { name, latex, speech } of golden) {
  checks.push([`golden: ${name}`, () => call('POST', '/speak', { latex }),
    (r) => r.status === 200 && r.json.speech === speech]);
}

let failed = 0;
try {
  for (let attempt = 0; ; attempt += 1) {
    try {
      await call('GET', '/health');
      break;
    } catch (error) {
      if (attempt > 50) throw error;
      await sleep(100);
    }
  }
  for (const [name, run, ok] of checks) {
    const result = await run();
    const passed = ok(result);
    failed += passed ? 0 : 1;
    console.log(`${passed ? 'ok  ' : 'FAIL'} ${name}${passed ? '' : ` -> ${JSON.stringify(result)}`}`);
  }
} finally {
  server.kill();
}
process.exit(failed ? 1 : 0);
