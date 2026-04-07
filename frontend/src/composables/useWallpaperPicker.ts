import { ref, type Ref } from "vue";

interface ThemeStoreLike {
  setCustomWallpaper: (value: string) => void;
}

export function useWallpaperPicker(
  themeStore: ThemeStoreLike,
  appNotice: Ref<string>,
  appNoticeLevel: Ref<"info" | "error">
) {
  const wallpaperInput = ref<HTMLInputElement | null>(null);

  function triggerWallpaperUpload() {
    wallpaperInput.value?.click();
  }

  function handleWallpaperUpload(e: Event) {
    const target = e.target as HTMLInputElement;
    const file = target.files?.[0];
    if (!file) return;

    const maxWallpaperBytes = 2 * 1024 * 1024;
    if (file.size > maxWallpaperBytes) {
      appNoticeLevel.value = "error";
      appNotice.value = "壁纸文件过大（上限 2MB），请压缩后再试。";
      return;
    }

    const reader = new FileReader();
    reader.onload = (event) => {
      if (typeof event.target?.result === "string") {
        themeStore.setCustomWallpaper(event.target.result);
      }
    };
    reader.readAsDataURL(file);

    if (wallpaperInput.value) {
      wallpaperInput.value.value = "";
    }
  }

  return {
    handleWallpaperUpload,
    triggerWallpaperUpload,
    wallpaperInput,
  };
}
