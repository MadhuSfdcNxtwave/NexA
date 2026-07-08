/** Animated mesh + grid background. */
export default function BackgroundGraphics({ variant = "page" }) {
  const rootClass = variant === "hero" ? "page-bg page-bg-hero" : "page-bg";

  return (
    <div className={rootClass} aria-hidden="true">
      <div className="page-bg-mesh">
        <div className="page-bg-orb page-bg-orb-1" />
        <div className="page-bg-orb page-bg-orb-2" />
        <div className="page-bg-orb page-bg-orb-3" />
      </div>
      <div className="page-bg-grid page-bg-grid-major" />
      <div className="page-bg-grid page-bg-grid-minor" />
    </div>
  );
}

export function HeroBackgroundGraphics() {
  return <BackgroundGraphics variant="hero" />;
}
