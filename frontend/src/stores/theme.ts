import { defineStore } from 'pinia';
import { ref, watch } from 'vue';

const THEME_KEY = 'zen70-theme-preference';
const WALLPAPER_KEY = 'zen70-wallpaper-preference';

export const useThemeStore = defineStore('theme', () => {
  // Available daisyUI themes
  const availableThemes = ['dark', 'light', 'synthwave', 'cyberpunk', 'luxury', 'dracula'];
  
  const currentTheme = ref(localStorage.getItem(THEME_KEY) ?? 'dark');
  const liveWallpaperEnabled = ref(localStorage.getItem(WALLPAPER_KEY) !== 'false');
  const customWallpaperUrl = ref(localStorage.getItem('zen70-custom-wallpaper') ?? '');

  // Apply theme to document
  watch(currentTheme, (newTheme) => {
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem(THEME_KEY, newTheme);
  }, { immediate: true });

  watch(liveWallpaperEnabled, (enabled) => {
    localStorage.setItem(WALLPAPER_KEY, String(enabled));
  });

  function setTheme(theme: string) {
    if (availableThemes.includes(theme)) {
      currentTheme.value = theme;
    }
  }

  function toggleWallpaper() {
    liveWallpaperEnabled.value = !liveWallpaperEnabled.value;
  }

  /** Max wallpaper size: 500KB base64 (~375KB raw image) */
  const MAX_WALLPAPER_SIZE = 500 * 1024;
  const ALLOWED_WALLPAPER_MIMES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

  function setCustomWallpaper(base64: string) {
    // Validate MIME type first (cheap check before size validation).
    const mimeMatch = base64.match(/^data:([^;]+);base64,/);
    if (mimeMatch && !ALLOWED_WALLPAPER_MIMES.includes(mimeMatch[1])) {
      throw new Error("仅支持 PNG / JPEG / WebP / GIF 格式壁纸");
    }
    if (base64.length > MAX_WALLPAPER_SIZE) {
      throw new Error(`壁纸超过 ${MAX_WALLPAPER_SIZE / 1024}KB 限制，请压缩后重试`);
    }
    customWallpaperUrl.value = base64;
    localStorage.setItem('zen70-custom-wallpaper', base64);
  }

  function clearCustomWallpaper() {
    customWallpaperUrl.value = '';
    localStorage.removeItem('zen70-custom-wallpaper');
  }

  return {
    availableThemes,
    currentTheme,
    liveWallpaperEnabled,
    customWallpaperUrl,
    setTheme,
    toggleWallpaper,
    setCustomWallpaper,
    clearCustomWallpaper
  };
});
