import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Served by the Copilot service behind the hospital gateway under /ai (the gateway strips the prefix, so the API lives at /ai/api/*).
export default defineConfig({
  base: '/ai/',
  plugins: [react()],
  server: { port: 5174, proxy: { '/ai': { target: 'http://localhost:8001', changeOrigin: false } } },
})
