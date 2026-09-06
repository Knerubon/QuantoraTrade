import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "QuantoraTrade Command Center",
  description: "PAPER trading operations and AI agent control center",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="th">
      <body>{children}</body>
    </html>
  );
}
