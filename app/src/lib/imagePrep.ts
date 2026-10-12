// Client-side image downscaling before EVERY photo upload (dish analyze,
// body-photo estimate, gallery picks). API.md v4: the backend runs on Vercel
// and forwards images inline (base64) to NVIDIA NIM, which rejects large
// inline images; Vercel also caps function request bodies. So the phone sends
// a small, upright, EXIF-free JPEG and the server re-encodes only as a safety
// net.
//
// Pipeline:
//   1. Decode with createImageBitmap(blob, { imageOrientation: 'from-image' })
//      so EXIF-rotated phone photos come out upright (fallback: plain
//      createImageBitmap, then an <img> element, which browsers also orient).
//   2. Scale so the long edge is ≤ 1024 px (never upscale).
//   3. Encode JPEG at quality 0.82 → 0.72 → 0.62 → 0.5; if still over budget,
//      step the long edge 896 → 768 → 640 (at the lowest quality) until the
//      blob is ≤ 170 KB. A last-ditch 0.4 / 0.3 pass at 640 px follows; if even
//      that is over, the smallest attempt is returned (the server re-encodes).
//
// Overlay safety: only the upload is downscaled. Callers keep the full-res
// frame for the on-screen frozen preview. The scale is uniform (same aspect
// ratio, same orientation) so the model's normalized 0..1 item points map onto
// the full-res preview unchanged — see `pointMappingDrift()` + the checks.
//
// Dev checks: in the dev server console run `await window.__hhImagePrepCheck()`
// (registered in DEV only), or from app/: `node --experimental-strip-types
// scripts/v4.check.mjs` for the pure maths.

export const MAX_UPLOAD_BYTES = 170 * 1024;
export const QUALITY_STEPS = [0.82, 0.72, 0.62, 0.5] as const;
export const EDGE_STEPS = [1024, 896, 768, 640] as const;
const LAST_DITCH_QUALITIES = [0.4, 0.3] as const;

export interface PrepOptions {
  maxBytes?: number; // default 170 KB
  maxEdge?: number; // default 1024 — the ladder starts at the first step ≤ this
}

export interface PreparedImage {
  blob: Blob; // image/jpeg, upright, no EXIF
  width: number;
  height: number;
  sourceWidth: number; // decoded (orientation-applied) source size
  sourceHeight: number;
  quality: number;
  bytes: number;
  attempts: number;
  fits: boolean; // bytes ≤ maxBytes
}

// ---------- pure helpers (unit-checkable in node) ----------

// Scale (w×h) so the long edge is ≤ maxEdge. Never upscales. Integer output.
export function fitWithin(w: number, h: number, maxEdge: number): { width: number; height: number } {
  if (!(w > 0) || !(h > 0)) return { width: 0, height: 0 };
  const long = Math.max(w, h);
  if (long <= maxEdge) return { width: Math.round(w), height: Math.round(h) };
  const s = maxEdge / long;
  return { width: Math.max(1, Math.round(w * s)), height: Math.max(1, Math.round(h * s)) };
}

// The ordered (edge, quality) attempts the encoder walks. Pure — exported so
// the ladder itself is testable.
export function encodeLadder(maxEdge: number = EDGE_STEPS[0]): { edge: number; quality: number }[] {
  const edges: number[] = EDGE_STEPS.filter((e) => e <= maxEdge);
  if (edges.length === 0) edges.push(Math.max(1, Math.round(maxEdge)));
  const out: { edge: number; quality: number }[] = [];
  // Quality first at the largest edge…
  for (const q of QUALITY_STEPS) out.push({ edge: edges[0], quality: q });
  // …then the edge, at the lowest quality.
  const qMin = QUALITY_STEPS[QUALITY_STEPS.length - 1];
  for (const e of edges.slice(1)) out.push({ edge: e, quality: qMin });
  const last = edges[edges.length - 1];
  for (const q of LAST_DITCH_QUALITIES) out.push({ edge: last, quality: q });
  return out;
}

// Worst-case error (normalized 0..1 units) when a point the model reports on
// the downscaled upload is drawn on the full-res preview. The scale is uniform,
// so normalized coordinates are identical except for integer rounding of the
// downscaled size: ≤ ½px per axis, i.e. < 0.001 at a 640px+ edge.
export function pointMappingDrift(srcW: number, srcH: number, maxEdge: number): number {
  const { width, height } = fitWithin(srcW, srcH, maxEdge);
  if (!width || !height) return 0;
  const s = Math.min(1, maxEdge / Math.max(srcW, srcH));
  const ex = Math.abs(width - srcW * s) / width;
  const ey = Math.abs(height - srcH * s) / height;
  return Math.max(ex, ey);
}

// ---------- browser pipeline ----------

interface Decoded {
  source: CanvasImageSource;
  width: number;
  height: number;
  close(): void;
}

