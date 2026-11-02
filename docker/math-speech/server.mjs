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
// Errors answer {"detail": "<code>: <reason>"}:
//   400  invalid_json, invalid_latex, invalid_mathml, invalid_parameter
//   404  not_found;  405 method_not_allowed (with an Allow header)
//   413  body_too_large (request body), expression_too_large (one field)
//   500  speech_failed (MathCAT failed on valid MathML), internal_error
//   503  unavailable (/health, when MathCAT did not start or broke)
// A braille failure does not discard the speech: /speak answers 200 without
// braille and with braille_error: "braille_failed: <reason>".
// No error message quotes the expression, which may be document content.

import http from 'node:http';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

const SERVER_VERSION = '1.0.1';
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
    // In pt-BR, `{,}` is a decimal separator (-0{,}0833), so it may end a
    // number with any count of digits; MathJax's default only takes `{,}` before
    // a three-digit thousands group, which still comes first (1{,}000.5). One
    // decimal part at most: 1{,}2{,}3 and 1{,}23.4 do not read as one number.
    digits: /^(?:[0-9]+(?:\{,\}[0-9]{3}(?![0-9]))*(?:\{,\}[0-9]+|\.[0-9]*)?|\.[0-9]+)/,
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
  mathcatProblem = `MathCAT failed to start: ${error?.message ?? error}`;
}

// MathCAT errors come prefixed with their code (see mathcat-wasm/src/lib.rs).
// Anything else is a trap or panic inside the module (e.g. "unreachable").
const CALLER_CODES = new Set(['invalid_mathml', 'invalid_preference']);
const FAILURE_CODES = new Set(['speech_failed', 'braille_failed']);

function mathcatError(error, fallback) {
  const message = String(error?.message ?? error);
  const separator = message.indexOf(':');
  const code = separator > 0 ? message.slice(0, separator) : '';
  const reason = separator > 0 ? message.slice(separator + 1).trim() : '';
  if (CALLER_CODES.has(code)) {
    return new RequestError(400, code, reason || 'rejected');
  }
  if (FAILURE_CODES.has(code)) {
    return new RequestError(500, code, reason || 'MathCAT failed');
  }
  // MathCAT keeps its preferences and expression in global state, which a
  // panic can leave half applied. Stop answering and let the container
  // restart into a clean instance once this response is sent.
  mathcatReady = false;
  mathcatProblem = 'MathCAT trapped; restarting';
  console.error('math-speech: MathCAT trapped, exiting after this response', error);
  const failure = new RequestError(500, fallback, 'MathCAT trapped');
  failure.fatal = true;
  return failure;
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
  } catch (error) {
    throw mathcatError(error, 'speech_failed');
  }
  if (brailleCode) {
    result.braille_code = brailleCode;
    try {
      result.braille = mathcat.braille(mathml, brailleCode);
    } catch (error) {
      // Speech is the main result: keep it and let the caller decide
      // whether missing braille matters. A trap still restarts the sidecar.
      const failure = mathcatError(error, 'braille_failed');
      if (failure.fatal) {
        throw failure;
      }
      result.braille_error = failure.message;
    }
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
      throw new RequestError(503, 'unavailable', mathcatProblem);
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
      if (size > MAX_BODY_BYTES) {
        return; // already answered 413; drain the rest of the upload
      }
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        // Destroying the socket here would reset the connection before the
        // 413 reaches the client, so answer now and keep reading.
        chunks.length = 0;
        reject(new RequestError(413, 'body_too_large', `body exceeds ${MAX_BODY_BYTES} bytes`));
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => {
      if (size > MAX_BODY_BYTES) {
        return;
      }
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

function send(response, status, payload, headers = {}, done) {
  const body = JSON.stringify(payload);
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    ...headers,
  });
  response.end(body, done);
}

const server = http.createServer(async (request, response) => {
  const path = new URL(request.url, 'http://localhost').pathname;
  const handler = ROUTES[`${request.method} ${path}`];
  if (!handler) {
    const allowed = Object.keys(ROUTES)
      .filter((route) => route.endsWith(` ${path}`))
      .map((route) => route.split(' ', 1)[0]);
    if (allowed.length) {
      send(response, 405, { detail: `method_not_allowed: use ${allowed.join(', ')}` }, { allow: allowed.join(', ') });
    } else {
      send(response, 404, { detail: 'not_found: no such route' });
    }
    return;
  }
  try {
    const body = request.method === 'POST' ? await readJson(request) : {};
    send(response, 200, handler(body));
  } catch (error) {
    if (error instanceof RequestError) {
      // After a 413 the rest of the upload is unread; do not keep the
      // connection alive for it.
      const headers = error.status === 413 ? { connection: 'close' } : {};
      const done = error.fatal ? () => process.exit(1) : undefined;
      send(response, error.status, { detail: error.message }, headers, done);
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
