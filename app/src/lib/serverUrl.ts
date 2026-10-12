// Normalise a pasted / scanned checkpoint address for pairing (API.md v4).
// The production checkpoint is a stable HTTPS Vercel deployment, so:
//   • trim whitespace, add https:// when no scheme was typed,
//   • keep only the origin (+ any sub-path), dropping a pasted /api/v1, /admin
//     or /pair suffix, query and hash, and trailing slashes,
//   • reject http:// except for local development hosts (localhost, 127.0.0.1,
//     [::1], *.localhost) — the APK only talks to the HTTPS deployment.
// Pure — no DOM access; checked by scripts/v4.check.mjs.

export type NormalizedUrl = { ok: true; url: string } | { ok: false; error: string };

function isLocalHost(host: string): boolean {
  const h = host.toLowerCase();
  return h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h === '::1' || h.endsWith('.localhost');
}

export function normalizeServerUrl(raw: string): NormalizedUrl {
  let s = (raw ?? '').trim().replace(/\s+/g, '');
  if (!s) return { ok: false, error: 'Enter the checkpoint address.' };
  // No scheme typed ("my-app.vercel.app") → assume https. Anything that looks
  // like a non-web scheme is rejected below by the protocol check.
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = 'https://' + s.replace(/^\/+/, '');
  let u: URL;
  try {
    u = new URL(s);
  } catch {
    return { ok: false, error: 'That doesn’t look like a web address.' };
  }
  if (!u.hostname) return { ok: false, error: 'That doesn’t look like a web address.' };
  if (u.protocol === 'http:' && !isLocalHost(u.hostname)) {
    return {
      ok: false,
      error: 'The checkpoint must use a secure https:// address — check the address on the pairing QR.',
    };
  }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') {
    return { ok: false, error: 'The checkpoint address must start with https://.' };
  }
  if (u.username || u.password) return { ok: false, error: 'Remove the user name from the address.' };
  // Drop known suffixes people paste from the admin page or API docs.
  let path = u.pathname.replace(/\/+$/, '');
  path = path.replace(/\/(api\/v1|api|admin|pair)(\/.*)?$/i, '');
  return { ok: true, url: (u.origin + path).replace(/\/+$/, '') };
}
