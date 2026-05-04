import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import NavBar from "@/components/NavBar";
import SpeakerSink from "@/components/SpeakerSink";
import DesktopBrowserPerception from "@/components/DesktopBrowserPerception";
import TeacherThinkingPanel from "@/components/TeacherThinkingPanel";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "beWithMe",
  description: "Personalized Reading Assistant",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="h-full flex flex-col bg-gray-50 text-gray-900">
        <NavBar />
        <main className="flex-1 flex flex-col min-h-0">{children}</main>
        <SpeakerSink />
        <DesktopBrowserPerception />
        <TeacherThinkingPanel />
      </body>
    </html>
  );
}
