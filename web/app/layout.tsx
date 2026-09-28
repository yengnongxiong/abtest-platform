import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

// Plex Sans for the interface; Plex Serif only for findings, so a result reads like one.
// Self-hosted (Latin subsets from Google Fonts, SIL OFL, see fonts/OFL.txt): next/font/google
// downloads fonts at build time, which makes every build depend on the network, and it broke
// the dev server once when Google served font URLs that Turbopack can't parse.
const plexSans = localFont({
  src: "./fonts/ibm-plex-sans-latin.woff2", // a variable font: one file for every weight
  weight: "100 700",
  variable: "--font-plex-sans",
});
const plexSerif = localFont({
  src: [
    { path: "./fonts/ibm-plex-serif-400-latin.woff2", weight: "400" },
    { path: "./fonts/ibm-plex-serif-500-latin.woff2", weight: "500" },
  ],
  variable: "--font-plex-serif",
});

export const metadata: Metadata = {
  title: "abtest",
  description: "Feature flags and A/B tests with results you can trust.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexSerif.variable} h-full antialiased`}>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
