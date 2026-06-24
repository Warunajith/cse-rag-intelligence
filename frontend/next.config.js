/** @type {import('next').NextConfig} */
const API_BASE = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  // Proxy /api/* to the FastAPI backend so the browser hits same-origin and
  // SSE streaming works without CORS headaches. In production, point
  // BACKEND_URL at the internal service address.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_BASE}/:path*` }];
  },
};

module.exports = nextConfig;
