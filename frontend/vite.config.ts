import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';

// Two front-ends live side by side so the new shadcn-based UI can be built up
// incrementally while the original viewer keeps working:
//   index.html -> src/main.tsx   (the original plain-CSS viewer)
//   v2.html    -> src/v2/main.tsx (the new production-engineer workspace)
// Tailwind only touches CSS that `@import "tailwindcss"`, which is v2-only, so
// the original app's styling is untouched.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        v2: resolve(__dirname, 'v2.html'),
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
});
