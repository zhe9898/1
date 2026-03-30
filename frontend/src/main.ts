import { createApp, type Component } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import { router } from "./router";
import "./style.css";
import { registerSW } from "virtual:pwa-register";

// 初始化 PWA Service Worker 确保离线秒开体验
registerSW({ immediate: true });

// 持久化申请在 App.vue onMounted 中调用，便于控制台查看结果

const app = createApp(App as Component);
app.use(createPinia());
app.use(router);
app.mount("#app");
