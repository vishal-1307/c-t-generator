import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next's dev server refuses to serve its own chunks to an origin it does
  // not recognise, which silently breaks hydration (the page renders, but no
  // client effect ever runs and no API call is made). Developers reach the
  // dev server by both spellings of the loopback host, so both are allowed
  // here. Dev-only setting - it has no effect on a production build.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
