import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const publicHost = process.env.TESTPILOT_PUBLIC_HOST?.trim()
const apiTarget = process.env.TESTPILOT_API_TARGET?.trim() || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    // Keep Vite's DNS-rebinding protection enabled and allow only the active tunnel hostname.
    allowedHosts: publicHost ? [publicHost] : [],
    proxy: {
      '/api/': {
        target: apiTarget,
        changeOrigin: false,
      },
    },
  },
})
