import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [{name: 'qa-local-runs', configureServer(server) {
    const runs = fileURLToPath(new URL('../../../.shots/video-qa/run/', import.meta.url)).replaceAll('\\', '/');
    server.middlewares.use((req, _res, next) => {
      if (req.url?.startsWith('/runs/')) req.url = `/@fs/${runs}${req.url.slice(6)}`;
      next();
    });
  }}],
  server: { fs: { allow: [fileURLToPath(new URL('../../../../', import.meta.url))] } },
});
