<template>
  <div
    v-if="themeStore.liveWallpaperEnabled"
    class="fixed inset-0 z-[-1] pointer-events-none overflow-hidden"
  >
    <!-- 自定义壁纸渲染 (M10.2) -->
    <div
      v-if="themeStore.customWallpaperUrl" 
      class="absolute inset-0 bg-cover bg-center transition-opacity duration-1000"
      :style="{ backgroundImage: `url(${themeStore.customWallpaperUrl})` }"
    />
    
    <div class="absolute inset-0 bg-base-300 opacity-80 mix-blend-multiply backdrop-blur-[2px]" />
    
    <!-- 默认流体星云动画 (仅在没有自定义壁纸时显示) -->
    <template v-if="!themeStore.customWallpaperUrl">
      <div class="blob w-96 h-96 absolute bg-primary opacity-30 rounded-full blur-3xl mix-blend-screen animate-blob top-10 left-10" />
      <div class="blob w-96 h-96 absolute bg-secondary opacity-30 rounded-full blur-3xl mix-blend-screen animate-blob animation-delay-2000 top-40 right-10" />
      <div class="blob w-[30rem] h-[30rem] absolute bg-accent opacity-20 rounded-full blur-3xl mix-blend-screen animate-blob animation-delay-4000 -bottom-20 left-1/3" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { useThemeStore } from '@/stores/theme';
const themeStore = useThemeStore();
</script>

<style scoped>
@keyframes blob {
  0% { transform: translate(0px, 0px) scale(1); }
  33% { transform: translate(30px, -50px) scale(1.1); }
  66% { transform: translate(-20px, 20px) scale(0.9); }
  100% { transform: translate(0px, 0px) scale(1); }
}

.animate-blob {
  animation: blob 15s infinite alternate ease-in-out;
}

.animation-delay-2000 {
  animation-delay: 2s;
}

.animation-delay-4000 {
  animation-delay: 4s;
}
</style>
