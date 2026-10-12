import type { CapacitorConfig } from '@capacitor/cli';

// Harahachi Android shell (see README "Building the APK"). The web build in
// dist/ is copied into the native project by `npx cap sync android`.
const config: CapacitorConfig = {
  appId: 'com.harahachi.app',
  appName: 'Harahachi',
  webDir: 'dist',
  server: {
    // https scheme gives the WebView a secure context, which getUserMedia
    // (camera) requires. The app talks to Sekisho over the https:// Vercel
    // address stored at pairing, never same-origin, so this scheme does not
    // affect API calls (the server's CORS allows https://localhost).
    androidScheme: 'https',
  },
};

export default config;
