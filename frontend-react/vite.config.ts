import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  // 默认指向 FastAPI；仅在显式覆盖时才访问其他后端地址。
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8090';

  return {
    plugins: [react()],
    server: {
      port: 5178,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true
        }
      }
    }
  };
});
