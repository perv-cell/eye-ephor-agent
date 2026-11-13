import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Живой ingest/SSE: без cacheComponents / partialPrefetching.
  agentRules: false,
  reactCompiler: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
