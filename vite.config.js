import { defineConfig } from 'vite';

export default defineConfig({
  root: '.',
  server: {
    host: true,
    port: 4200,
  },
  build: {
    rollupOptions: {
      input: {
        index: 'index.html',
        fastPathtracer: 'fast-pathtracer.html',
        pathtracer: 'pathtracer.html',
        scroll: 'scroll.html',
      },
    },
  },
});
