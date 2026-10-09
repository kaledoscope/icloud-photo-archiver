import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// 开发时前端跑在 5173、后端在 8787。把 /api 转发过去，前端代码里
// 一律用相对路径请求，于是开发和生产（后端直接托管 dist）走同一套写法，
// 不用靠环境变量切 base URL。
const BACKEND = process.env.BACKEND_ORIGIN ?? 'http://127.0.0.1:8787'

export default defineConfig({
  plugins: [svelte()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        // SSE 必须关掉缓冲，否则进度会攒着一起吐出来
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache'
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
  },
})
