import type { NextConfig } from "next";
const config: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  turbopack: { root: process.cwd() },
  poweredByHeader: false,
  experimental: { cpus: 1, staticGenerationMaxConcurrency: 1 },
};
export default config;
