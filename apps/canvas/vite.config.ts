import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Set by Node; declared here so the config needs no @types/node.
declare const process: { env: Record<string, string | undefined> }

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // `murakumo canvas` serves the API; in development Vite forwards /api to it (MURAKUMO_API to
    // use another port).
    proxy: { '/api': process.env.MURAKUMO_API ?? 'http://127.0.0.1:8765' },
  },
})
