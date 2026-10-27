import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'node:path'

const API_TARGET = process.env.VITE_DEV_API_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [
    react(),
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
        // Stage 8: a function, not the `{ charts: ['recharts'] }` list — the list also pulled
        // the helpers Recharts shares with the app (clsx) into the charts chunk, so the main
        // bundle imported and preloaded all of Recharts. Shared helpers now go to `vendor`
        // (a manual chunk takes every dependency not already placed elsewhere), Recharts and
        // what only it uses to `charts`, which loads with /stats.
        manualChunks(id) {
          if (!id.includes('/node_modules/')) return undefined
          if (
            /\/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler|@remix-run\/router|clsx|prop-types|react-is|object-assign|tslib)\//.test(
              id,
            )
          ) {
            return 'vendor'
          }
          if (/\/node_modules\/(framer-motion|motion-dom|motion-utils)\//.test(id)) return 'motion'
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
