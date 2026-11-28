import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const root = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
    base: "/static/build/",
    publicDir: false,
    build: {
        outDir: resolve(root, "../src/glowbug/web/static/build"),
        emptyOutDir: true,
        cssCodeSplit: false,
        target: "es2022",
        rollupOptions: {
            input: resolve(root, "src/dashboard.js"),
            output: {
                entryFileNames: "dashboard.js",
                chunkFileNames: "chunks/[name]-[hash].js",
                assetFileNames: "dashboard.[ext]",
            },
        },
    },
});
