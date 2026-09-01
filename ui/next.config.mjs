/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emit `.next/standalone`: a server with only the modules its pages actually load traced into it.
  // Read by the container image alone — `npm run dev` and `npm run start` are unaffected — and the
  // reason it is here is that copying node_modules into the image instead costs 400MB of platform
  // binaries the server never opens.
  output: 'standalone',
  // Every page reads the service through `?weeks=`, so a cached RSC payload for a dynamic route
  // would serve the previous window's numbers after the selector changes it. Zero stale time forces
  // a server round-trip on each navigation.
  experimental: {
    staleTimes: {
      dynamic: 0,
    },
  },
};

export default nextConfig;
