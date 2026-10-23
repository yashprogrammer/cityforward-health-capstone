import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5171, proxy: { '/int': 'http://localhost:8001', '/fhir': 'http://localhost:8001', '/smart': 'http://localhost:8001' } },
})
