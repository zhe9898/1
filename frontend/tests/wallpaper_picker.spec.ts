import { describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import { useWallpaperPicker } from "@/composables/useWallpaperPicker";

describe("useWallpaperPicker", () => {
  it("triggers hidden input click", () => {
    const setCustomWallpaper = vi.fn();
    const appNotice = ref("");
    const appNoticeLevel = ref<"info" | "error">("info");
    const picker = useWallpaperPicker({ setCustomWallpaper }, appNotice, appNoticeLevel);
    const click = vi.fn();

    picker.wallpaperInput.value = { click, value: "" } as unknown as HTMLInputElement;
    picker.triggerWallpaperUpload();

    expect(click).toHaveBeenCalledOnce();
  });

  it("sets error notice when file is larger than 2MB", () => {
    const setCustomWallpaper = vi.fn();
    const appNotice = ref("");
    const appNoticeLevel = ref<"info" | "error">("info");
    const picker = useWallpaperPicker({ setCustomWallpaper }, appNotice, appNoticeLevel);

    const oversized = { size: 3 * 1024 * 1024 } as File;
    picker.handleWallpaperUpload({ target: { files: [oversized] } } as unknown as Event);

    expect(appNoticeLevel.value).toBe("error");
    expect(appNotice.value.length).toBeGreaterThan(0);
    expect(setCustomWallpaper).not.toHaveBeenCalled();
  });

  it("reads valid wallpaper and writes data url to store", () => {
    const setCustomWallpaper = vi.fn();
    const appNotice = ref("");
    const appNoticeLevel = ref<"info" | "error">("info");
    const picker = useWallpaperPicker({ setCustomWallpaper }, appNotice, appNoticeLevel);

    class MockFileReader {
      onload: ((event: ProgressEvent<FileReader>) => void) | null = null;
      readAsDataURL(): void {
        const event = { target: { result: "data:image/png;base64,abc" } } as unknown as ProgressEvent<FileReader>;
        this.onload?.(event);
      }
    }

    vi.stubGlobal("FileReader", MockFileReader);
    picker.wallpaperInput.value = { value: "will-reset" } as unknown as HTMLInputElement;

    const validFile = { size: 1024 } as File;
    picker.handleWallpaperUpload({ target: { files: [validFile] } } as unknown as Event);

    expect(setCustomWallpaper).toHaveBeenCalledWith("data:image/png;base64,abc");
    expect(picker.wallpaperInput.value?.value).toBe("");
  });
});
