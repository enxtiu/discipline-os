import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'node:path'

const API_TARGET = process.env.VITE_DEV_API_TARGET ?? 'http://127.0.0.1:8000'

/**
 * Stage 9 (performance): each locale is a chunk of its own (src/i18n/index.ts loads
 * only the active one). This puts a tiny inline script into the built index.html
 * that preloads the locale the device will ask for — the language i18next detects
 * (stored on the device, else the browser's, else Russian) — so it downloads in
 * parallel with the app code instead of after it. A wrong guess (the server's
 * language differs) only costs that preload: the app then loads the other locale.
 */
function localePreload(): Plugin {
  return {
    name: 'dsos-locale-preload',
    apply: 'build',
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        const files: Record<string, string> = {}
        for (const chunk of Object.values(ctx.bundle ?? {})) {
          const match = chunk.type === 'chunk' && chunk.facadeModuleId?.match(/\/i18n\/locales\/(ru|en)\.json$/)
          if (match) files[match[1]] = `/${chunk.fileName}`
        }
        if (!files.ru || !files.en) throw new Error('localePreload: the ru/en locale chunks were not found')
        // the stored language, else the first ru/en among the browser's languages, else ru
        const script = `(function(){var f=${JSON.stringify(files)},l='ru';try{var c=[localStorage.getItem('dsos.language')].concat(navigator.languages||[navigator.language]);for(var i=0;i<c.length;i++){var x=String(c[i]||'').slice(0,2).toLowerCase();if(x==='ru'||x==='en'){l=x;break}}}catch(e){}var k=document.createElement('link');k.rel='modulepreload';k.href=f[l];document.head.appendChild(k)})()`
        return [{ tag: 'script', children: script, injectTo: 'head-prepend' }]
      },
    },
  }
}

export default defineConfig({
  plugins: [
    react(),
    localePreload(),
    VitePWA({
      strategies: 'generateSW',
      registerType: 'prompt',
      injectRegister: null,
      filename: 'sw.js',
      includeAssets: ['icons/apple-touch-icon.png', 'icons/favicon.svg', 'offline.html'],
      manifest: {
        name: 'DisciplineOS',
        short_name: 'Discipline',
        description:
          'Personal discipline tracker: nutrition, training, steps, PARA tasks, time tracking, gamification.',
        lang: 'ru',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        display_override: ['standalone', 'minimal-ui'],
        orientation: 'portrait',
        background_color: '#0B1220',
        theme_color: '#0B1220',
        categories: ['health', 'productivity', 'lifestyle'],
        icons: [
          { src: 'icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
        shortcuts: [
          { name: 'Add meal', short_name: 'Meal', url: '/food?action=add' },
          { name: 'Start workout', short_name: 'Train', url: '/train?action=start' },
          { name: 'Today', short_name: 'Today', url: '/today' },
        ],
      },
      workbox: {
        // stage 7: web push handlers (public/sw-push.js) — the built dist/sw.js starts
        // with importScripts("sw-push.js"), so push / notificationclick live in the same SW
        importScripts: ['sw-push.js'],
        globPatterns: ['**/*.{js,css,html,svg,png,woff2,webmanifest}'],
        navigateFallback: '/index.html',
        // never cache API traffic — the app has its own offline queue
        navigateFallbackDenylist: [/^\/api/],
        runtimeCaching: [
          {
            urlPattern: /^\/api\//,
            handler: 'NetworkOnly',
          },
          {
            urlPattern: /\.(?:png|jpg|jpeg|svg|webp|woff2)$/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'dsos-assets',
              expiration: { maxEntries: 120, maxAgeSeconds: 60 * 60 * 24 * 30 },
            },
          },
        ],
      },
      devOptions: { enabled: false },
    }),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  // the locales are big flat objects: JSON.parse reads them faster than an object literal
  json: { stringify: true },
  server: {
    host: true,
    port: 5173,
    // Same-origin API in production (Caddy) — the dev server proxies so the
    // frontend code always uses relative "/api" URLs.
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
    },
  },
  preview: {
    host: true,
    port: 4173,
  },
  build: {
    target: 'es2022',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // Fixed chunks for libraries that change far less often than the app (an update
        // then re-downloads the app chunk only): React and the small helpers the app shares
        // with other libraries (`vendor`) and Dexie (`dexie` — the offline queue opens its
        // IndexedDB before the first screen), both loaded at start; Recharts and what only
        // it uses (`charts`, loaded with /stats). A function, not lists: a list also takes
        // every dependency not placed elsewhere, so `{ charts: ['recharts'] }` pulled clsx
        // (and with it the whole charts chunk) into the start-up path. framer-motion is left
        // to the automatic split on purpose: `m` and LazyMotion load at start, the animation
        // features lazily (src/lib/motion.tsx) — a `motion` chunk would bring them all back.
        manualChunks(id) {
          if (!id.includes('/node_modules/')) return undefined
          if (
            /\/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler|@remix-run\/router|clsx|prop-types|react-is|object-assign|tslib)\//.test(
              id,
            )
          ) {
            return 'vendor'
          }
          if (/\/node_modules\/dexie\//.test(id)) return 'dexie'
          if (
            /\/node_modules\/(recharts|recharts-scale|react-smooth|victory-vendor|d3-[a-z-]+|internmap|decimal\.js-light|lodash|eventemitter3|tiny-invariant|fast-equals|react-transition-group|dom-helpers)\//.test(
              id,
            )
          ) {
            return 'charts'
          }
          return undefined
        },
      },
    },
  },
})