async function decodeImage(blob: Blob): Promise<Decoded> {
  if (typeof createImageBitmap === 'function') {
    // Preferred: honour EXIF orientation explicitly.
    try {
      const bmp = await createImageBitmap(blob, { imageOrientation: 'from-image' });
      return { source: bmp, width: bmp.width, height: bmp.height, close: () => bmp.close() };
    } catch {
      /* option unsupported (older Safari) — try without */
    }
    try {
      const bmp = await createImageBitmap(blob);
      return { source: bmp, width: bmp.width, height: bmp.height, close: () => bmp.close() };
    } catch {
      /* fall through to <img> */
    }
  }
  // <img> fallback: browsers apply EXIF orientation by default
  // (image-orientation: from-image), and naturalWidth/Height are oriented.
  const url = URL.createObjectURL(blob);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error('Could not decode the photo.'));
      el.src = url;
    });
    return {
      source: img,
      width: img.naturalWidth,
      height: img.naturalHeight,
      close: () => URL.revokeObjectURL(url),
    };
  } catch (e) {
    URL.revokeObjectURL(url);
    throw e;
  }
}

function toJpeg(canvas: HTMLCanvasElement, quality: number): Promise<Blob | null> {
  return new Promise((res) => canvas.toBlob((b) => res(b), 'image/jpeg', quality));
}

// Downscale + re-encode a photo for upload. Always re-encodes (strips EXIF —
// including GPS — and bakes orientation into the pixels).
export async function prepareImageForUpload(input: Blob, opts: PrepOptions = {}): Promise<PreparedImage> {
  const maxBytes = opts.maxBytes ?? MAX_UPLOAD_BYTES;
  const maxEdge: number = opts.maxEdge ?? EDGE_STEPS[0];
  const dec = await decodeImage(input);
  try {
    if (!dec.width || !dec.height) throw new Error('Could not decode the photo.');
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas unavailable.');

    let best: PreparedImage | null = null;
    let drawnEdge = -1;
    let attempts = 0;
    for (const step of encodeLadder(maxEdge)) {
      const { width, height } = fitWithin(dec.width, dec.height, step.edge);
      if (step.edge !== drawnEdge) {
        canvas.width = width;
        canvas.height = height;
        // White matte: transparent PNGs would otherwise encode black.
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, width, height);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(dec.source, 0, 0, width, height);
        drawnEdge = step.edge;
      }
      const blob = await toJpeg(canvas, step.quality);
      attempts++;
      if (!blob) continue;
      const cand: PreparedImage = {
        blob,
        width,
        height,
        sourceWidth: dec.width,
        sourceHeight: dec.height,
        quality: step.quality,
        bytes: blob.size,
        attempts,
        fits: blob.size <= maxBytes,
      };
      if (cand.fits) {
        recordUpload(cand);
        return cand;
      }
      if (!best || cand.bytes < best.bytes) best = cand;
    }
    if (!best) throw new Error('Could not encode the photo.');
    best.attempts = attempts;
    recordUpload(best);
    return best;
  } finally {
    dec.close();
  }
}

// DEV-only breadcrumb so the dev-server console (and the verification run) can
// inspect exactly what was uploaded. Stripped from production builds.
function recordUpload(p: PreparedImage): void {
  if (import.meta.env.DEV) {
    (window as unknown as { __hhLastUpload?: PreparedImage }).__hhLastUpload = p;
  }
}

// ---------- dev-console checks ----------

function synthCanvas(w: number, h: number, seed: number): HTMLCanvasElement {
  // A deliberately hard-to-compress image: gradients + noise + shapes, so the
  // ladder actually has to work (a flat canvas would compress to a few KB).
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const ctx = c.getContext('2d')!;
  const g = ctx.createLinearGradient(0, 0, w, h);
  g.addColorStop(0, `hsl(${seed * 47 % 360} 70% 55%)`);
  g.addColorStop(1, `hsl(${(seed * 47 + 160) % 360} 60% 35%)`);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  const img = ctx.getImageData(0, 0, w, h);
  let s = seed * 9301 + 49297;
  for (let i = 0; i < img.data.length; i += 4) {
    s = (s * 9301 + 49297) % 233280;
    const n = (s / 233280 - 0.5) * 90;
    img.data[i] += n;
    img.data[i + 1] += n;
    img.data[i + 2] += n;
  }
  ctx.putImageData(img, 0, 0);
  for (let k = 0; k < 40; k++) {
    ctx.fillStyle = `hsla(${(k * 37 + seed * 11) % 360} 80% 50% / 0.6)`;
    ctx.beginPath();
    ctx.arc(((k * 7919) % w), ((k * 104729) % h), (w / 30) * (1 + (k % 4)), 0, Math.PI * 2);
    ctx.fill();
  }
  return c;
}

