import type { NextConfig } from "next";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  // Dev-proxy body cap defaults to 10 MB; raise it so /api/media/upload
  // can accept real videos. (Renamed from middlewareClientMaxBodySize in
  // this Next version per node_modules/next/dist/docs/.../codemods.md.)
  experimental: {
    proxyClientMaxBodySize: "1gb",
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
