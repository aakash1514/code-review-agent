import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Local dev mirrors production's single origin: the dashboard calls relative
// URLs (/api/..., /auth/..., /ws/...), and the dev server forwards them to
// the backend, the way CloudFront forwards them to the ALB on AWS.
const BACKEND = 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': BACKEND,
      '/auth': BACKEND,
      '/ws': { target: BACKEND, ws: true },
    },
  },
})