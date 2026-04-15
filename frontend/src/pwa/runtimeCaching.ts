import { API_BASE, AUTH, CONSOLE, PROFILE, SYSTEM } from "../utils/api";

export const IDENTITY_SCOPED_API_PATHS = [
  `${API_BASE}${AUTH.session}`,
  `${API_BASE}${SYSTEM.capabilities}`,
  `${API_BASE}${CONSOLE.menu}`,
  `${API_BASE}${CONSOLE.surfaces}`,
  `${API_BASE}${PROFILE.current}`,
] as const;

export const REPLAY_SAFE_BACKGROUND_SYNC_PATHS: readonly string[] = [];

export function isIdentityScopedApiPath(path: string): boolean {
  return IDENTITY_SCOPED_API_PATHS.some((candidate) => path === candidate);
}

// Cache only immutable or user-neutral assets here. Backend-authored control-plane
// state is identity-scoped and must always come from the network.
export const workboxRuntimeCaching = [
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
] as const;
