// App bootstrap: apply persisted glass alpha, decide the initial phase
// (onboarding → pairing → ready), and mount the shell inside AppProvider.

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { AppProvider, type Phase } from './state/AppContext';
import { ErrorBoundary } from './components/ErrorBoundary';
import { registerSW } from 'virtual:pwa-register';
import { applyGlassAlpha, getActiveUser, getPairing, isDemoEnabled, isNativeShell, isOnboarded, setActiveUser } from './api/storage';
import './styles/global.css';
import './styles/map.css';
import './styles/components.css';
import './styles/capture.css';
import './styles/onboarding.css';
import './styles/account.css';

// Persisted user transparency must apply before first paint (DESIGN.md §2).
applyGlassAlpha();

// Mark the Capacitor/Android WebView so the stylesheet can cap backdrop-filter
// blur and drop it from small transient chips — the WebView pays a steep
// compositor cost for backdrop-filter that desktop browsers do not.
if (isNativeShell()) {
  document.documentElement.classList.add('native-shell');
}

// Register the service worker (app-shell precache + push handlers). Auto-updates.
registerSW({ immediate: true });

// VITE_DEMO=1 short-circuits straight into a stubbed session.
const demoEnv = import.meta.env.VITE_DEMO === '1';

// If the build was started with VITE_DEMO=1, flip on demo persistence + a fake
// account so the AppProvider picks the demo client and lands on the map.
if (demoEnv && !isDemoEnabled()) {
  localStorage.setItem('hh.demo', '1');
}
if ((demoEnv || isDemoEnabled()) && !getActiveUser()) {
  // The demo account is a keeper (admin) so the Checkpoint keeper surface is
  // reachable straight from a demo boot (matches the demo client + startDemo).
  setActiveUser({ id: 'demo-user', username: 'traveler', is_admin: true });
}

function initialPhase(): Phase {
  // Demo always has an account (set above) → straight to the map.
  if (demoEnv || isDemoEnabled()) return 'ready';
  if (!isOnboarded()) return 'onboarding';
  if (!getPairing()) return 'pairing';
  // Paired but no active account → the login/create screen (accounts v2).
  if (!getActiveUser()) return 'login';
  return 'ready';
}

const root = document.getElementById('root')!;
createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <AppProvider initialPhase={initialPhase()}>
        <App />
      </AppProvider>
    </ErrorBoundary>
  </StrictMode>,
);
