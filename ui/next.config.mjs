/** @type {import('next').NextConfig} */
const nextConfig = {
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
