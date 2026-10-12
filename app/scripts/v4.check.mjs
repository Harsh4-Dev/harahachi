// Node checks for the v4 (Vercel + NVIDIA NIM) client helpers — the pure parts
// that don't need a browser: image-size maths (lib/imagePrep), pasted-address
// normalisation (lib/serverUrl) and API-error classification (api/errors).
// The canvas/JPEG pipeline itself runs in the dev server console:
//   await window.__hhImagePrepCheck()
//
// Run from app/:  node --experimental-strip-types scripts/v4.check.mjs
// (Node ≥ 22.6). Sources are copied to a temp dir with Vite-only bits stubbed
// (import.meta.env.DEV → false, extensionless relative imports → .ts).

import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const src = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
const tmp = mkdtempSync(join(tmpdir(), 'hh-v4-'));
const files = {
  'imagePrep.ts': 'lib/imagePrep.ts',
  'serverUrl.ts': 'lib/serverUrl.ts',
  'http.ts': 'api/http.ts',
  'errors.ts': 'api/errors.ts',
};
for (const [out, rel] of Object.entries(files)) {
  const code = readFileSync(join(src, rel), 'utf8')
    .replaceAll('import.meta.env.DEV', 'false')
    .replace(/from '\.\/http'/g, "from './http.ts'");
  writeFileSync(join(tmp, out), code);
}
const load = (f) => import(pathToFileURL(join(tmp, f)).href);

let fails = 0;
function check(name, ok, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
  if (!ok) fails++;
}

try {
  const ip = await load('imagePrep.ts');
  const f = ip.fitWithin(4000, 3000, 1024);
  check('fitWithin 4000x3000 → 1024x768', f.width === 1024 && f.height === 768, JSON.stringify(f));
  const p = ip.fitWithin(3024, 4032, 1024);
  check('fitWithin portrait → 768x1024', p.width === 768 && p.height === 1024, JSON.stringify(p));
  check('fitWithin never upscales', ip.fitWithin(800, 600, 1024).width === 800);
  const ladder = ip.encodeLadder();
  check(
    'encodeLadder: quality 0.82→0.5 at 1024, then edges 896/768/640',
    JSON.stringify(ladder.slice(0, 7)) ===
      JSON.stringify([
        { edge: 1024, quality: 0.82 },
        { edge: 1024, quality: 0.72 },
        { edge: 1024, quality: 0.62 },
        { edge: 1024, quality: 0.5 },
        { edge: 896, quality: 0.5 },
        { edge: 768, quality: 0.5 },
        { edge: 640, quality: 0.5 },
      ]),
    ladder.map((s) => `${s.edge}@${s.quality}`).join(' '),
  );
  check('encodeLadder({maxEdge:768}) starts at 768', ip.encodeLadder(768)[0].edge === 768);
  check('MAX_UPLOAD_BYTES = 170 KB', ip.MAX_UPLOAD_BYTES === 170 * 1024);
  // Overlay mapping: normalized points survive the downscale (sub-pixel only).
  const worst = Math.max(
    ...[[4000, 3000], [3024, 4032], [6000, 1500], [1920, 1080], [4031, 3023], [1001, 999]].flatMap(([w, h]) =>
      [1024, 896, 768, 640].map((e) => ip.pointMappingDrift(w, h, e)),
    ),
  );
  check('point mapping drift < 0.1% across sizes/edges', worst < 0.001, `worst ${worst.toExponential(2)}`);

  const su = await load('serverUrl.ts');
  const n = (s) => su.normalizeServerUrl(s);
  const cases = [
    ['  harahachi.vercel.app  ', 'https://harahachi.vercel.app'],
    ['https://harahachi.vercel.app/', 'https://harahachi.vercel.app'],
    ['https://harahachi.vercel.app///', 'https://harahachi.vercel.app'],
    ['HTTPS://Harahachi.Vercel.App/admin', 'https://harahachi.vercel.app'],
    ['https://harahachi.vercel.app/api/v1/', 'https://harahachi.vercel.app'],
    ['https://harahachi.vercel.app/?x=1#y', 'https://harahachi.vercel.app'],
    ['http://localhost:8000', 'http://localhost:8000'],
    ['http://127.0.0.1:8000/', 'http://127.0.0.1:8000'],
    ['http://[::1]:8000', 'http://[::1]:8000'],
    ['http://app.localhost:5173', 'http://app.localhost:5173'],
  ];
  for (const [inp, want] of cases) {
    const r = n(inp);
    check(`normalize ${JSON.stringify(inp)}`, r.ok && r.url === want, r.ok ? r.url : r.error);
  }
  for (const bad of ['http://harahachi.vercel.app', 'http://192.168.137.1', '', '   ', 'ftp://x.example', 'https://']) {
    const r = n(bad);
    check(`reject ${JSON.stringify(bad)}`, !r.ok, r.ok ? r.url : r.error);
  }
  check('http:// rejection message mentions https', /https/.test(n('http://example.com').error ?? ''));

  const http = await load('http.ts');
  const er = await load('errors.ts');
  let revoked = 0;
  let noUser = 0;
  http.setDeviceRevokedHandler(() => revoked++);
  http.setNoUserHandler(() => noUser++);
  const mk = (status, code) => http.apiErrorFrom(status, code ? { error: { code, message: 'x' } } : null);
  check('0 → network', er.classifyApiError(new http.ApiError('timeout', 't', 0)) === 'network');
  check('413 (html body) → too_large', er.classifyApiError(mk(413)) === 'too_large');
  check('429 daily_cap → daily_cap', er.classifyApiError(mk(429, 'daily_cap')) === 'daily_cap');
  check('429 llm_daily_limit → daily_cap', er.classifyApiError(mk(429, 'llm_daily_limit')) === 'daily_cap');
  check('429 rate_limited → rate_limited', er.classifyApiError(mk(429, 'rate_limited')) === 'rate_limited');
  check('503 → unavailable', er.classifyApiError(mk(503, 'provider_unavailable')) === 'unavailable');
  const before = revoked;
  check('401 invalid_key → device_revoked', er.classifyApiError(mk(401, 'invalid_key')) === 'device_revoked');
  check('401 invalid_key fires the revoked hook', revoked === before + 1);
  const r2 = revoked;
  mk(401, 'bad_credentials');
  check('401 bad_credentials does NOT fire revoked hook', revoked === r2);
  mk(401, 'no_user');
  check('401 no_user fires the no-user hook only', noUser === 1 && revoked === r2);
  const copy = Object.values(er.AI_COPY).join(' ');
  check('AI copy never mentions mock/vllm/nvidia', !/mock|vllm|nvidia|nim\b/i.test(copy));
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

console.log(fails ? `\n${fails} FAILED` : '\nall passed');
process.exit(fails ? 1 : 0);
