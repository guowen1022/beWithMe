import type { Metadata } from "next";
import { Geist, Geist_Mono, Onest, Fraunces, JetBrains_Mono } from "next/font/google";
import SpeakerSink from "@/components/SpeakerSink";
import TeacherCaption from "@/components/TeacherCaption";
import DesktopBrowserPerception from "@/components/DesktopBrowserPerception";
import TeacherThinkingPanel from "@/components/TeacherThinkingPanel";
import MermaidLoader from "@/components/MermaidLoader";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// beWithMe block design system fonts.
const onest = Onest({
  variable: "--font-onest",
  subsets: ["latin"],
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  style: ["italic"],
  axes: ["opsz"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
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
      className={`${geistSans.variable} ${geistMono.variable} ${onest.variable} ${fraunces.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="h-full flex flex-col bg-[var(--bw-void)] text-[var(--bw-ink)]">
        <main className="flex-1 flex flex-col min-h-0">{children}</main>
        <SpeakerSink />
        <TeacherCaption />
        <DesktopBrowserPerception />
        <TeacherThinkingPanel />
        <MermaidLoader />
      </body>
    </html>
  );
}
