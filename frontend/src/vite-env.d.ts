/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

// Vue SFC shim — required for bare tsc (without vite) to resolve *.vue imports
declare module "*.vue" {
  import type { DefineComponent } from "vue";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component: DefineComponent<object, object, any>;
  export default component;
}

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
