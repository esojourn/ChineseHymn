import { defineConfig } from 'vite';

export default defineConfig({
  // 开发服务器配置
  server: {
    port: 8080,
    open: true,
  },

  // 构建配置
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    // 生成源码映射，便于调试
    sourcemap: false,
    // 压缩选项
    minify: 'esbuild',
    // chunk 大小警告限制
    chunkSizeWarningLimit: 1000,
  },

  // 公共基础路径
  base: './',
});
