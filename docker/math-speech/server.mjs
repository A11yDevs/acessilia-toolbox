// math-speech sidecar: LaTeX -> MathML (MathJax) and MathML -> speech and
// braille (MathCAT), behind a small HTTP API for math.convert and
// math.verbalize.
//
//   POST /convert  {latex, display?}                      -> {latex, mathml}
//   POST /speak    {latex | mathml, language?, style?, verbosity?, braille?}
//                                                         -> {mathml, speech, braille?, ...}
//   GET  /health, GET /version
//
// MathJax and MathCAT are pinned (package-lock.json, mathcat-wasm/Cargo.toml).
// /version reports both; the Toolbox adapter feeds them into the cache key,
// so an upgrade re-speaks instead of serving text produced by older rules.
//
// Errors answer {"detail": "<code>: <reason>"}. Caller errors are 400
// (invalid_json, invalid_latex, invalid_mathml, invalid_parameter) or 413;
// a MathCAT failure on valid MathML is 500 (speech_failed, braille_failed).
// No error message quotes the expression, which may be document content.

import http from 'node:http';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

const SERVER_VERSION = '1.0.0';
const PORT = Number(process.env.MATH_SPEECH_PORT ?? 5009);
const MAX_BODY_BYTES = Number(process.env.MATH_SPEECH_MAX_BODY_BYTES ?? 256 * 1024);
// A formula, not a document: bounds the synchronous work of one request.
const MAX_EXPRESSION_CHARS = Number(process.env.MATH_SPEECH_MAX_EXPRESSION_CHARS ?? 20000);

// The Toolbox speaks pt-BR; MathCAT's Brazilian rules are its "pt" language.
const LANGUAGES = { 'pt-BR': 'pt', pt: 'pt', en: 'en' };
const STYLES = ['ClearSpeak', 'SimpleSpeak'];
const VERBOSITIES = ['Terse', 'Medium', 'Verbose'];
const BRAILLE_CODES = ['CMU', 'Nemeth', 'UEB'];

// ---------------------------------------------------------------------------
// MathJax (TeX input, serialized MathML output)
// ---------------------------------------------------------------------------

const { mathjax } = require('mathjax-full/js/mathjax.js');
const { TeX } = require('mathjax-full/js/input/tex.js');
const { liteAdaptor } = require('mathjax-full/js/adaptors/liteAdaptor.js');
const { RegisterHTMLHandler } = require('mathjax-full/js/handlers/html.js');
const { AllPackages } = require('mathjax-full/js/input/tex/AllPackages.js');
const { SerializedMmlVisitor } = require('mathjax-full/js/core/MmlTree/SerializedMmlVisitor.js');
const { STATE } = require('mathjax-full/js/core/MathItem.js');
const MATHJAX_VERSION = require('mathjax-full/package.json').version;

// Left out: bussproofs needs an output jax with layout; html lets a formula
// inject \href/\class/\style into MathML that ends up in HTML exports; require
// and autoload load code at run time; noerrors and noundefined turn invalid
// LaTeX into red MathML instead of an error the caller can act on.
const EXCLUDED_PACKAGES = new Set([
  'bussproofs', 'html', 'require', 'autoload', 'noerrors', 'noundefined', 'setoptions',
]);
const TEX_PACKAGES = AllPackages.filter((name) => !EXCLUDED_PACKAGES.has(name));

RegisterHTMLHandler(liteAdaptor());
const texDocument = mathjax.document('', {
  InputJax: new TeX({
    packages: TEX_PACKAGES,
    formatError: (_jax, error) => {
      throw error;
    },
  }),
});
const mmlVisitor = new SerializedMmlVisitor();

class RequestError extends Error {
  constructor(status, code, reason) {
    super(`${code}: ${reason}`);
    this.status = status;
  }
}

function latexToMathml(latex, display) {
  let node;
  try {
    node = texDocument.convert(latex, { display, end: STATE.CONVERT });
  } catch (error) {
    // TexError carries an id such as MissingCloseBrace; its message may quote
    // the macro or text that failed.
    throw new RequestError(400, 'invalid_latex', error?.id ?? 'TeX error');
  }
  // data-mjx-* attributes are MathJax rendering hints, not MathML.
  return mmlVisitor.visitTree(node, texDocument).replace(/\s+data-mjx-[a-z-]+="[^"]*"/g, '');
}

// ---------------------------------------------------------------------------
// MathCAT (WebAssembly)
// ---------------------------------------------------------------------------

const mathcat = require('./mathcat/mathcat_wasm.js');
let mathcatReady = false;
let mathcatProblem = '';
try {
  mathcat.init();
  mathcatReady = true;
} catch (error) {
  mathcatProblem = String(error?.message ?? error);
}

