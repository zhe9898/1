import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { VitePWA } from "vite-plugin-pwa";
import { resolve } from "path";

export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg"],
      manifest: {
        name: "ZEN70 家庭数字堡垒",
        short_name: "ZEN70",
        description: "极客私有云，物理防腐，跨端平权",
        display: "standalone",
        start_url: "/",
        theme_color: "#1e1e2f",
        background_color: "#12121a",
        icons: [
          { src: "/favicon.svg", sizes: "any", type: "image/svg+xml" },
          { src: "/pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "/pwa-512x512.png", sizes: "512x512", type: "image/png" },
          { src: "/pwa-512x512.png", sizes: "512x512", type: "image/png", purpose: "any maskable" }
        ],
      },
      workbox: {
        skipWaiting: true,
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
        navigateFallback: null,
        navigateFallbackDenylist: [/^\/api/],
        runtimeCaching: [
          {
            urlPattern: /\.(?:mp4|webm|m3u8|ts)$/i,
            handler: "CacheFirst",
            options: {
              rangeRequests: true,
              cacheName: "zen70-media-cache",
              expiration: { maxEntries: 30, maxAgeSeconds: 86400 },
              cacheableResponse: { statuses: [0, 200, 206] },
            },
          },
          {
            urlPattern: /\/api\/v1\/capabilities/i,
            handler: "NetworkFirst",
            options: {
              cacheName: "zen70-api-capabilities",
              expiration: { maxEntries: 10, maxAgeSeconds: 300 },
              networkTimeoutSeconds: 5,
              cacheableResponse: { statuses: [0, 200], headers: { 'content-type': 'application/json' } },
            },
          },
          {
            urlPattern: /\/api\/.*/i,
            handler: "NetworkFirst",
            options: {
              cacheName: "zen70-api-cache",
              expiration: { maxEntries: 50 },
              networkTimeoutSeconds: 5,
              cacheableResponse: { statuses: [0, 200], headers: { 'content-type': 'application/json' } },
            },
          },
          {
            urlPattern: /\/api\/.*/i,
            handler: "NetworkOnly",
            method: "POST",
            options: {
              backgroundSync: {
                name: "zen70-offline-sync-queue",
                options: {
                  maxRetentionTime: 24 * 60, // Retry for up to 24 hours
                },
              },
            },
          },
          {
            urlPattern: /\/api\/.*/i,
            handler: "NetworkOnly",
            method: "PUT",
            options: {
              backgroundSync: {
                name: "zen70-offline-sync-queue",
                options: {
                  maxRetentionTime: 24 * 60,
                },
              },
            },
          },
          {
            urlPattern: /\/api\/.*/i,
            handler: "NetworkOnly",
            method: "DELETE",
            options: {
              backgroundSync: {
                name: "zen70-offline-sync-queue",
                options: {
                  maxRetentionTime: 24 * 60,
                },
              },
            },
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  build: {
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
