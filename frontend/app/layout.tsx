import type { Metadata } from "next";
import "./globals.css";
import ProviderBadge from "@/app/components/ProviderBadge";

export const metadata: Metadata = {
  title: "Vol Dashboard",
  description: "Volatility trading dashboard — IV, RV, VRP, Term Structure, Skew, Macro",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-surface">
        {/* Provider badge — fixed top-right, visible on every page */}
        <div className="fixed top-3 right-4 z-50">
          <ProviderBadge />
        </div>
        {children}
      </body>
    </html>
  );
}