// MathCAT errors come prefixed with their code (see mathcat-wasm/src/lib.rs).
function mathcatError(error) {
  const message = String(error?.message ?? error);
  const [code] = message.split(':', 1);
  if (code === 'invalid_mathml' || code === 'invalid_preference') {
    return new RequestError(400, code, message.slice(code.length + 1).trim() || 'rejected');
  }
  return new RequestError(500, code || 'speech_failed', message.slice(code.length + 1).trim());
}

// ---------------------------------------------------------------------------
// Request handling
// ---------------------------------------------------------------------------

function choice(body, field, allowed, fallback) {
  const value = body[field] ?? fallback;
  if (!allowed.includes(value)) {
    throw new RequestError(400, 'invalid_parameter', `${field} must be one of ${allowed.join(', ')}`);
  }
  return value;
}

function expression(body, field) {
  const value = body[field];
  if (typeof value !== 'string' || !value.trim()) {
    return null;
  }
  if (value.length > MAX_EXPRESSION_CHARS) {
    throw new RequestError(413, 'expression_too_large', `${field} exceeds ${MAX_EXPRESSION_CHARS} characters`);
  }
  return value.trim();
}

function convert(body) {
  const latex = expression(body, 'latex');
  if (latex === null) {
    throw new RequestError(400, 'invalid_parameter', 'latex must be a non-empty string');
  }
  return { latex, mathml: latexToMathml(latex, body.display === true) };
}

function speak(body) {
  const latex = expression(body, 'latex');
  const given = expression(body, 'mathml');
  if ((latex === null) === (given === null)) {
    throw new RequestError(400, 'invalid_parameter', 'send exactly one of latex or mathml');
  }
  const language = choice(body, 'language', Object.keys(LANGUAGES), 'pt-BR');
  const style = choice(body, 'style', STYLES, 'ClearSpeak');
  const verbosity = choice(body, 'verbosity', VERBOSITIES, 'Medium');
  const brailleCode = body.braille == null ? null : choice(body, 'braille', BRAILLE_CODES, null);

  const mathml = given ?? latexToMathml(latex, body.display === true);
  const result = { mathml, language, style, verbosity };
  try {
    result.speech = mathcat.speak(mathml, LANGUAGES[language], style, verbosity).trim();
    if (brailleCode) {
      result.braille = mathcat.braille(mathml, brailleCode);
      result.braille_code = brailleCode;
    }
  } catch (error) {
    throw mathcatError(error);
  }
  return result;
}

function versions() {
  return {
    version: SERVER_VERSION,
    mathjax: MATHJAX_VERSION,
    mathcat: mathcatReady ? mathcat.version() : '',
    node: process.versions.node,
    capability: 'math.convert,math.verbalize',
  };
}

const ROUTES = {
  'GET /health': () => {
    if (!mathcatReady) {
      throw new RequestError(503, 'unavailable', `MathCAT failed to start: ${mathcatProblem}`);
    }
    return { status: 'ok', version: SERVER_VERSION };
  },
  'GET /version': versions,
  'POST /convert': convert,
  'POST /speak': speak,
};

function readJson(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    request.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new RequestError(413, 'body_too_large', `body exceeds ${MAX_BODY_BYTES} bytes`));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => {
      try {
        const body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
        if (body === null || typeof body !== 'object' || Array.isArray(body)) {
          throw new Error('not an object');
        }
        resolve(body);
      } catch {
        reject(new RequestError(400, 'invalid_json', 'body must be a JSON object'));
      }
    });
    request.on('error', reject);
  });
}

function send(response, status, payload) {
  const body = JSON.stringify(payload);
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
  });
  response.end(body);
}

const server = http.createServer(async (request, response) => {
  const path = new URL(request.url, 'http://localhost').pathname;
  const handler = ROUTES[`${request.method} ${path}`];
  if (!handler) {
    const known = Object.keys(ROUTES).some((route) => route.endsWith(` ${path}`));
    send(response, known ? 405 : 404, { detail: known ? 'method not allowed' : 'not found' });
    return;
  }
  try {
    const body = request.method === 'POST' ? await readJson(request) : {};
    send(response, 200, handler(body));
  } catch (error) {
    if (error instanceof RequestError) {
      send(response, error.status, { detail: error.message });
      return;
    }
    console.error('math-speech: unexpected error', error);
    send(response, 500, { detail: 'internal_error: unexpected failure' });
  }
});

server.listen(PORT, () => {
  console.log(`math-speech ${SERVER_VERSION} listening on ${PORT}`, JSON.stringify(versions()));
});

for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
