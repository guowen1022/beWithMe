// Route-group layout that visually opts out of the NavBar for chromeless
// surfaces (e.g. the dedicated dynamic-UI canvas). The root layout always
// wraps every page in App Router, so NavBar renders no matter what — we
// cover it with a fixed full-bleed wrapper at a higher stacking index.

export default function ChromelessLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <div
      className="fixed inset-0 bg-[#0a0a0a]"
      style={{ zIndex: 1000 }}
    >
      {children}
    </div>
  );
}
