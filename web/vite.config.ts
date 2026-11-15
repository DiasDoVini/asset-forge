import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Em dev a UI roda em :5173 e faz proxy da API do Forge (:8765). Em produção o próprio
// `forge serve` serve web/dist.
const api = process.env.FORGE_API ?? 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: api, ws: true },
      '/files': api,
    },
  },
})