// Per-pixel seeded noise at full amplitude: the worst case for JPEG, forcing
// the quality AND edge ladder to engage.
function noiseCanvas(w: number, h: number, seed: number): HTMLCanvasElement {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const ctx = c.getContext('2d')!;
  const img = ctx.createImageData(w, h);
  let s = seed >>> 0 || 1;
  for (let i = 0; i < img.data.length; i += 4) {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    img.data[i] = s & 255;
    img.data[i + 1] = (s >>> 8) & 255;
    img.data[i + 2] = (s >>> 16) & 255;
    img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

export async function makeSyntheticPhoto(
  w: number,
  h: number,
  seed = 1,
  quality = 0.95,
  kind: 'photo' | 'noise' = 'photo',
): Promise<Blob> {
  const c = kind === 'noise' ? noiseCanvas(w, h, seed) : synthCanvas(w, h, seed);
  const b = await new Promise<Blob | null>((res) => c.toBlob(res, 'image/jpeg', quality));
  if (!b) throw new Error('toBlob failed');
  return b;
}

// Splice a minimal EXIF APP1 (Orientation tag only) into a JPEG right after
// SOI — simulates a phone photo stored sideways with an orientation flag.
export async function withExifOrientation(jpeg: Blob, orientation: number): Promise<Blob> {
  const buf = new Uint8Array(await jpeg.arrayBuffer());
  const tiff = [
    0x4d, 0x4d, 0x00, 0x2a, 0x00, 0x00, 0x00, 0x08, // big-endian TIFF, IFD at 8
    0x00, 0x01, // 1 entry
    0x01, 0x12, 0x00, 0x03, 0x00, 0x00, 0x00, 0x01, 0x00, orientation & 0xff, 0x00, 0x00, // Orientation SHORT
    0x00, 0x00, 0x00, 0x00, // no next IFD
  ];
  const payload = [0x45, 0x78, 0x69, 0x66, 0x00, 0x00, ...tiff]; // the "Exif" + 2 NUL header
  const len = payload.length + 2;
  const app1 = [0xff, 0xe1, (len >> 8) & 0xff, len & 0xff, ...payload];
  const out = new Uint8Array(buf.length + app1.length);
  out.set(buf.subarray(0, 2), 0);
  out.set(app1, 2);
  out.set(buf.subarray(2), 2 + app1.length);
  return new Blob([out], { type: 'image/jpeg' });
}

export interface CheckRow {
  name: string;
  source: string;
  inBytes: number;
  out: string;
  outBytes: number;
  quality: number;
  attempts: number;
  drift: number;
  pass: boolean;
}

// Runs the pipeline on three synthetic photos and asserts the contract.
export async function runImagePrepChecks(): Promise<CheckRow[]> {
  const cases: [string, number, number, number, 'photo' | 'noise'][] = [
    ['landscape 4000x3000', 4000, 3000, 1, 'photo'],
    ['portrait 3024x4032', 3024, 4032, 2, 'photo'],
    ['panorama 6000x1500', 6000, 1500, 3, 'photo'],
    ['worst-case noise 1024x1024', 1024, 1024, 4, 'noise'],
  ];
  const rows: CheckRow[] = [];
  for (const [name, w, h, seed, kind] of cases) {
    const src = await makeSyntheticPhoto(w, h, seed, 0.95, kind);
    const p = await prepareImageForUpload(src);
    const ratioIn = w / h;
    const ratioOut = p.width / p.height;
    const drift = pointMappingDrift(w, h, Math.max(p.width, p.height));
    rows.push({
      name,
      source: `${w}x${h}`,
      inBytes: src.size,
      out: `${p.width}x${p.height}`,
      outBytes: p.bytes,
      quality: p.quality,
      attempts: p.attempts,
      drift,
      pass:
        p.fits &&
        p.bytes <= MAX_UPLOAD_BYTES &&
        Math.max(p.width, p.height) <= 1024 &&
        p.blob.type === 'image/jpeg' &&
        Math.abs(ratioIn - ratioOut) / ratioIn < 0.005 &&
        drift < 0.005,
    });
  }
  // EXIF orientation: a 4000x3000 sensor image flagged "rotate 90° CW" (6)
  // must come out upright as a portrait 768x1024.
  {
    const raw = await makeSyntheticPhoto(4000, 3000, 5);
    const src = await withExifOrientation(raw, 6);
    const p = await prepareImageForUpload(src);
    rows.push({
      name: 'EXIF orientation 6 (4000x3000 stored)',
      source: '4000x3000 +exif6',
      inBytes: src.size,
      out: `${p.width}x${p.height}`,
      outBytes: p.bytes,
      quality: p.quality,
      attempts: p.attempts,
      drift: 0,
      pass: p.width === 768 && p.height === 1024 && p.fits,
    });
  }
  // Pure checks.
  const f = fitWithin(4000, 3000, 1024);
  const pureOk =
    f.width === 1024 && f.height === 768 &&
    fitWithin(800, 600, 1024).width === 800 && // never upscale
    encodeLadder()[0].quality === 0.82 && encodeLadder()[0].edge === 1024 &&
    encodeLadder().some((s) => s.edge === 640);
  rows.push({
    name: 'pure: fitWithin/encodeLadder',
    source: '-',
    inBytes: 0,
    out: '-',
    outBytes: 0,
    quality: 0,
    attempts: 0,
    drift: 0,
    pass: pureOk,
  });
  return rows;
}

if (import.meta.env.DEV && typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).__hhImagePrepCheck = async () => {
    const rows = await runImagePrepChecks();
    console.table(rows);
    return rows;
  };
  (window as unknown as Record<string, unknown>).__hhMakeSyntheticPhoto = makeSyntheticPhoto;
}
