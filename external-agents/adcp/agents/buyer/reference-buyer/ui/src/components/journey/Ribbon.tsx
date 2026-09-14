/**
 * The background ribbon.
 *
 * Retinted from the active phase's authored `glow` triplet, using the same gradient the prototype's
 * `setRibbon` builds. A null glow restores the authored default, which is the stylesheet's own
 * `.ribbon` background rather than anything computed here.
 */

export function Ribbon({ glow }: { glow: string | null }) {
  const style =
    glow === null
      ? undefined
      : {
          background:
            `radial-gradient(70% 55% at 50% 40%, rgba(${glow},.5), transparent 65%),` +
            'radial-gradient(60% 40% at 15% 18%, rgba(140,42,252,.32), transparent 60%),' +
            'radial-gradient(60% 50% at 82% 80%, rgba(0,108,36,.24), transparent 60%)',
        };
  return <div className="ribbon" data-testid="ribbon" {...(style ? { style } : {})} />;
}
