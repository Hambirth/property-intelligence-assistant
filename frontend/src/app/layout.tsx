import type { Metadata } from "next";

import "./globals.css";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: "Property Intelligence — Grounded property research",
  description: "AI-powered search across publicly available DarGlobal and Wasalt property information, with source-backed answers.",
  openGraph: {
    title: "Property Intelligence — Grounded property research",
    description: "Source-backed property intelligence across DarGlobal and Wasalt.",
    type: "website",
    images: [
      {
        url: "/og.png",
        width: 1200,
        height: 630,
        alt: "Property Intelligence — grounded property research",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Property Intelligence — Grounded property research",
    description: "Source-backed property intelligence across DarGlobal and Wasalt.",
    images: ["/og.png"],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
