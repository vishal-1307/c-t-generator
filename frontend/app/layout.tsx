import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/shell/AppShell";
import { AcademicContextProvider } from "@/lib/academicContext";
import { AuthProvider } from "@/lib/auth";
import { NoticesProvider } from "@/lib/notices";

export const metadata: Metadata = {
  title: "College Timetable Generator",
  description: "Conflict-free semester timetable generation with OR-Tools CP-SAT",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-surface-sunken text-ink antialiased">
        <NoticesProvider>
          <AuthProvider>
            <AcademicContextProvider>
              <AppShell>{children}</AppShell>
            </AcademicContextProvider>
          </AuthProvider>
        </NoticesProvider>
      </body>
    </html>
  );
}
