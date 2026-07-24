import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    // Streamlit loads the bundle from the declare_component path.
    // Relative asset paths work when the dist/ is committed to the repo.
    base: "./",
  },
});
