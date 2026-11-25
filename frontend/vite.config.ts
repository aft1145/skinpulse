import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// 本地开发时把 /api 转给正在跑的服务。服务端对写请求做同源校验（spec §6），所以这里把
// Origin 改写成服务自己的地址，而不是让服务端为开发放宽规则。
const API = 'http://127.0.0.1:8766'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    // 构建产物提交进仓库，由 FastAPI 在同一个端口提供（ADR-0012）
    outDir: '../skinpulse/web/dist',
    emptyOutDir: true,
    target: 'es2020',
    // 不把小文件内联成 data: URI：前端页面的 CSP 是 font-src 'self'，内联字体会被拦（spec §6）
    assetsInlineLimit: 0,
  },
  server: {
    proxy: {
      '/api': {
        target: API,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (req) => {
            if (req.getHeader('origin')) req.setHeader('origin', API)
          })
        },
      },
    },
  },
  test: { environment: 'jsdom' },
})
