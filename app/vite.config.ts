import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

// App version (package.json) baked in at build time; sent to Sekisho in the
// X-Harahachi-Client header (apk|pwa|web/<version>) for ops analytics.
const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8')) as {
  version?: string;
};

// https://vite.dev/config/
export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version ?? '0.0.0'),
  },
  server: {
    host: true, // dev only: lets a phone on the same Wi-Fi open the dev server
    port: 5173,
  },
  plugins: [
    react(),
    VitePWA({
      // injectManifest so our custom SW (src/sw.ts) can add push +
      // notificationclick handlers on top of the workbox app-shell precache.
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      injectManifest: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
      },
      manifest: {
        name: 'Harahachi 腹八分',
        short_name: 'Harahachi',
        description: 'Photo-first calorie & macro tracker — a journey across a sakura map.',
        theme_color: '#C73E3A',
        background_color: '#F3ECDA',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        icons: [
          { src: 'pwa-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      devOptions: {
        enabled: false,
        type: 'module',
      },
    }),
  ],
});
